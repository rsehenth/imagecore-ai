# -*- coding: utf-8 -*-
"""Etapa 7: perfis de geracao — selecao, estado honesto, isolamento de parametros,
persistencia nos metadados e protecao contra substituicao silenciosa por simulacao."""

from pathlib import Path
import json

import pytest

from backend.core.config import ComfyUISettings, ProviderSettings, Settings, StorageSettings
from backend.generation.base import GenerateRequest
from backend.generation.comfyui import ComfyUIProvider
from backend.generation.profiles import (
    FLUX2_KLEIN_PROFILE,
    PROFILES,
    SDXL_PROFILE,
    Z_IMAGE_TURBO_PROFILE,
    profile_status,
    weights_present,
)
from tests.conftest import make_settings, wait_terminal


# ------------------------------------------------------------------ estado dos perfis

def test_api_lista_tres_perfis_com_estado_honesto(client):
    data = client.get("/api/profiles").json()["profiles"]
    by_id = {p["id"]: p for p in data}
    assert set(by_id) == {"sdxl", "flux2-klein-4b", "z-image-turbo"}
    # Com provider simulado e models_dir nao configurado, o SDXL aparece pelo que e:
    # workflow presente, pesos nao verificados -> "compativel" (honesto).
    assert by_id["sdxl"]["status"] in ("compativel", "instalado")
    # Candidatos NUNCA aparecem como instalados sem pesos verificados.
    assert by_id["flux2-klein-4b"]["status"] in ("compativel", "nao_validado", "indisponivel")
    assert by_id["z-image-turbo"]["status"] in ("compativel", "nao_validado", "indisponivel")
    for perfil in data:
        assert perfil["status_label"]
        assert perfil["license"]
        assert perfil["repo_url"].startswith("https://huggingface.co/")


def test_estado_instalado_exige_todos_os_pesos(tmp_path):
    models = tmp_path / "models"
    comfy = ComfyUISettings(models_dir=models)
    status, _ = profile_status(SDXL_PROFILE, comfy)
    assert status == "compativel"  # nada baixado
    (models / "checkpoints").mkdir(parents=True)
    (models / "checkpoints" / "sd_xl_base_1.0.safetensors").write_bytes(b"x")
    status, _ = profile_status(SDXL_PROFILE, comfy)
    assert status == "instalado"  # SDXL validado + peso presente


def test_peso_faltante_deixa_candidato_compativel(tmp_path):
    models = tmp_path / "models"
    (models / "diffusion_models").mkdir(parents=True)
    (models / "diffusion_models" / "z_image_turbo_int8_convrot.safetensors").write_bytes(b"x")
    comfy = ComfyUISettings(models_dir=models)
    status, reason = profile_status(Z_IMAGE_TURBO_PROFILE, comfy)
    assert status == "compativel"
    assert "text_encoders" in reason  # qwen_3_4b ausente aparece no motivo


def test_pesos_presentes_sem_smoke_ficam_nao_validado(tmp_path):
    models = tmp_path / "models"
    for sub, name in Z_IMAGE_TURBO_PROFILE.required_files.items():
        (models / sub).mkdir(parents=True, exist_ok=True)
        (models / sub / name).write_bytes(b"x")
    comfy = ComfyUISettings(models_dir=models)
    status, _ = profile_status(Z_IMAGE_TURBO_PROFILE, comfy)
    assert status == "nao_validado"  # honesto: pesos la, smoke real nao registrado


def test_workflow_ausente_fica_indisponivel(tmp_path):
    from backend.generation.profiles import GenerationProfile

    fantasma = GenerationProfile(
        id="fantasma", label="Fantasma", provider="comfyui", license="x", license_url="x",
        repo_url="x", docs_url="x",
        workflow_t2i=Path("config/workflows/nao-existe.json"), workflow_i2i=None, node_map={},
    )
    status, _ = profile_status(fantasma, ComfyUISettings())
    assert status == "indisponivel"


# ------------------------------------------------------------------ selecao no pedido

def test_perfil_desconhecido_rejeitado(client):
    response = client.post(
        "/api/generations",
        json={"module": "image", "prompt": "teste", "ratio": "1:1", "tier": "720p", "profile": "mistral"},
    )
    assert response.status_code == 400
    assert "desconhecido" in response.json()["error"]


def test_candidato_sem_pesos_e_recusado_sem_substituicao_silenciosa(client):
    """Um candidato indisponivel gera 409 e NAO cria job (nem cai para simulado/sdxl)."""
    antes = client.get("/api/generations?limit=100").json()["generations"]
    for perfil in ("flux2-klein-4b", "z-image-turbo"):
        response = client.post(
            "/api/generations",
            json={"module": "image", "prompt": "teste", "ratio": "1:1", "tier": "720p", "profile": perfil},
        )
        assert response.status_code == 409
        body = response.json()["error"].lower()
        assert "substituicao silenciosa" in body or "nao esta instalado" in body
    depois = client.get("/api/generations?limit=100").json()["generations"]
    assert len(depois) == len(antes)


def test_provider_simulado_recusa_perfil_de_candidato_no_worker(client):
    """Defesa em profundidade: se um job do candidato chegasse a fila, o worker falha
    sem desenhar placeholder e sem inventar inferencia."""
    from backend.generation.simulated import SimulatedProvider

    request = GenerateRequest(
        job_id="x", prompt="p", width=64, height=64, seed=1, profile="z-image-turbo"
    )
    import asyncio

    with pytest.raises(Exception, match="simulacao"):
        asyncio.run(SimulatedProvider().generate(request, progress=lambda s, p: None, cancel=lambda: False))


# ------------------------------------------------------------------ isolamento de parametros

@pytest.mark.parametrize(
    ("perfil", "payload"),
    [
        ("z-image-turbo", {"steps": 20}),  # SDXL aceita 20; Z-Image-Turbo nao (max 8)
        ("z-image-turbo", {"cfg": 7.0}),  # CFG do SDXL; Z-Image roda com CFG 1
        ("z-image-turbo", {"negative_prompt": "blurry"}),  # template oficial sem negative
        ("flux2-klein-4b", {"steps": 30}),
        ("flux2-klein-4b", {"cfg": 6.5}),
        ("flux2-klein-4b", {"negative_prompt": "text"}),
        ("flux2-klein-4b", {"sampler": "euler_ancestral"}),  # fixo euler
    ],
)
def test_parametros_do_sdxl_nao_vazam_para_candidatos(client, perfil, payload):
    response = client.post(
        "/api/generations",
        json={"module": "image", "prompt": "teste", "ratio": "1:1", "tier": "720p", "profile": perfil, **payload},
    )
    assert response.status_code == 400
    assert "Perfil" in response.json()["error"]


def test_sdxl_mantem_faixas_e_presets_atuais(client):
    response = client.post(
        "/api/generations",
        json={"module": "image", "prompt": "teste", "ratio": "1:1", "tier": "720p",
              "quality": "rapida", "steps": 20, "cfg": 6.5, "sampler": "euler"},
    )
    assert response.status_code == 201
    params = response.json()["generation"]["params"]
    assert params["steps"] == 20 and params["cfg"] == 6.5


# ------------------------------------------------------------------ persistencia nos metadados

def test_perfil_persistido_nos_metadados_da_geracao(client):
    gen = client.post(
        "/api/generations",
        json={"module": "image", "prompt": "cidade ao amanhecer", "ratio": "1:1", "tier": "720p"},
    ).json()["generation"]
    final = wait_terminal(client, gen["id"])
    assert final["status"] == "succeeded"
    assert final["params"]["profile"] == "sdxl"


# ------------------------------------------------------------------ validacao de workflows por perfil

@pytest.mark.parametrize("profile_id", ["sdxl", "flux2-klein-4b", "z-image-turbo"])
def test_workflow_do_perfil_e_valido_e_mapeado(profile_id):
    perfil = PROFILES[profile_id]
    import json

    workflow = json.loads(Path(perfil.workflow_t2i).read_text(encoding="utf-8"))
    assert isinstance(workflow, dict) and workflow
    # Todo caminho do node_map resolve dentro do workflow (set-path "seco").
    from backend.generation.comfyui import _set_path
    for field, path in perfil.node_map.items():
        document = json.loads(json.dumps(workflow))
        _set_path(document, path, 0 if field in ("width", "height", "seed", "steps") else "x")
    if perfil.expected_model:
        modelos = [
            node["inputs"].get("ckpt_name") or node["inputs"].get("unet_name", "")
            for node in workflow.values()
            if isinstance(node, dict)
            and node.get("class_type") in ("CheckpointLoaderSimple", "UNETLoader")
        ]
        assert perfil.expected_model in modelos


def test_text_encoders_oficiais_dos_candidatos():
    """Nomes oficiais verificados na API do Hugging Face (17/11/2026; tamanhos e sha256 em
    docs/model-profiles.md): o Klein 4B usa o Qwen3-4B bf16 (`qwen_3_4b.safetensors`) e o
    Z-Image-Turbo int8 usa `qwen_3_4b_fp8_mixed.safetensors` — arquivos DIFERENTES, sem
    reaproveitamento de peso entre os candidatos escolhidos. O compartilhamento (mesmo
    sha256 nos repos Comfy-Org) vale so para o par bf16 (Z-Image bf16 completo, nao alvo)."""
    assert FLUX2_KLEIN_PROFILE.required_files["text_encoders"] == "qwen_3_4b.safetensors"
    assert Z_IMAGE_TURBO_PROFILE.required_files["text_encoders"] == "qwen_3_4b_fp8_mixed.safetensors"
    # Os workflows do app apontam exatamente para esses arquivos (defesa contra desvio).
    for perfil in (FLUX2_KLEIN_PROFILE, Z_IMAGE_TURBO_PROFILE):
        workflow = json.loads(Path(perfil.workflow_t2i).read_text(encoding="utf-8"))
        clips = [
            node["inputs"].get("clip_name", "")
            for node in workflow.values()
            if isinstance(node, dict) and node.get("class_type") == "CLIPLoader"
        ]
        assert perfil.required_files["text_encoders"] in clips


# ------------------------------------------------------------------ substituicao silenciosa (provider real)

def test_comfyui_recusa_workflow_que_aponta_para_outro_modelo(tmp_path):
    """Se o workflow ativo aponta para outro modelo, o provider falha ANTES de submeter."""
    workflow = tmp_path / "t2i.json"
    workflow.write_text(
        '{"3": {"class_type": "KSampler", "inputs": {"seed": 0}}, '
        '"4": {"class_type": "UNETLoader", "inputs": {"unet_name": "outro-modelo.safetensors"}}}',
        encoding="utf-8",
    )
    provider = ComfyUIProvider(ComfyUISettings(workflow=workflow, base_url="http://127.0.0.1:1"))
    request = GenerateRequest(
        job_id="j1", prompt="p", width=64, height=64, seed=1,
        profile="sdxl", expected_model="sd_xl_base_1.0.safetensors",
    )
    import asyncio

    with pytest.raises(Exception, match="substituicao silenciosa"):
        asyncio.run(provider.generate(request, progress=lambda s, p: None, cancel=lambda: False))


def test_models_dir_nao_configurado_retorna_indeciso(tmp_path):
    comfy = ComfyUISettings()
    assert weights_present(SDXL_PROFILE, comfy) is None
