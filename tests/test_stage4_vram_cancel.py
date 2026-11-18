"""Etapa 4: preflight de VRAM com evidencia do worker, cancelamento seguro ampliado,
negative prompt no no CLIP correto e registro do prompt final no historico."""

import asyncio
import json

import pytest

from backend.core.config import ComfyUISettings, GpuSettings
from backend.core.gpu import parse_process_apps, port_of_base_url
from backend.generation.base import (
    GenerateRequest,
    GenerationCancelled,
    GenerationError,
)
from backend.generation.comfyui import ComfyUIProvider
from backend.jobs.manager import GenerationJobManager
from tests.conftest import wait_terminal
from tests.test_sdxl_profiles import _FakeClient, _FakeResponse, _provider, _png_bytes


# ------------------------------------------------------------------ parse de processos GPU

def test_parse_process_apps_de_processos_da_gpu():
    output = "1234, python.exe, 8200\n999, chrome.exe, 1500\n"
    processes = parse_process_apps(output)
    assert processes == [
        {"pid": 1234, "process_name": "python.exe", "used_memory_mb": 8200},
        {"pid": 999, "process_name": "chrome.exe", "used_memory_mb": 1500},
    ]
    assert parse_process_apps("") == []
    assert parse_process_apps("n/a, x, y") == []


def test_porta_da_base_url():
    assert port_of_base_url("http://127.0.0.1:8188") == 8188
    assert port_of_base_url("http://127.0.0.1") is None
    assert port_of_base_url("http://:8188") == 8188
    assert port_of_base_url("nao-e-url") is None


def test_gpu_evidence_identifica_memoria_do_processo_do_motor(monkeypatch):
    import backend.core.gpu as gpu_module

    monkeypatch.setattr(gpu_module, "gpu_processes", lambda: [
        {"pid": 1234, "process_name": "python.exe", "used_memory_mb": 8200},
        {"pid": 999, "process_name": "chrome.exe", "used_memory_mb": 700},
    ])
    monkeypatch.setattr(gpu_module, "pid_listening_on_port", lambda port: 1234)
    evidence = gpu_module.gpu_evidence("http://127.0.0.1:8188")
    assert evidence["comfyui_pid"] == 1234
    assert evidence["comfyui_vram_mb"] == 8200  # so o motor; memoria do chrome NAO entra


def test_gpu_evidence_wddm_sem_memoria_por_processo_nao_inventa_zero(monkeypatch):
    """Drivers WDDM listam processos com memoria [N/A]: residencia nao pode ser confirmada
    (e NUNCA e reportada como 0 MB, o que ativaria estimativa falsamente a frio)."""
    import backend.core.gpu as gpu_module

    monkeypatch.setattr(gpu_module, "gpu_processes", lambda: [
        {"pid": 1234, "process_name": "python.exe", "used_memory_mb": None}])
    monkeypatch.setattr(gpu_module, "pid_listening_on_port", lambda port: 1234)
    evidence = gpu_module.gpu_evidence("http://127.0.0.1:8188")
    assert evidence["comfyui_pid"] == 1234
    assert evidence["comfyui_vram_mb"] is None
    assert "WDDM" in evidence["note"]


# ------------------------------------------------------------------ preflight do manager

class _FakePreflightProvider:
    """Replica a semantica de estimativa do ComfyUIProvider sem depender de rede."""

    name = "fake"

    def __init__(self, base_vram_mb: int = 8500):
        self.base_vram_mb = base_vram_mb
        self._model_warm = False  # simulando backend recem-reiniciado

    async def health(self) -> dict:
        return {"ok": True}

    def estimate_vram_mb(self, width, height, mode="t2i", comfyui_vram_mb=None):
        warm = self._model_warm or (
            comfyui_vram_mb is not None and comfyui_vram_mb >= self.base_vram_mb * 0.5
        )
        return int(self.base_vram_mb * 0.2) if warm else self.base_vram_mb

    def vram_basis(self, comfyui_vram_mb):
        if self._model_warm:
            return "pesos provavelmente residentes (geracao anterior desta sessao)"
        if comfyui_vram_mb is not None and comfyui_vram_mb >= self.base_vram_mb * 0.5:
            return "pesos provavelmente residentes (memoria do processo do motor)"
        if comfyui_vram_mb is None:
            return "estimativa conservadora a frio (residencia dos pesos nao confirmada)"
        return "estimativa a frio"


def _manager(provider, monkeypatch, *, total=12282, used=9282, free=3000, evidence=None):
    import backend.jobs.manager as manager_module

    monkeypatch.setattr(
        manager_module, "gpu_snapshot",
        lambda: [{"name": "RTX Test", "memory_total_mb": total, "memory_used_mb": used,
                  "memory_free_mb": free, "utilization_gpu_percent": 0, "temperature_c": 40}],
    )
    monkeypatch.setattr(manager_module, "gpu_evidence", lambda base_url: evidence or {
        "processes": [], "comfyui_pid": None, "comfyui_vram_mb": None, "note": "nao identificado",
    })
    return GenerationJobManager(
        repo=None, provider=provider, uploads=None,
        gpu_cfg=GpuSettings(enabled=True, min_free_vram_mb=1024),
    )


def test_preflight_vram_de_outro_app_nao_e_evidencia_de_pesos(monkeypatch):
    """Outro aplicativo ocupando VRAM: estimativa segue a frio e bloqueia quando a folga e curta."""
    provider = _FakePreflightProvider()
    manager = _manager(provider, monkeypatch, evidence={
        "processes": [{"pid": 999, "process_name": "chrome.exe", "used_memory_mb": 7000}],
        "comfyui_pid": None, "comfyui_vram_mb": None, "note": "motor nao identificado",
    })
    with pytest.raises(Exception) as excinfo:
        asyncio.run(manager._preflight(lambda stage, pct: None, width=1024, height=1024, mode="t2i"))
    message = str(excinfo.value)
    assert "3000 MB livres" in message          # diagnostico separado de total/livre/usada
    assert "9282 MB em uso no total" in message
    assert "NAO medicao" in message             # rotulo explicito de estimativa
    assert "nao identificado" in message        # residencia dos pesos nao confirmada


def test_preflight_modelo_residente_sobrevive_a_reinicio_do_backend(monkeypatch):
    """Backend reiniciado (sem _model_warm): a memoria do PROCESSO do motor sustenta a estimativa incremental."""
    provider = _FakePreflightProvider()
    manager = _manager(provider, monkeypatch, evidence={
        "processes": [{"pid": 1234, "process_name": "python.exe", "used_memory_mb": 8200},
                      {"pid": 999, "process_name": "chrome.exe", "used_memory_mb": 700}],
        "comfyui_pid": 1234, "comfyui_vram_mb": 8200, "note": "motor (pid 1234)",
    })
    stages: list[str] = []
    asyncio.run(manager._preflight(lambda stage, pct: stages.append(stage), width=1024, height=1024, mode="t2i"))
    assert any("vram ok" in stage for stage in stages)
    assert any("processo do motor" in stage for stage in stages)


def test_preflight_estimativa_e_explicita_no_progresso(monkeypatch):
    provider = _FakePreflightProvider()
    manager = _manager(provider, monkeypatch, free=11000)
    stages: list[str] = []
    asyncio.run(manager._preflight(lambda stage, pct: stages.append(stage), width=1024, height=1024, mode="t2i"))
    assert any("nao medicao" in stage for stage in stages)


# ------------------------------------------------------------------ cancelamento: awaiting completo

def test_cancelamento_em_execucao_nao_mostra_cancelado_enquanto_executa(tmp_path):
    """API individual disponivel: o cancelamento e despachado por job e o status so aparece
    quando o prompt proprio atinge estado terminal (resultado concluido e descartado)."""
    provider = _provider(tmp_path)
    history_calls = {"count": 0}

    def history_handler(kwargs):
        history_calls["count"] += 1
        if history_calls["count"] < 3:  # ainda executando (sem historico)
            return _FakeResponse(200, {})
        return _FakeResponse(200, {"pid1": {
            "status": {"status_str": "success", "completed": True,
                       "messages": [["execution_success", {}]]},
            "outputs": {"9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}},
        }})

    fake = _FakeClient({
        ("GET", "/queue"): {"queue_running": [["1", "pid1", {}]], "queue_pending": []},
        ("POST", "/api/jobs/"): _FakeResponse(200, {"cancelled": True}),
        ("GET", "/api/jobs/"): _FakeResponse(200, {"id": "pid1", "status": "in_progress"}),
        ("GET", "/history/"): history_handler,
    })
    provider._client = fake
    provider._prompt_ids["job1"] = "pid1"
    stages: list[str] = []
    with pytest.raises(GenerationCancelled, match="descartado"):
        asyncio.run(provider._wait_outputs("pid1", "job1", lambda stage, pct: stages.append(stage), lambda: True))
    assert any("cancelando" in stage for stage in stages)
    posts = [path for method, path, _ in fake.calls if method == "POST"]
    assert any("/api/jobs/pid1/cancel" in path for path in posts)  # cancelamento INDIVIDUAL
    assert not any("/interrupt" in path for path in posts)
    assert not any("/queue" in path for path in posts)


def test_cancelamento_em_execucao_nunca_vira_cancelamento_prematuro(tmp_path):
    """Enquanto o prompt proprio segue em execucao (estado in_progress na API de jobs) e
    sem terminal confirmado, o job NAO termina 'cancelado': o provider continua o poll."""
    provider = _provider(tmp_path)
    provider.cfg.timeout_seconds = 0.15
    fake = _FakeClient({
        ("GET", "/queue"): {"queue_running": [["1", "pid1", {}]], "queue_pending": []},
        ("POST", "/api/jobs/"): _FakeResponse(200, {"cancelled": True}),
        ("GET", "/api/jobs/"): _FakeResponse(200, {"id": "pid1", "status": "in_progress"}),
        ("GET", "/history/"): _FakeResponse(200, {}),
    })
    provider._client = fake
    provider._prompt_ids["job1"] = "pid1"
    with pytest.raises(GenerationError, match="Tempo limite"):
        asyncio.run(provider._wait_outputs("pid1", "job1", lambda stage, pct: None, lambda: True))


def test_execution_error_de_oom_reseta_sessao_e_orienta(tmp_path):
    provider = _provider(tmp_path)
    provider._model_warm = True
    status = {"status_str": "error", "completed": False,
              "messages": [["execution_error", {"node_type": "KSampler",
                                                "exception_message": "CUDA out of memory. Tried to allocate 2.00 GiB"}]]}
    message = provider._extract_error(status)
    assert "VRAM insuficiente" in message
    assert "Nenhum processo externo" in message
    assert provider._model_warm is False  # estimativa volta a frio na proxima geracao


def test_erro_comum_nao_e_confundido_com_oom(tmp_path):
    provider = _provider(tmp_path)
    provider._model_warm = True
    status = {"status_str": "error", "completed": False,
              "messages": [["execution_error", {"node_type": "CheckpointLoaderSimple",
                                                "exception_message": "checkpoint not found"}]]}
    message = provider._extract_error(status)
    assert "checkpoint not found" in message
    assert provider._model_warm is True  # sem OOM: estado da sessao preservado


# ------------------------------------------------------------------ identificacao de jobs

def test_submissao_nao_fabrica_prompt_id_e_usa_o_retornado(tmp_path):
    """Etapa 8: o ComfyUI 0.45+ so aceita prompt_id em UUID canonico (ids fabricados sao
    rejeitados com 400) — o ImageCore NAO envia prompt_id proprio e rastreia EXCLUSIVAMENTE
    o id real devolvido pelo servidor; a identificacao vai no client_id."""
    provider = _provider(tmp_path)
    captured: dict = {}

    def handle_prompt(kwargs):
        captured.update(kwargs.get("json", {}))
        return _FakeResponse(200, {"prompt_id": "uuid-real-do-servidor"})

    fake = _FakeClient({("POST", "/prompt"): handle_prompt})
    provider._client = fake
    prompt_id = asyncio.run(provider._submit({"6": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}}}, "job-abc"))
    assert prompt_id == "uuid-real-do-servidor"
    assert captured["client_id"] == "job-abc"
    assert "prompt_id" not in captured  # id fabricado nunca e enviado


def test_submissao_recusada_nao_reenvia_com_id_fabricado(tmp_path):
    provider = _provider(tmp_path)
    bodies: list[dict] = []

    def handle_prompt(kwargs):
        bodies.append(kwargs.get("json", {}))
        return _FakeResponse(400, {"error": {"type": "invalid_prompt_id", "message": "prompt_id must be a valid UUID"}})

    fake = _FakeClient({("POST", "/prompt"): handle_prompt})
    provider._client = fake
    with pytest.raises(GenerationError, match="recusou o workflow"):
        asyncio.run(provider._submit({"6": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}}}, "job-abc"))
    assert len(bodies) == 1  # um unico envio: sem fallback e sem id fabricado


# ------------------------------------------------------------------ negative prompt

def test_negative_prompt_vai_para_o_no_clip_negativo(tmp_path):
    provider = _provider(tmp_path)
    workflow_posted: dict = {}

    def handle_prompt(kwargs):
        workflow_posted.update(kwargs.get("json", {}).get("prompt", {}))
        return _FakeResponse(200, {"prompt_id": "pid1"})

    fake = _FakeClient({
        ("POST", "/prompt"): handle_prompt,
        ("GET", "/queue"): {"queue_running": [["1", "pid1", {}]], "queue_pending": []},
        ("GET", "/history/"): _FakeResponse(200, {"pid1": {
            "status": {"status_str": "success", "completed": True, "messages": []},
            "outputs": {"9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}},
        }}),
        ("GET", "/view"): _FakeResponse(200, content=_png_bytes()),
    })
    provider._client = fake
    request = GenerateRequest(job_id="job1", prompt="a red fox in the snow", width=256, height=256, seed=7,
                              negative_prompt="birds, lowres")
    asyncio.run(provider.generate(request, progress=lambda stage, pct: None, cancel=lambda: False))
    assert workflow_posted["6"]["inputs"]["text"] == "a red fox in the snow"
    assert workflow_posted["7"]["inputs"]["text"] == "birds, lowres"  # no CLIP NEGATIVO (7), nao no 6


def test_negative_prompt_padrao_quando_nao_informado(tmp_path):
    provider = _provider(tmp_path)
    assert provider._negative_for(GenerateRequest(job_id="j", prompt="x", width=64, height=64, seed=1)) \
        == provider.cfg.default_negative_prompt
    # opt-out explicito: string vazia envia texto vazio
    assert provider._negative_for(GenerateRequest(job_id="j", prompt="x", width=64, height=64, seed=1,
                                                  negative_prompt="")) == ""


def test_workflow_sem_no_negativo_nao_e_tocado(tmp_path):
    cfg_workflow = tmp_path / "t2i.json"
    cfg_workflow.write_text(json.dumps({
        "3": {"class_type": "KSampler", "inputs": {"seed": 0}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
    }), encoding="utf-8")
    cfg = ComfyUISettings(
        workflow=cfg_workflow, workflow_i2i=None, timeout_seconds=2.0, poll_interval_seconds=0.01,
        node_map={"prompt": ["6", "inputs", "text"], "width": ["5", "inputs", "width"],
                  "height": ["5", "inputs", "height"], "seed": ["3", "inputs", "seed"]},
    )
    provider = ComfyUIProvider(cfg)
    # node_map sem negative_prompt: nenhum texto de negative e definido (no permanece como esta)
    assert provider._negative_for(GenerateRequest(job_id="j", prompt="x", width=64, height=64, seed=1)) is None
    caps = provider.capabilities()
    assert caps["negative_prompt_supported"] is False


# ------------------------------------------------------------------ API: registro no historico

def test_api_registra_negative_e_prompt_final_no_historico(settings, monkeypatch):
    from backend.generation.registry import PROVIDERS
    from backend.main import create_app as _create
    from fastapi.testclient import TestClient

    class NegativeStub(PROVIDERS["simulated"]):
        def capabilities(self):
            caps = super().capabilities()
            caps["negative_prompt_supported"] = True
            return caps

    monkeypatch.setitem(PROVIDERS, "simulated", NegativeStub)
    with TestClient(_create(settings)) as client:
        created = client.post("/api/generations", json={
            "module": "image", "prompt": "uma raposa na neve", "ratio": "1:1", "tier": "720p",
            "negative_prompt": "birds, lowres",
        })
        assert created.status_code == 201
        gen = created.json()["generation"]
        assert gen["params"]["negative_prompt"] == "birds, lowres"
        assert gen["params"]["final_negative_prompt"] == "birds, lowres"
        assert gen["params"]["final_prompt"] == "uma raposa na neve"  # estilo none: sem sufixo

        created2 = client.post("/api/generations", json={
            "module": "image", "prompt": "a red fox in the snow", "style": "photoreal",
            "ratio": "1:1", "tier": "720p",
        })
        gen2 = created2.json()["generation"]
        assert "text, watermark" in gen2["params"]["final_negative_prompt"]  # padrao do servidor
        assert gen2["params"]["final_prompt"].endswith(
            ", photorealistic, natural lighting, sharp focus, high detail")
        assert gen2["params"]["style_label"] == "Fotorrealista"  # rotulo exibido continua em pt-BR
        wait_terminal(client, gen["id"])


def test_api_nao_registra_negative_quando_motor_nao_suporta(client):
    created = client.post("/api/generations", json={
        "module": "image", "prompt": "sem suporte", "ratio": "1:1", "tier": "720p",
        "negative_prompt": "birds",
    })
    gen = created.json()["generation"]
    assert "negative_prompt" not in gen["params"]
    assert "final_negative_prompt" not in gen["params"]
