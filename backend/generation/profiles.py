"""Perfis de geracao (Etapa 7): registro estatico de motores/modelos e deteccao honesta de estado.

Cada perfil declara workflow (API format), node_map, arquivos de pesos esperados e o contrato
de parametros que ele aceita. O estado e derivado de evidencias locais (arquivos no disco),
nunca de otimismo: um candidato sem pesos NUNCA aparece como disponivel para gerar.

Estados (rotulos na UI):
- "instalado": workflow presente + TODOS os pesos presentes no models_dir + perfil validado
  (smoke real registrado);
- "compativel": workflow presente, porem pesos ausentes (ou models_dir nao configurado);
- "nao_validado": pesos presentes, smoke real ainda nao executado;
- "indisponivel": workflow do perfil ausente ou invalido.

O perfil ativo do ComfyUI continua sendo o par (comfyui.workflow, comfyui.node_map) em
config/settings.yaml — o provider NAO foi reconstruido. Um pedido com perfil cujo workflow
difere do ativo e recusado (sem substituicao silenciosa); o ComfyUIProvider ainda verifica,
no momento de executar, que o modelo do workflow confere com o perfil (defesa em profundidade).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from backend.core.config import ComfyUISettings
from backend.core.errors import ValidationError
from backend.generation.base import GenerationError

WORKFLOW_DIR = Path("config/workflows")

# Contratos de parametros por perfil. SDXL mantem as faixas globais de presets.py; os
# candidatos usam os valores dos templates oficiais do ComfyUI (menores/fixos) — nunca
# herdam automaticamente os parametros do SDXL.
_SDXL_STEPS_RANGE = (8, 60)
_SDXL_CFG_RANGE = (1.0, 15.0)


@dataclass(frozen=True)
class GenerationProfile:
    id: str
    label: str
    provider: str  # provider que executa este perfil ("comfyui")
    license: str
    license_url: str
    repo_url: str
    docs_url: str
    workflow_t2i: Path
    workflow_i2i: Path | None
    node_map: dict[str, list[str]]
    node_map_i2i: dict[str, list[str]] = field(default_factory=dict)
    ref_node_map_i2i: dict[int, list[str]] = field(default_factory=dict)
    # Pesos esperados: {subpasta do models_dir: nome do arquivo}. Vazio = sem pesos proprios.
    required_files: dict[str, str] = field(default_factory=dict)
    # Modelo que o workflow DEVE carregar (protecao contra substituicao silenciosa).
    expected_model: str = ""
    # Contrato de parametros aceito por este perfil (validacao no pedido).
    steps_range: tuple[int, int] = _SDXL_STEPS_RANGE
    steps_default: int = 30
    cfg_range: tuple[float, float] = _SDXL_CFG_RANGE
    cfg_default: float = 7.0
    sampler_fixed: str | None = None  # fixo pelo perfil (None = lista livre do SDXL)
    negative_prompt_supported: bool = True
    native_buckets: bool = False  # SDXL True (buckets ~1 MP); candidatos: resolucao flexivel
    img2img: bool = False  # ediçao img2img EFETIVAMENTE ligada no app (workflow_i2i + node_map)
    multi_reference: bool = False
    # Estimativas rotuladas (nao medicoes locais): tamanhos oficiais dos repositorios.
    estimated_download_label: str = ""
    estimated_vram_mb: int = 0
    notes: str = ""
    # Smoke real registrado (nunca "otimista": so o homologado fica True).
    validated: bool = False


SDXL_PROFILE = GenerationProfile(
    id="sdxl",
    label="SDXL Base 1.0",
    provider="comfyui",
    license="CreativeML Open RAIL++-M",
    license_url="https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/main/LICENSE.md",
    repo_url="https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0",
    docs_url="https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0",
    workflow_t2i=WORKFLOW_DIR / "t2i.sdxl.json",
    workflow_i2i=WORKFLOW_DIR / "i2i.sdxl.json",
    node_map={
        "prompt": ["6", "inputs", "text"],
        "negative_prompt": ["7", "inputs", "text"],
        "width": ["5", "inputs", "width"],
        "height": ["5", "inputs", "height"],
        "seed": ["3", "inputs", "seed"],
        "steps": ["3", "inputs", "steps"],
        "cfg": ["3", "inputs", "cfg"],
        "sampler": ["3", "inputs", "sampler_name"],
    },
    node_map_i2i={
        "prompt": ["6", "inputs", "text"],
        "negative_prompt": ["7", "inputs", "text"],
        "seed": ["3", "inputs", "seed"],
        "denoise": ["3", "inputs", "denoise"],
        "steps": ["3", "inputs", "steps"],
        "cfg": ["3", "inputs", "cfg"],
        "sampler": ["3", "inputs", "sampler_name"],
    },
    ref_node_map_i2i={1: ["11", "inputs", "image"]},
    required_files={"checkpoints": "sd_xl_base_1.0.safetensors"},
    expected_model="sd_xl_base_1.0.safetensors",
    steps_default=30,
    cfg_default=7.0,
    native_buckets=True,
    img2img=True,
    estimated_download_label="6,46 GiB (checkpoint unico)",
    estimated_vram_mb=8500,
    notes="Instalado e homologado (Etapa 7): geracao t2i real, buckets nativos ~1 MP, i2i com denoise.",
    validated=True,
)

FLUX2_KLEIN_PROFILE = GenerationProfile(
    id="flux2-klein-4b",
    label="FLUX.2 Klein 4B (destilado)",
    provider="comfyui",
    license="Apache-2.0",
    license_url="https://huggingface.co/black-forest-labs/FLUX.2-klein-4B/blob/main/LICENSE.md",
    repo_url="https://huggingface.co/black-forest-labs/FLUX.2-klein-4B",
    docs_url="https://docs.comfy.org/tutorials/flux/flux-2-klein",
    workflow_t2i=WORKFLOW_DIR / "t2i.flux2-klein.json",
    workflow_i2i=None,  # a edicao existe no MODELO, mas ainda nao esta ligada no app
    node_map={
        "prompt": ["6", "inputs", "text"],
        "width": ["5", "inputs", "width"],
        "height": ["5", "inputs", "height"],
        "seed": ["3", "inputs", "seed"],
        "steps": ["3", "inputs", "steps"],
        "cfg": ["3", "inputs", "cfg"],
        "sampler": ["3", "inputs", "sampler_name"],
    },
    required_files={
        "diffusion_models": "flux-2-klein-4b.safetensors",
        "text_encoders": "qwen_3_4b.safetensors",
        "vae": "flux2-vae.safetensors",
    },
    expected_model="flux-2-klein-4b.safetensors",
    steps_range=(1, 8),
    steps_default=4,
    cfg_range=(1.0, 1.0),
    cfg_default=1.0,
    sampler_fixed="euler",
    negative_prompt_supported=False,  # destilado usa ConditioningZeroOut (template oficial)
    multi_reference=False,  # suportada pelo modelo; workflow de edicao ainda nao ligado no app
    estimated_download_label="~15,0 GiB (7,22 GiB DiT bf16 + 7,49 GiB Qwen3-4B + 0,31 GiB VAE)",
    estimated_vram_mb=10500,
    notes=(
        "Candidato. Template oficial presente no ComfyUI local. 4 passos, CFG 1 (sem negative). "
        "Edicao multi-referencia existe no modelo, porem NAO esta ligada no app. FP8 nao tem "
        "ganho nativo em Ampere (sem tensor core FP8) — o bf16 e o caminho honesto na 3080 Ti."
    ),
)

Z_IMAGE_TURBO_PROFILE = GenerationProfile(
    id="z-image-turbo",
    label="Z-Image-Turbo",
    provider="comfyui",
    license="Apache-2.0",
    license_url="https://huggingface.co/Tongyi-MAI/Z-Image-Turbo",
    repo_url="https://huggingface.co/Tongyi-MAI/Z-Image-Turbo",
    docs_url="https://huggingface.co/Comfy-Org/Z-Image-Turbo",
    workflow_t2i=WORKFLOW_DIR / "t2i.z-image-turbo.json",
    workflow_i2i=None,  # Z-Image-Edit (variante de edicao) ainda nao foi lancada oficialmente
    node_map={
        "prompt": ["6", "inputs", "text"],
        "width": ["5", "inputs", "width"],
        "height": ["5", "inputs", "height"],
        "seed": ["3", "inputs", "seed"],
        "steps": ["3", "inputs", "steps"],
        "cfg": ["3", "inputs", "cfg"],
        "sampler": ["3", "inputs", "sampler_name"],
    },
    required_files={
        "diffusion_models": "z_image_turbo_int8_convrot.safetensors",
        "text_encoders": "qwen_3_4b_fp8_mixed.safetensors",
        "vae": "ae.safetensors",
    },
    expected_model="z_image_turbo_int8_convrot.safetensors",
    steps_range=(1, 12),
    steps_default=8,
    cfg_range=(1.0, 1.0),
    cfg_default=1.0,
    sampler_fixed="res_multistep",
    negative_prompt_supported=False,  # template oficial usa ConditioningZeroOut (CFG desligado)
    estimated_download_label=(
        "11,33 GiB verificados na API oficial (17/11/2026): 5,78 GiB (int8_convrot) + "
        "5,24 GiB (qwen_3_4b_fp8_mixed) + 0,31 GiB (ae)"
    ),
    estimated_vram_mb=8000,
    notes=(
        "Candidato ESCOLHIDO (Etapa 8). 8 passos, CFG 1, ModelSamplingAuraFlow shift 3. Variante "
        "int8 oficial da Comfy-Org (template image_z_image_turbo_int8 presente no ComfyUI local "
        "0.45.0). Text encoder qwen_3_4b_fp8_mixed; o bf16 completo (~20 GiB) NAO e o alvo. "
        "Edicao: variante Z-Image-Edit ainda nao lancada (sem img2img no app). Pesos ainda nao "
        "instalados (download exige autorizacao explicita)."
    ),
)

PROFILES: dict[str, GenerationProfile] = {
    p.id: p for p in (SDXL_PROFILE, FLUX2_KLEIN_PROFILE, Z_IMAGE_TURBO_PROFILE)
}

STATUS_LABELS = {
    "instalado": "INSTALADO",
    "compativel": "COMPATÍVEL",
    "nao_validado": "NÃO VALIDADO",
    "indisponivel": "INDISPONÍVEL",
}


def get_profile(profile_id: str) -> GenerationProfile | None:
    return PROFILES.get(profile_id)


def _workflow_ok(path: Path) -> bool:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and bool(data)


def weights_present(profile: GenerationProfile, comfyui: ComfyUISettings) -> bool | None:
    """True/False quando o models_dir permite verificar; None quando nao configurado."""
    if not comfyui.models_dir:
        return None
    for subfolder, filename in profile.required_files.items():
        if not (comfyui.models_dir / subfolder / filename).is_file():
            return False
    return True


def profile_status(profile: GenerationProfile, comfyui: ComfyUISettings) -> tuple[str, str]:
    """(estado, motivo honesto) — derivado apenas de evidencias locais."""
    if not _workflow_ok(profile.workflow_t2i):
        return "indisponivel", f"Workflow {profile.workflow_t2i} ausente ou invalido."
    present = weights_present(profile, comfyui)
    if present is None:
        return "compativel", "Workflow presente; pesos nao verificados (comfyui.models_dir nao configurado)."
    if not present:
        missing = ", ".join(
            f"{sub}/{name}"
            for sub, name in profile.required_files.items()
            if not (comfyui.models_dir / sub / name).is_file()
        )
        return "compativel", f"Workflow presente; pesos ausentes: {missing}."
    if not profile.validated:
        return "nao_validado", "Pesos presentes; smoke real ainda nao executado para este perfil."
    return "instalado", "Workflow, pesos e smoke real registrados."


def active_workflow_matches(profile: GenerationProfile, comfyui: ComfyUISettings) -> bool:
    """O provider executa comfyui.workflow: o perfil precisa ser exatamente o ativo."""
    return Path(comfyui.workflow).resolve() == profile.workflow_t2i.resolve()


def assert_runnable(profile: GenerationProfile, comfyui: ComfyUISettings) -> None:
    """Recusa perfis nao operacionais com erro honesto (nunca substituicao silenciosa).
    Codigos estaveis (i18n): profile_not_installed / active_workflow_mismatch."""
    status, reason = profile_status(profile, comfyui)
    if status != "instalado":
        raise GenerationError(
            f"Perfil '{profile.label}' nao esta instalado ({STATUS_LABELS[status]}): {reason} "
            "Nao ha substituicao silenciosa por outro modelo ou simulacao.",
            code="profile_not_installed",
        )
    if not active_workflow_matches(profile, comfyui):
        raise GenerationError(
            f"Perfil '{profile.label}' exige comfyui.workflow = {profile.workflow_t2i.as_posix()} "
            f"(ativo: {Path(comfyui.workflow).as_posix()}). Ajuste config/settings.yaml.",
            code="active_workflow_mismatch",
        )


def _status_code(profile: GenerationProfile, comfyui: ComfyUISettings, status: str) -> tuple[str, list[str]]:
    """Codigo estavel do motivo do estado (i18n do card de perfis) + lista de pesos ausentes."""
    if status == "indisponivel":
        return "workflow_missing", []
    if not comfyui.models_dir:
        return "weights_unverified", []
    missing = [
        f"{sub}/{name}"
        for sub, name in profile.required_files.items()
        if not (comfyui.models_dir / sub / name).is_file()
    ]
    if status == "compativel":
        return "weights_missing", missing
    if status == "nao_validado":
        return "smoke_pending", missing
    return "installed_ok", []


def serialize(profile: GenerationProfile, comfyui: ComfyUISettings) -> dict:
    status, reason = profile_status(profile, comfyui)
    status_code, missing_files = _status_code(profile, comfyui, status)
    return {
        "id": profile.id,
        "label": profile.label,
        "provider": profile.provider,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "status_reason": reason,
        "status_code": status_code,
        "missing_files": missing_files,
        "license": profile.license,
        "license_url": profile.license_url,
        "repo_url": profile.repo_url,
        "docs_url": profile.docs_url,
        "workflow_t2i": profile.workflow_t2i.as_posix(),
        "expected_model": profile.expected_model,
        "params": {
            "steps_range": list(profile.steps_range),
            "steps_default": profile.steps_default,
            "cfg_range": list(profile.cfg_range),
            "cfg_default": profile.cfg_default,
            "sampler_fixed": profile.sampler_fixed,
            "negative_prompt_supported": profile.negative_prompt_supported,
            "native_buckets": profile.native_buckets,
            "img2img": profile.img2img,
            "multi_reference": profile.multi_reference,
        },
        "estimated_download_label": profile.estimated_download_label,
        "estimated_vram_mb": profile.estimated_vram_mb,
        "notes": profile.notes,
        "validated": profile.validated,
    }


def list_profiles(comfyui: ComfyUISettings) -> list[dict]:
    return [serialize(profile, comfyui) for profile in PROFILES.values()]


def validate_request_params(profile: GenerationProfile, *, steps: int | None, cfg: float | None,
                            sampler: str | None, negative_prompt: str | None) -> None:
    """Cada perfil define seus parametros compativeis — nunca herda os do SDXL.
    Codigos estaveis (i18n): invalid_steps / invalid_cfg / sampler_fixed / negative_not_supported."""
    if steps is not None and not profile.steps_range[0] <= steps <= profile.steps_range[1]:
        raise ValidationError(
            f"Perfil '{profile.label}' aceita steps de {profile.steps_range[0]} a {profile.steps_range[1]} "
            f"(padrao {profile.steps_default}); recebido {steps}.",
            code="invalid_steps",
            details={"min": profile.steps_range[0], "max": profile.steps_range[1], "received": steps},
        )
    if cfg is not None and not profile.cfg_range[0] <= cfg <= profile.cfg_range[1]:
        raise ValidationError(
            f"Perfil '{profile.label}' aceita CFG de {profile.cfg_range[0]} a {profile.cfg_range[1]} "
            f"(padrao {profile.cfg_default}); recebido {cfg}.",
            code="invalid_cfg",
            details={"min": profile.cfg_range[0], "max": profile.cfg_range[1], "received": cfg},
        )
    if profile.sampler_fixed and sampler and sampler != profile.sampler_fixed:
        raise ValidationError(
            f"Perfil '{profile.label}' usa o sampler fixo '{profile.sampler_fixed}'; recebido '{sampler}'.",
            code="sampler_fixed",
            details={"expected": profile.sampler_fixed, "received": sampler},
        )
    if negative_prompt is not None and not profile.negative_prompt_supported:
        raise ValidationError(
            f"Perfil '{profile.label}' nao aceita negative prompt (template oficial roda sem CFG/negative).",
            code="negative_not_supported",
        )
