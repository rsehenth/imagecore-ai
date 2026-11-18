"""Etapa 8: cancelamento individual (POST /api/jobs/{prompt_id}/cancel), pre-flight de VRAM
com evidencia do historico do proprio ComfyUI (sobrevive a reinicios do backend; WDDM nao
informa memoria por processo) e POST /free somente de maneira controlada."""

import asyncio
import json

import httpx
import pytest

from backend.core.config import ComfyUISettings, GpuSettings
from backend.generation.base import (
    GenerationCancelled,
    GenerationError,
    VramInsufficientError,
)
from backend.generation.comfyui import ComfyUIProvider
from backend.jobs.manager import GenerationJobManager
from tests.test_sdxl_profiles import _FakeClient, _FakeResponse, _provider


# ------------------------------------------------------------------ cancelamento individual

def _comfy_provider(tmp_path) -> ComfyUIProvider:
    provider = _provider(tmp_path)
    provider.cfg.timeout_seconds = 2.0
    provider.cfg.poll_interval_seconds = 0.01
    return provider


def test_cancelamento_de_trabalho_proprio_pendente_via_api_individual(tmp_path):
    """Job proprio pendente: POST /api/jobs/{prompt_id}/cancel -> confirmacao de terminal
    (404: removido da fila antes de executar) -> cancelamento apresentado como concluido."""
    provider = _comfy_provider(tmp_path)
    fake = _FakeClient({
        ("GET", "/queue"): {"queue_running": [["1", "prompt-externo", {}]],
                            "queue_pending": [["2", "meu-prompt", {}]]},
        ("POST", "/api/jobs/"): _FakeResponse(200, {"cancelled": True}),
        ("GET", "/api/jobs/"): _FakeResponse(404, {"error": "Job not found"}),
        ("GET", "/history/"): _FakeResponse(200, {}),
    })
    provider._client = fake
    provider._prompt_ids["job1"] = "meu-prompt"
    with pytest.raises(GenerationCancelled, match="estado terminal"):
        asyncio.run(provider._wait_outputs("meu-prompt", "job1", lambda stage, pct: None, lambda: True))
    posts = [path for method, path, _ in fake.calls if method == "POST"]
    assert any("/api/jobs/meu-prompt/cancel" in path for path in posts)
    assert not any("/interrupt" in path for path in posts)
    assert not any("/queue" in path for path in posts)  # sem fallback: a API individual basta


def test_cancelamento_nunca_afeta_trabalho_externo(tmp_path):
    """Job externo em execucao + nosso pendente: o cancelamento do NOSSO job usa somente o
    prompt_id proprio (URL e corpo); o prompt externo nunca aparece em nenhuma acao."""
    provider = _comfy_provider(tmp_path)
    fake = _FakeClient({
        ("GET", "/queue"): {"queue_running": [["1", "prompt-externo", {}]],
                            "queue_pending": [["2", "meu-prompt", {}]]},
        ("POST", "/api/jobs/"): _FakeResponse(200, {"cancelled": True}),
        ("GET", "/api/jobs/"): _FakeResponse(404, {"error": "Job not found"}),
        ("GET", "/history/"): _FakeResponse(200, {}),
    })
    provider._client = fake
    provider._prompt_ids["job1"] = "meu-prompt"
    asyncio.run(provider.cancel_current("job1"))
    for method, path, kwargs in fake.calls:
        serialized = f"{path} {json.dumps(kwargs, default=str)}"
        assert "prompt-externo" not in serialized  # trabalho externo intocado em toda chamada


def test_corrida_conclusao_e_cancelamento_resultado_e_descartado(tmp_path):
    """O prompt conclui na corrida com o cancelamento: a API individual responde
    {"cancelled": false} (terminal) -> cancelamento confirmado, resultado descartado."""
    provider = _comfy_provider(tmp_path)
    fake = _FakeClient({
        ("POST", "/api/jobs/"): _FakeResponse(200, {"cancelled": False}),
    })
    provider._client = fake
    provider._prompt_ids["job1"] = "meu-prompt"
    with pytest.raises(GenerationCancelled):
        asyncio.run(provider._wait_outputs("meu-prompt", "job1", lambda stage, pct: None, lambda: True))


def test_corrida_cancelamento_despachado_e_conclusao_descarta_resultado(tmp_path):
    """Cancelamento despachado, mas o prompt conclui antes da interrupcao: o estado terminal
    e confirmado e o resultado concluido e descartado (nunca entregue como sucesso)."""
    provider = _comfy_provider(tmp_path)
    fake = _FakeClient({
        ("GET", "/queue"): {"queue_running": [["1", "meu-prompt", {}]], "queue_pending": []},
        ("POST", "/api/jobs/"): _FakeResponse(200, {"cancelled": True}),
        ("GET", "/api/jobs/"): _FakeResponse(200, {"id": "meu-prompt", "status": "completed"}),
        ("GET", "/history/"): _FakeResponse(200, {"meu-prompt": {
            "status": {"status_str": "success", "completed": True,
                       "messages": [["execution_success", {}]]},
            "outputs": {"9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}},
        }}),
    })
    provider._client = fake
    provider._prompt_ids["job1"] = "meu-prompt"
    with pytest.raises(GenerationCancelled, match="descartado"):
        asyncio.run(provider._wait_outputs("meu-prompt", "job1", lambda stage, pct: None, lambda: True))


def test_cancelamento_confirmado_pelo_estado_cancelado_da_api_de_jobs(tmp_path):
    """Job interrompido no ComfyUI aparece como status 'cancelled' na API de jobs: o
    cancelamento e apresentado como concluido mesmo sem entrada no historico."""
    provider = _comfy_provider(tmp_path)
    fake = _FakeClient({
        ("GET", "/queue"): {"queue_running": [], "queue_pending": []},
        ("POST", "/api/jobs/"): _FakeResponse(200, {"cancelled": True}),
        ("GET", "/api/jobs/"): _FakeResponse(200, {"id": "meu-prompt", "status": "cancelled"}),
        ("GET", "/history/"): _FakeResponse(200, {}),
    })
    provider._client = fake
    provider._prompt_ids["job1"] = "meu-prompt"
    with pytest.raises(GenerationCancelled, match="estado terminal"):
        asyncio.run(provider._wait_outputs("meu-prompt", "job1", lambda stage, pct: None, lambda: True))


class _DownClient:
    """Motor inacessivel: toda chamada falha no transporte — o estado nunca fica determinado."""

    async def get(self, path, **kwargs):
        raise httpx.ConnectError("boom")

    async def post(self, path, **kwargs):
        raise httpx.ConnectError("boom")

    async def aclose(self):
        pass


def test_cancelamento_inconclusivo_nao_confirma_cancelamento(tmp_path):
    """Motor inacessivel no momento do cancelamento: o estado terminal NAO foi determinado —
    o job NAO vira 'cancelado' (o poll continua ate o terminal real ou o timeout)."""
    provider = _comfy_provider(tmp_path)
    provider._client = _DownClient()
    provider._prompt_ids["job1"] = "meu-prompt"
    assert asyncio.run(provider.cancel_current("job1")) == "inconclusive"
    with pytest.raises(GenerationError, match="Tempo limite"):
        asyncio.run(provider._wait_outputs("meu-prompt", "job1", lambda stage, pct: None, lambda: True))


def test_removido_da_fila_legado_confirma_so_apos_terminal(tmp_path):
    """API legada: o delete do pendente NAO confirma o cancelamento por si so (corrida com
    execucao). 'Cancelado' so apos a fila (lida com exito) nao ter o prompt e o historico
    nao ter entrada dele (nunca executou)."""
    provider = _comfy_provider(tmp_path)
    queue_calls = {"count": 0}

    def queue_handler(kwargs):
        queue_calls["count"] += 1
        if queue_calls["count"] == 1:  # leitura do cancel_current: nosso job pendente
            return {"queue_running": [["1", "prompt-externo", {}]],
                    "queue_pending": [["2", "meu-prompt", {}]]}
        return {"queue_running": [], "queue_pending": []}  # apos o delete: fora da fila

    fake = _FakeClient({
        ("POST", "/api/jobs/"): _FakeResponse(404, {"error": "not found"}),  # sem API individual
        ("GET", "/queue"): queue_handler,
        ("POST", "/queue"): _FakeResponse(200, {}),
        ("GET", "/history/"): _FakeResponse(200, {}),  # nunca executou: sem entrada
    })
    provider._client = fake
    provider._prompt_ids["job1"] = "meu-prompt"
    with pytest.raises(GenerationCancelled, match="removido da fila antes de executar"):
        asyncio.run(provider._wait_outputs("meu-prompt", "job1", lambda stage, pct: None, lambda: True))
    deletes = [kwargs.get("json") for method, path, kwargs in fake.calls
               if method == "POST" and "/queue" in path]
    assert {"delete": ["meu-prompt"]} in deletes  # so o NOSSO prompt foi removido


# ------------------------------------------------------------------ VRAM: evidencia do historico

def _provider_with_model(tmp_path, model="sd_xl_base_1.0.safetensors") -> ComfyUIProvider:
    provider = _provider(tmp_path)
    provider.cfg.estimated_vram_mb = 8500  # alinha com o settings.yaml usado nos outros testes
    workflow = tmp_path / "t2i.json"
    data = json.loads(workflow.read_text(encoding="utf-8"))
    data["4"] = {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model}}
    workflow.write_text(json.dumps(data), encoding="utf-8")
    return provider


def _history_entry(model: str, status_str: str, messages: list) -> dict:
    return {
        "prompt": [1, "pid", {"4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model}}},
                   {}, None],
        "outputs": {},
        "status": {"status_str": status_str, "completed": status_str == "success", "messages": messages},
    }


def test_historico_com_sucesso_do_modelo_confere_residencia_apos_reiniciar_backend(tmp_path):
    """Backend reiniciado (sem sessao) + WDDM sem memoria por processo: o historico do
    PROPRIO ComfyUI (ultima execucao com carga de modelo = nosso modelo, sucesso) sustenta
    a estimativa incremental — nova geracao permitida com pesos residentes."""
    provider = _provider_with_model(tmp_path)
    fake = _FakeClient({
        ("GET", "/history"): _FakeResponse(200, {
            "antigo": _history_entry("outro-modelo.safetensors", "success", [["execution_success", {}]]),
            "recente": _history_entry("sd_xl_base_1.0.safetensors", "success", [["execution_success", {}]]),
        }),
    })
    provider._client = fake
    evidence = asyncio.run(provider.warm_evidence(comfyui_vram_mb=None))
    assert evidence["warm"] is True
    assert "historico" in evidence.get("note", "") + evidence.get("basis", "")
    # custo incremental, nao a carga completa
    assert provider.estimate_vram_mb(1024, 1024, "t2i", warm=True) == int(8500 * 0.2)


def test_historico_com_erro_depois_do_sucesso_nao_confere_residencia(tmp_path):
    """Erro na ultima execucao com carga de modelo (o ComfyUI descarrega TODOS os modelos
    apos OOM): residencia nao confirmada -> estimativa a frio."""
    provider = _provider_with_model(tmp_path)
    fake = _FakeClient({
        ("GET", "/history"): _FakeResponse(200, {
            "antigo": _history_entry("sd_xl_base_1.0.safetensors", "success", [["execution_success", {}]]),
            "recente": _history_entry("sd_xl_base_1.0.safetensors", "error",
                                      [["execution_error", {"exception_message": "CUDA out of memory"}]]),
        }),
    })
    provider._client = fake
    evidence = asyncio.run(provider.warm_evidence(comfyui_vram_mb=None))
    assert evidence["warm"] is False


def test_historico_com_outro_modelo_depois_do_novo_nao_confere_residencia(tmp_path):
    provider = _provider_with_model(tmp_path)
    fake = _FakeClient({
        ("GET", "/history"): _FakeResponse(200, {
            "antigo": _history_entry("sd_xl_base_1.0.safetensors", "success", [["execution_success", {}]]),
            "recente": _history_entry("outro-modelo.safetensors", "success", [["execution_success", {}]]),
        }),
    })
    provider._client = fake
    evidence = asyncio.run(provider.warm_evidence(comfyui_vram_mb=None))
    assert evidence["warm"] is False


def test_historico_com_interrupcao_do_modelo_mantem_residencia(tmp_path):
    """Interrupcao NAO descarrega modelos no ComfyUI 0.45: a residencia permanece."""
    provider = _provider_with_model(tmp_path)
    fake = _FakeClient({
        ("GET", "/history"): _FakeResponse(200, {
            "unico": _history_entry("sd_xl_base_1.0.safetensors", "error",
                                    [["execution_interrupted", {}]]),
        }),
    })
    provider._client = fake
    evidence = asyncio.run(provider.warm_evidence(comfyui_vram_mb=None))
    assert evidence["warm"] is True


def test_historico_indisponivel_fica_conservador(tmp_path):
    """Historico com status 500 (indisponivel): conservador, sem excecao e sem inventar."""
    provider = _provider_with_model(tmp_path)
    fake = _FakeClient({("GET", "/history"): _FakeResponse(500, {})})
    provider._client = fake
    evidence = asyncio.run(provider.warm_evidence(comfyui_vram_mb=None))
    assert evidence["warm"] is None
    assert provider.estimate_vram_mb(1024, 1024, "t2i", warm=None, comfyui_vram_mb=None) == 8500


def test_sessao_do_backend_tem_precedencia_sobre_o_historico(tmp_path):
    provider = _provider_with_model(tmp_path)
    # historico sem execucoes com carga de modelo: nada contradiz a sessao
    provider._client = _FakeClient({("GET", "/history"): _FakeResponse(200, {})})
    provider._model_warm = True
    evidence = asyncio.run(provider.warm_evidence(comfyui_vram_mb=None))
    assert evidence["warm"] is True
    assert evidence["residency"] == "provavel"
    assert "sessao" in evidence["basis"]


def test_historico_com_erro_desmente_sessao_e_processo(tmp_path):
    """Recuperacao controlada apos OOM (inclusive de OUTRO cliente, que descarrega TODOS os
    modelos): o historico mais recente (erro) vence a sessao e a leitura 'cheia' do processo."""
    provider = _provider_with_model(tmp_path)
    fake = _FakeClient({
        ("GET", "/history"): _FakeResponse(200, {
            "unico": _history_entry("sd_xl_base_1.0.safetensors", "error",
                                    [["execution_error", {"exception_message": "CUDA out of memory"}]]),
        }),
    })
    provider._client = fake
    provider._model_warm = True
    evidence = asyncio.run(provider.warm_evidence(comfyui_vram_mb=8000))
    assert evidence["warm"] is False
    assert evidence["residency"] == "desconhecida"
    assert provider._model_warm is False  # sessao corrigida para a proxima geracao


def test_warm_evidence_classifica_residencia_desconhecida(tmp_path):
    """Historico indisponivel e sem evidencia do processo: residencia DESCONHECIDA (a frio),
    nunca apresentada como confirmada ou provavel."""
    provider = _provider_with_model(tmp_path)
    fake = _FakeClient({("GET", "/history"): _FakeResponse(500, {})})
    provider._client = fake
    evidence = asyncio.run(provider.warm_evidence(comfyui_vram_mb=None))
    assert evidence["warm"] is None
    assert evidence["residency"] == "desconhecida"


# ------------------------------------------------------------------ VRAM livre pelo proprio motor

def test_memoria_livre_reportada_pelo_proprio_motor(tmp_path):
    provider = _provider(tmp_path)
    fake = _FakeClient({
        ("GET", "/system_stats"): _FakeResponse(200, {
            "system": {"os": "nt"},
            "devices": [{"name": "cuda:0", "vram_total": 12575248384, "vram_free": 3867148288}],
        }),
    })
    provider._client = fake
    assert asyncio.run(provider.device_free_vram_mb()) == 3867148288


def test_memoria_livre_indisponivel_retorna_none_sem_inventar(tmp_path):
    provider = _provider(tmp_path)
    fake = _FakeClient({("GET", "/system_stats"): _FakeResponse(200, {"devices": []})})
    provider._client = fake
    assert asyncio.run(provider.device_free_vram_mb()) is None


# ------------------------------------------------------------------ pre-flight do manager (Etapa 8)

class _WarmEvidenceProvider:
    """Provider falso com o contrato novo (warm_evidence + device_free_vram_mb)."""

    name = "fake"

    def __init__(self, warm, basis="evidencia de teste", worker_free=None, base_vram_mb=8500):
        self.base_vram_mb = base_vram_mb
        self.warm = warm
        self.basis = basis
        self.worker_free = worker_free
        self._model_warm = False  # backend recem-reiniciado
        self.received_warm = "nunca-passo"

    async def health(self):
        return {"ok": True}

    def estimate_vram_mb(self, width, height, mode="t2i", comfyui_vram_mb=None, warm=None):
        self.received_warm = warm
        return int(self.base_vram_mb * 0.2) if warm else self.base_vram_mb

    def vram_basis(self, comfyui_vram_mb):
        return "estimativa conservadora a frio (residencia dos pesos nao confirmada)"

    async def warm_evidence(self, comfyui_vram_mb=None):
        return {"warm": self.warm, "basis": self.basis}

    async def device_free_vram_mb(self):
        return self.worker_free


def _manager(provider, monkeypatch, *, total=12282, used=9282, free=3000):
    import backend.jobs.manager as manager_module

    monkeypatch.setattr(
        manager_module, "gpu_snapshot",
        lambda: [{"name": "RTX Test", "memory_total_mb": total, "memory_used_mb": used,
                  "memory_free_mb": free, "utilization_gpu_percent": 0, "temperature_c": 40}],
    )
    monkeypatch.setattr(manager_module, "gpu_evidence", lambda base_url: {
        "processes": None, "comfyui_pid": None, "comfyui_vram_mb": None,
        "note": "driver nao informou processos de GPU (WDDM)",
    })
    return GenerationJobManager(
        repo=None, provider=provider, uploads=None,
        gpu_cfg=GpuSettings(enabled=True, min_free_vram_mb=1024),
    )


def test_preflight_historico_confere_residencia_e_permite_geracao(monkeypatch):
    """Cenario real do bug: backend reiniciado com o ComfyUI ativo, WDDM sem memoria por
    processo. O historico do proprio motor confere a residencia -> custo incremental ->
    geracao permitida (antes: recusada com a GPU 'cheia' de pesos ja carregados)."""
    provider = _WarmEvidenceProvider(
        warm=True,
        basis="pesos provavelmente residentes (historico do proprio ComfyUI)",
        worker_free=3000,
    )
    manager = _manager(provider, monkeypatch)
    stages: list[str] = []
    asyncio.run(manager._preflight(lambda stage, pct: stages.append(stage), width=1024, height=1024, mode="t2i"))
    assert provider.received_warm is True
    assert any("vram ok" in stage and "historico do proprio ComfyUI" in stage for stage in stages)


def test_preflight_sem_residencia_confirmada_recusa_a_frio(monkeypatch):
    provider = _WarmEvidenceProvider(warm=False, basis="residencia nao confirmada", worker_free=3000)
    manager = _manager(provider, monkeypatch)
    with pytest.raises(VramInsufficientError) as excinfo:
        asyncio.run(manager._preflight(lambda stage, pct: None, width=1024, height=1024, mode="t2i"))
    assert provider.received_warm is False
    assert "8500 MB" in str(excinfo.value)  # custo completo a frio
    assert "residencia nao confirmada" in str(excinfo.value)


def test_preflight_fonte_da_memoria_livre_aparece_no_diagnostico(monkeypatch):
    """Com relatorio do proprio motor (/system_stats) a fonte e identificada; sem ele, o
    snapshot do driver (nvidia-smi) entra no diagnostico no formato detalhado."""
    provider = _WarmEvidenceProvider(warm=True, basis="residencia", worker_free=2000)
    manager = _manager(provider, monkeypatch)
    with pytest.raises(VramInsufficientError) as excinfo:
        asyncio.run(manager._preflight(lambda stage, pct: None, width=1024, height=1024, mode="t2i"))
    assert "2000 MB livres (relatorio do proprio motor)" in str(excinfo.value)

    provider2 = _WarmEvidenceProvider(warm=False, basis="residencia nao confirmada", worker_free=None)
    manager2 = _manager(provider2, monkeypatch, free=3000)
    with pytest.raises(VramInsufficientError) as excinfo2:
        asyncio.run(manager2._preflight(lambda stage, pct: None, width=1024, height=1024, mode="t2i"))
    assert "3000 MB livres de 12282 MB na RTX Test" in str(excinfo2.value)
    assert "9282 MB em uso no total" in str(excinfo2.value)


def test_preflight_sem_warm_evidence_mantem_contrato_antigo(monkeypatch):
    """Providers sem o contrato novo (ex.: fakes antigos/simulado) seguem funcionando:
    sem warm_evidence o manager nao passa o kwarg `warm`."""
    from backend.core.config import GpuSettings as _G
    import backend.jobs.manager as manager_module

    class _LegacyProvider:
        name = "fake"

        async def health(self):
            return {"ok": True}

        def estimate_vram_mb(self, width, height, mode="t2i", comfyui_vram_mb=None):
            return 1

    monkeypatch.setattr(manager_module, "gpu_snapshot", lambda: None)
    monkeypatch.setattr(manager_module, "gpu_evidence", lambda base_url: {
        "processes": None, "comfyui_pid": None, "comfyui_vram_mb": None, "note": "",
    })
    manager = GenerationJobManager(repo=None, provider=_LegacyProvider(), uploads=None,
                                   gpu_cfg=_G(enabled=True, min_free_vram_mb=1024))
    stages: list[str] = []
    asyncio.run(manager._preflight(lambda stage, pct: stages.append(stage), width=64, height=64, mode="t2i"))


# ------------------------------------------------------------------ POST /free controlado

def test_free_sem_confirmacao_e_recusado(tmp_path):
    provider = _provider(tmp_path)
    fake = _FakeClient({})
    provider._client = fake
    with pytest.raises(GenerationError, match="confirmacao"):
        asyncio.run(provider.controlled_free(confirm=False))
    assert fake.calls == []  # nada foi enviado ao ComfyUI


def test_free_sem_instancia_dedicada_e_indisponivel(tmp_path):
    """Etapa 8.1: sem a declaracao de instancia DEDICADA ao ImageCore AI, /free fica
    indisponivel mesmo com confirm=true — fila vazia nao prova exclusividade e pesos de
    outros clientes nao podem ser descarregados. Explicacao objetiva, nenhuma chamada."""
    provider = _provider(tmp_path)
    fake = _FakeClient({})
    provider._client = fake
    with pytest.raises(GenerationError, match="dedicada"):
        asyncio.run(provider.controlled_free(confirm=True))
    assert fake.calls == []


def test_free_com_fila_ilegivel_e_recusado(tmp_path):
    """Fail closed: fila que nao pode ser verificada (HTTP 500) NAO e fila vazia — /free
    nao e enviado."""
    provider = _provider(tmp_path)
    provider.cfg.dedicated = True
    fake = _FakeClient({
        ("GET", "/queue"): _FakeResponse(500, {}),
        ("POST", "/free"): _FakeResponse(200, {}),
    })
    provider._client = fake
    with pytest.raises(GenerationError, match="verificada"):
        asyncio.run(provider.controlled_free(confirm=True))
    assert not [call for call in fake.calls if call[0] == "POST" and "/free" in call[1]]


def test_free_com_fila_ocupada_e_recusado_sem_tocar_no_motor(tmp_path):
    """Fila com trabalhos (inclusive externos): /free NAO e enviado — nenhum trabalho externo
    pode ser afetado."""
    provider = _provider(tmp_path)
    provider.cfg.dedicated = True
    fake = _FakeClient({
        ("GET", "/queue"): {"queue_running": [["1", "prompt-externo", {}]], "queue_pending": [["2", "outro", {}]]},
        ("POST", "/free"): _FakeResponse(200, {}),
    })
    provider._client = fake
    provider._model_warm = True
    with pytest.raises(GenerationError, match="externos"):
        asyncio.run(provider.controlled_free(confirm=True))
    assert not [call for call in fake.calls if call[0] == "POST" and "/free" in call[1]]
    assert provider._model_warm is True  # estado da sessao preservado (nada aconteceu)


def test_free_controlado_com_fila_vazia_e_confirmacao(tmp_path):
    provider = _provider(tmp_path)
    provider.cfg.dedicated = True
    fake = _FakeClient({
        ("GET", "/queue"): {"queue_running": [], "queue_pending": []},
        ("POST", "/free"): _FakeResponse(200, {}),
    })
    provider._client = fake
    provider._model_warm = True
    result = asyncio.run(provider.controlled_free(confirm=True))
    assert result["freed"] is True
    free_calls = [kwargs for method, path, kwargs in fake.calls if method == "POST" and "/free" in path]
    assert free_calls and free_calls[0]["json"] == {"unload_models": True, "free_memory": True}
    assert provider._model_warm is False  # pesos descarregados: proxima estimativa volta a frio
