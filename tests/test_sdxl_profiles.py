"""Etapa 3: perfis SDXL (buckets nativos), img2img, qualidade e cancelamento seguro."""

import asyncio
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.core.config import ComfyUISettings
from backend.core.presets import (
    I2I_PIXEL_BUDGET,
    i2i_latent_dims,
    sdxl_bucket_for,
)
from backend.generation.base import (
    GenerateRequest,
    GenerationCancelled,
    GenerationError,
)
from backend.generation.comfyui import ComfyUIProvider
from tests.conftest import wait_terminal


# ------------------------------------------------------------------ buckets nativos SDXL

def test_bucket_sdxl_proporcoes_principais():
    assert sdxl_bucket_for(1024, 1024) == (1024, 1024)
    assert sdxl_bucket_for(1920, 1080) == (1344, 768)      # 16:9
    assert sdxl_bucket_for(1080, 1920) == (768, 1344)      # 9:16
    assert sdxl_bucket_for(1080, 1350) == (896, 1152)      # 4:5 (retrato instagram)
    assert sdxl_bucket_for(2560, 1080) == (1536, 640)      # 21:9 classe
    assert sdxl_bucket_for(1280, 720) == (1344, 768)       # thumbnail 16:9


def test_bucket_sdxl_sempre_na_classe_de_1mp():
    for bucket in [(1024, 1024), (1920, 1080), (3840, 2160), (6272, 6272), (854, 480)]:
        w, h = sdxl_bucket_for(*bucket)
        assert w * h <= I2I_PIXEL_BUDGET * 1.1
        assert w % 16 == 0 and h % 16 == 0


def test_i2i_latent_dims_reescala_para_orcamento():
    # foto grande: reescala para ~1 MP em grade de 16 px preservando a proporcao
    w, h = i2i_latent_dims(4000, 3000)
    assert w * h <= I2I_PIXEL_BUDGET + 16 * 4096
    assert w % 16 == 0 and h % 16 == 0
    assert abs(w / h - 4 / 3) < 0.05
    # imagem pequena: piso util de 256 px
    assert i2i_latent_dims(64, 48) == (256, 256)


# ------------------------------------------------------------------ API: politicas e validacao

def test_t2i_sem_perfis_nativos_mantem_dimensoes_exatas(client):
    created = client.post("/api/generations", json={
        "module": "image", "prompt": "16:9 exato", "ratio": "16:9", "tier": "1080p",
    })
    gen = created.json()["generation"]
    assert (gen["width"], gen["height"]) == (1920, 1080)
    final = wait_terminal(client, gen["id"])
    assert final["status"] == "succeeded"


def test_perfis_nativos_geram_bucket_e_registram_exportacao(client, settings, monkeypatch):
    from backend.generation.registry import PROVIDERS
    from backend.main import create_app as _create

    class NativeStub(PROVIDERS["simulated"]):
        def capabilities(self):
            caps = super().capabilities()
            caps["native_profiles"] = True
            return caps

    monkeypatch.setitem(PROVIDERS, "simulated", NativeStub)
    with TestClient(_create(settings)) as native_client:
        created = native_client.post("/api/generations", json={
            "module": "image", "prompt": "wallpaper 4k", "ratio": "16:9", "tier": "4k",
        })
        assert created.status_code == 201
        gen = created.json()["generation"]
        # geracao no bucket nativo; dimensoes finais ficam registradas como exportacao
        assert (gen["width"], gen["height"]) == (1344, 768)
        assert (gen["export_width"], gen["export_height"]) == (3840, 2160)
        assert gen["params"]["requested"] == "3840x2160"
        final = wait_terminal(native_client, gen["id"])
        assert final["status"] == "succeeded"
        generated = final["images"][0]
        assert (generated["width"], generated["height"]) == (1344, 768)


def test_i2i_exige_exatamente_uma_referencia(client, png_bytes):
    ref = client.post("/api/references", content=png_bytes, headers={"x-filename": "r.png"}).json()["reference"]
    sem_ref = client.post("/api/generations", json={
        "module": "image", "prompt": "i2i sem ref", "mode": "i2i",
    })
    assert sem_ref.status_code == 400
    assert "referencia" in sem_ref.json()["error"].lower()

    ref2 = client.post("/api/references", content=png_bytes, headers={"x-filename": "r2.png"}).json()["reference"]
    duas = client.post("/api/generations", json={
        "module": "image", "prompt": "i2i com duas", "mode": "i2i",
        "reference_ids": [ref["id"], ref2["id"]],
    })
    assert duas.status_code == 400


def test_i2i_sucesso_deriva_dimensoes_da_referencia(client, png_bytes):
    ref = client.post("/api/references", content=png_bytes, headers={"x-filename": "r.png"}).json()["reference"]
    created = client.post("/api/generations", json={
        "module": "image", "prompt": "transformar a referencia", "mode": "i2i",
        "denoise": 0.45, "reference_ids": [ref["id"]],
    })
    assert created.status_code == 201
    gen = created.json()["generation"]
    assert (gen["width"], gen["height"]) == i2i_latent_dims(64, 48)
    assert gen["params"]["mode"] == "i2i"
    assert gen["params"]["denoise"] == 0.45
    final = wait_terminal(client, gen["id"])
    assert final["status"] == "succeeded", final.get("error")
    generated = final["images"][0]
    assert (generated["width"], generated["height"]) == (gen["width"], gen["height"])


def test_i2i_denoise_padrao_e_faixa(client, png_bytes):
    ref = client.post("/api/references", content=png_bytes, headers={"x-filename": "r.png"}).json()["reference"]
    created = client.post("/api/generations", json={
        "module": "image", "prompt": "denoise padrao", "mode": "i2i", "reference_ids": [ref["id"]],
    })
    gen = created.json()["generation"]
    assert gen["params"]["denoise"] == pytest.approx(0.6)

    fora = client.post("/api/generations", json={
        "module": "image", "prompt": "denoise fora", "mode": "i2i",
        "denoise": 0.05, "reference_ids": [ref["id"]],
    })
    assert fora.status_code == 400
    acima = client.post("/api/generations", json={
        "module": "image", "prompt": "denoise alto", "mode": "i2i",
        "denoise": 1.5, "reference_ids": [ref["id"]],
    })
    assert acima.status_code == 422


def test_qualidade_presets_e_avancados(client):
    created = client.post("/api/generations", json={
        "module": "image", "prompt": "alta qualidade", "ratio": "1:1", "tier": "720p", "quality": "alta",
    })
    gen = created.json()["generation"]
    assert gen["params"]["quality"] == "alta"
    assert gen["params"]["steps"] == 40
    assert gen["params"]["cfg"] == pytest.approx(7.5)

    avancado = client.post("/api/generations", json={
        "module": "image", "prompt": "avancado", "ratio": "1:1", "tier": "720p",
        "steps": 25, "cfg": 6.5, "sampler": "dpmpp_2m",
    })
    params = avancado.json()["generation"]["params"]
    assert params["steps"] == 25 and params["cfg"] == pytest.approx(6.5)
    assert params["sampler"] == "dpmpp_2m"

    steps_fora = client.post("/api/generations", json={
        "module": "image", "prompt": "x", "ratio": "1:1", "tier": "720p", "steps": 200,
    })
    assert steps_fora.status_code == 400
    cfg_fora = client.post("/api/generations", json={
        "module": "image", "prompt": "x", "ratio": "1:1", "tier": "720p", "cfg": 25.0,
    })
    assert cfg_fora.status_code == 400
    sampler_errado = client.post("/api/generations", json={
        "module": "image", "prompt": "x", "ratio": "1:1", "tier": "720p", "sampler": "banana",
    })
    assert sampler_errado.status_code == 400


def test_t2i_com_referencia_sugere_modo_i2i(client, settings, png_bytes, monkeypatch):
    from backend.generation.registry import PROVIDERS
    from backend.main import create_app as _create

    class I2IOnlyStub(PROVIDERS["simulated"]):
        def capabilities(self):
            # motor real com so o workflow i2i mapeado: t2i nao aceita referencias
            return {"references": False, "img2img": True, "max_references": 1, "note": "stub"}

    monkeypatch.setitem(PROVIDERS, "simulated", I2IOnlyStub)
    with TestClient(_create(settings)) as scoped:
        ref = scoped.post("/api/references", content=png_bytes, headers={"x-filename": "r.png"}).json()["reference"]
        response = scoped.post("/api/generations", json={
            "module": "image", "prompt": "x", "ratio": "1:1", "tier": "720p",
            "reference_ids": [ref["id"]],  # sem mode=i2i: recusa e aponta o modo certo
        })
        assert response.status_code == 400
        assert "imagem para imagem" in response.json()["error"].lower()


# ------------------------------------------------------------------ presets expostos na API

def test_presets_incluem_qualidade_e_samplers(client):
    data = client.get("/api/presets").json()
    assert {item["key"] for item in data["quality_presets"]} == {"rapida", "padrao", "alta"}
    assert "euler" in data["samplers"]
    assert data["ranges"]["steps"] == [8, 60]


# ------------------------------------------------------------------ adaptador ComfyUI (unidade)

class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, content=b""):
        self.status_code = status_code
        self._json = json_data or {}
        self.content = content

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise GenerationError(f"HTTP {self.status_code}")


class _FakeClient:
    """Cliente httpx falso: registra chamadas e responde conforme o programa da instancia."""

    def __init__(self, program):
        self.program = program  # dict: (method, path_marker) -> _FakeResponse ou callable
        self.calls: list[tuple[str, str, dict]] = []

    async def get(self, path, **kwargs):
        self.calls.append(("GET", path, kwargs))
        return self._respond("GET", path)

    async def post(self, path, **kwargs):
        self.calls.append(("POST", path, kwargs))
        return self._respond("POST", path, kwargs)

    def _respond(self, method, path, kwargs=None):
        for (want_method, marker), handler in self.program.items():
            if want_method == method and marker in path:
                response = handler(kwargs or {}) if callable(handler) else handler
                return _FakeResponse(200, response) if isinstance(response, dict) else response
        raise GenerationError(f"FakeClient sem programa para {method} {path}")

    async def aclose(self):
        pass


def _png_bytes(width=8, height=8) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def _provider(tmp_path) -> ComfyUIProvider:
    cfg = ComfyUISettings(
        workflow=tmp_path / "t2i.json",
        workflow_i2i=tmp_path / "i2i.json",
        timeout_seconds=5.0,
        poll_interval_seconds=0.01,
        node_map={
            "prompt": ["6", "inputs", "text"], "negative_prompt": ["7", "inputs", "text"],
            "width": ["5", "inputs", "width"], "height": ["5", "inputs", "height"],
            "seed": ["3", "inputs", "seed"], "steps": ["3", "inputs", "steps"],
            "cfg": ["3", "inputs", "cfg"], "sampler": ["3", "inputs", "sampler_name"],
        },
        node_map_i2i={
            "prompt": ["6", "inputs", "text"], "negative_prompt": ["7", "inputs", "text"],
            "seed": ["3", "inputs", "seed"], "denoise": ["3", "inputs", "denoise"],
            "steps": ["3", "inputs", "steps"], "cfg": ["3", "inputs", "cfg"],
            "sampler": ["3", "inputs", "sampler_name"],
        },
        ref_node_map_i2i={1: ["11", "inputs", "image"]},
    )
    (tmp_path / "t2i.json").write_text(json.dumps({
        "3": {"class_type": "KSampler", "inputs": {"seed": 0, "steps": 20, "cfg": 7.0, "sampler_name": "euler", "denoise": 1.0}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
    }), encoding="utf-8")
    (tmp_path / "i2i.json").write_text(json.dumps({
        "3": {"class_type": "KSampler", "inputs": {"seed": 0, "steps": 20, "cfg": 7.0, "sampler_name": "euler", "denoise": 1.0}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        "11": {"class_type": "LoadImage", "inputs": {"image": "x.png"}},
    }), encoding="utf-8")
    return ComfyUIProvider(cfg)


def test_cancelamento_seguro_so_atinge_o_proprio_prompt(tmp_path):
    """Etapa 8: cancelamento INDIVIDUAL por job (POST /api/jobs/{prompt_id}/cancel) com o
    prompt_id REAL devolvido pelo ComfyUI; /interrupt global nunca e usado."""
    provider = _provider(tmp_path)
    fake = _FakeClient({
        ("POST", "/api/jobs/"): _FakeResponse(200, {"cancelled": True}),
    })
    provider._client = fake
    provider._prompt_ids["job1"] = "meu-prompt"

    assert asyncio.run(provider.cancel_current("job1")) == "requested"
    posts = [path for method, path, _ in fake.calls if method == "POST"]
    assert any("/api/jobs/meu-prompt/cancel" in path for path in posts)  # endpoint INDIVIDUAL, id real
    assert not any("/interrupt" in path for path in posts)               # /interrupt global nunca

    # Sem a API individual (ComfyUI anterior a 0.45): pendente proprio e removido por prompt_id
    provider2 = _provider(tmp_path)
    fake2 = _FakeClient({
        ("POST", "/api/jobs/"): _FakeResponse(404, {"error": "not found"}),
        ("GET", "/queue"): {"queue_running": [["1", "prompt-externo", {}]], "queue_pending": [["2", "meu-prompt", {}]]},
        ("POST", "/queue"): _FakeResponse(200, {}),
    })
    provider2._client = fake2
    provider2._prompt_ids["job1"] = "meu-prompt"
    assert asyncio.run(provider2.cancel_current("job1")) == "removed"
    posts2 = [(path, kwargs) for method, path, kwargs in fake2.calls if method == "POST"]
    assert not any("/interrupt" in path for path, _ in posts2)
    deletes = [kwargs.get("json") for _, kwargs in posts2 if "/queue" in _]
    assert {"delete": ["meu-prompt"]} in deletes  # so o NOSSO prompt; externo intocado

    # Job proprio ja terminal pela API individual ({"cancelled": false}): nenhuma acao
    provider3 = _provider(tmp_path)
    fake3 = _FakeClient({("POST", "/api/jobs/"): _FakeResponse(200, {"cancelled": False})})
    provider3._client = fake3
    provider3._prompt_ids["job1"] = "meu-prompt"
    assert asyncio.run(provider3.cancel_current("job1")) is None
    assert not [call for call in fake3.calls if call[0] == "POST" and "/queue" in call[1]]


def test_cancelamento_em_execucao_sem_api_individual_aguarda_e_nao_toca_em_nada(tmp_path):
    """Sem o endpoint individual (ComfyUI anterior a 0.45): comportamento conservador —
    nenhum POST destrutivo (nem /interrupt global, nem delete na fila), so a checagem da
    fila. A unica via POST e a sonda idempotente da API individual, que descobre o 404 e
    memoriza a ausencia do endpoint (a chamada seguinte nao envia POST nenhum)."""
    provider = _provider(tmp_path)
    fake = _FakeClient({
        ("POST", "/api/jobs/"): _FakeResponse(404, {"error": "not found"}),
        ("GET", "/queue"): {"queue_running": [["1", "meu-prompt", {}]], "queue_pending": []},
        ("POST", "/interrupt"): _FakeResponse(200, {}),
        ("POST", "/queue"): _FakeResponse(200, {}),
    })
    provider._client = fake
    provider._prompt_ids["job1"] = "meu-prompt"
    assert asyncio.run(provider.cancel_current("job1")) == "awaiting"
    posts = [path for method, path, _ in fake.calls if method == "POST"]
    assert not any("/interrupt" in path for path in posts)   # /interrupt global nunca
    assert not any("/queue" in path for path in posts)       # nem delete cirurgico em execucao
    assert all("/api/jobs/" in path for path in posts)       # unica via POST: a sonda (404 memorizado)
    # A ausencia do endpoint fica memorizada: o cancelamento seguinte nao envia POST nenhum.
    assert asyncio.run(provider.cancel_current("job1")) == "awaiting"
    assert len([call for call in fake.calls if call[0] == "POST"]) == len(posts)


def test_execution_interrupted_vira_cancelamento(tmp_path):
    provider = _provider(tmp_path)
    fake = _FakeClient({
        ("GET", "/history/"): _FakeResponse(200, {"pid1": {
            "status": {"status_str": "error", "completed": False,
                       "messages": [["execution_interrupted", {}]]},
            "outputs": {},
        }}),
        ("GET", "/queue"): {"queue_running": [["1", "pid1", {}]], "queue_pending": []},
    })
    provider._client = fake
    progress = lambda stage, pct: None
    with pytest.raises(GenerationCancelled):
        asyncio.run(provider._wait_outputs("pid1", "job1", progress, lambda: False))


def test_i2i_generate_aplica_campos_no_workflow(tmp_path):
    provider = _provider(tmp_path)
    workflow_posted: dict = {}

    def handle_prompt(kwargs):
        workflow_posted.update(kwargs.get("json", {}).get("prompt", {}))
        return _FakeResponse(200, {"prompt_id": "pid1"})

    fake = _FakeClient({
        ("POST", "/upload/image"): _FakeResponse(200, {"name": "imagecore-reference.png"}),
        ("POST", "/prompt"): handle_prompt,
        ("GET", "/queue"): {"queue_running": [["1", "pid1", {}]], "queue_pending": []},
        ("GET", "/history/"): _FakeResponse(200, {"pid1": {
            "status": {"status_str": "success", "completed": True,
                       "messages": [["execution_success", {}]]},
            "outputs": {"9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}},
        }}),
        ("GET", "/view"): _FakeResponse(200, content=_png_bytes()),
    })
    provider._client = fake
    reference = tmp_path / "ref.png"
    reference.write_bytes(_png_bytes(64, 48))
    request = GenerateRequest(
        job_id="job1", prompt="uma transformacao", width=256, height=256, seed=42,
        references=[reference], mode="i2i", denoise=0.4, steps=25, cfg=6.5, sampler="euler",
    )
    result = asyncio.run(provider.generate(request, progress=lambda stage, pct: None, cancel=lambda: False))
    assert result.data == _png_bytes()
    assert provider._model_warm is True
    ksampler = workflow_posted["3"]["inputs"]
    assert ksampler["seed"] == 42
    assert ksampler["steps"] == 25
    assert ksampler["cfg"] == pytest.approx(6.5)
    assert ksampler["sampler_name"] == "euler"
    assert ksampler["denoise"] == pytest.approx(0.4)
    assert workflow_posted["6"]["inputs"]["text"] == "uma transformacao"
    assert workflow_posted["11"]["inputs"]["image"] == "imagecore-reference.png"


def test_t2i_generate_recusa_i2i_sem_configuracao(tmp_path):
    provider = _provider(tmp_path)
    provider.cfg.workflow_i2i = None
    provider.cfg.ref_node_map_i2i = {}
    reference = tmp_path / "ref.png"
    reference.write_bytes(_png_bytes())
    request = GenerateRequest(job_id="job1", prompt="x", width=256, height=256, seed=1,
                              references=[reference], mode="i2i")
    with pytest.raises(GenerationError, match="img2img"):
        asyncio.run(provider.generate(request, progress=lambda stage, pct: None, cancel=lambda: False))


def test_estimativa_de_vram_por_resolucao_modo_e_peso_quente(tmp_path):
    provider = _provider(tmp_path)
    provider.cfg.estimated_vram_mb = 8500
    provider._model_warm = False
    fria_1mp = provider.estimate_vram_mb(1024, 1024, "t2i", comfyui_vram_mb=0)
    fria_1mp_i2i = provider.estimate_vram_mb(1024, 1024, "i2i", comfyui_vram_mb=0)
    assert fria_1mp == 8500
    assert fria_1mp_i2i == 8500 + 256  # VAE encode da referencia
    # evidencia do PROPRIO worker (memoria do processo do ComfyUI) => pesos provavelmente residentes
    quente = provider.estimate_vram_mb(1024, 1024, "t2i", comfyui_vram_mb=8200)
    # pesos residentes: cobra so o incremental (os pesos ja estao no "usado" do nvidia-smi)
    assert quente == int(8500 * 0.2)
    quente_i2i = provider.estimate_vram_mb(1024, 1024, "i2i", comfyui_vram_mb=8200)
    assert quente_i2i == int(8500 * 0.2) + 256
    # uso GLOBAL alto (outros aplicativos) NAO e evidencia: segue a frio (Etapa 4)
    assert provider.estimate_vram_mb(1024, 1024, "t2i", comfyui_vram_mb=None) == 8500
    # flag da sessao tambem ativa o caminho incremental (sobrevive sem snapshot de GPU)
    provider._model_warm = True
    assert provider.estimate_vram_mb(1024, 1024, "t2i") == int(8500 * 0.2)
    provider._model_warm = False
    alta = provider.estimate_vram_mb(2048, 2048, "t2i", comfyui_vram_mb=8200)
    assert alta == int(8500 * 0.2 * 2.0)  # fator de resolucao com teto 2x
    assert alta > quente


def test_vram_basis_descreve_a_base_da_estimativa(tmp_path):
    provider = _provider(tmp_path)
    provider.cfg.estimated_vram_mb = 8500
    assert "conservadora" in provider.vram_basis(None)
    assert "a frio" in provider.vram_basis(0)
    assert "processo do motor" in provider.vram_basis(8200)
    provider._model_warm = True
    assert "sessao" in provider.vram_basis(0)
