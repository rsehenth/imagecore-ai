"""Rotas de sistema: saude, presets, capacidades, diagnostico (GPU/motor) e liberacao
controlada de memoria do motor (POST /free no ComfyUI, Etapa 8)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.api.deps import get_provider, get_settings
from backend.core.config import Settings
from backend.core.gpu import gpu_evidence, gpu_snapshot, port_of_base_url
from backend.generation.base import GenerationError, GenerationUnavailableError
from backend.generation.profiles import list_profiles
from backend.storage.gallery import ConflictError
from backend.core.presets import (
    ASPECT_RATIOS,
    CFG_RANGE,
    DENOISE_RANGE,
    PLATFORM_PRESETS,
    QUALITY_LABELS,
    QUALITY_PRESETS,
    RESOLUTION_TIERS,
    SAMPLERS,
    STEPS_RANGE,
    STYLE_LABELS,
)

router = APIRouter(tags=["system"])

_VERSION = "0.1.0"


@router.get("/health")
def health() -> dict:
    return {"ok": True, "name": "ImageCore AI", "version": _VERSION}


@router.get("/presets")
def presets() -> dict:
    return {
        "platform_presets": PLATFORM_PRESETS,
        "aspect_ratios": ASPECT_RATIOS,
        "resolution_tiers": RESOLUTION_TIERS,
        "styles": [{"key": key, "label": STYLE_LABELS.get(key, key)} for key in STYLE_LABELS],
        "quality_presets": [
            {"key": key, "label": QUALITY_LABELS.get(key, key), **QUALITY_PRESETS[key]}
            for key in QUALITY_PRESETS
        ],
        "samplers": list(SAMPLERS),
        "ranges": {
            "steps": list(STEPS_RANGE),
            "cfg": list(CFG_RANGE),
            "denoise": list(DENOISE_RANGE),
        },
    }


@router.get("/profiles")
def profiles(settings: Settings = Depends(get_settings)) -> dict:
    """Etapa 7: perfis de geracao com estado honesto (instalado/compativel/indisponivel/
    nao validado), contrato de parametros por modelo e links oficiais."""
    return {"profiles": list_profiles(settings.comfyui)}


@router.get("/provider")
async def provider_status(provider=Depends(get_provider)) -> dict:
    health: dict
    try:
        health = await provider.health()
    except Exception as exc:  # motor fora do ar e diagnostico, nao falha de rota
        health = {"ok": False, "error": str(exc)}
    return {"name": provider.name, "required_vram_mb": provider.required_vram_mb, "health": health, "capabilities": provider.capabilities()}


@router.get("/diagnostics")
async def diagnostics(settings: Settings = Depends(get_settings), provider=Depends(get_provider)) -> dict:
    try:
        provider_health = await provider.health()
    except Exception as exc:
        provider_health = {"ok": False, "error": str(exc)}
    gpus = gpu_snapshot()
    base_url = getattr(getattr(provider, "cfg", None), "base_url", None)
    evidence = gpu_evidence(base_url)
    # Etapa 8.1: residencia CLASSIFICADA — "confirmada" (evidencia direta; nenhuma API
    # consultada a expoe hoje, logo nunca afirmada), "provavel" (heuristica: sessao,
    # processo do motor ou historico do proprio ComfyUI) e "desconhecida" (estimativa a frio).
    residency = "residencia dos pesos nao confirmada nesta sessao"
    residency_tier = "desconhecida"
    warm_fn = getattr(provider, "warm_evidence", None)
    if callable(warm_fn):
        try:
            warm_info = await warm_fn(comfyui_vram_mb=evidence.get("comfyui_vram_mb"))
        except Exception:  # diagnostico nunca falha por causa da heuristica de residencia
            warm_info = None
        if isinstance(warm_info, dict):
            residency = str(warm_info.get("basis") or residency)
            residency_tier = str(warm_info.get("residency") or residency_tier)
    elif bool(getattr(provider, "_model_warm", False)):
        residency = "pesos provavelmente residentes (geracao anterior desta sessao do backend)"
        residency_tier = "provavel"
    if evidence.get("comfyui_vram_mb") is not None:
        residency += (
            f"; processo do motor (pid {evidence['comfyui_pid']}) usando cerca de "
            f"{evidence['comfyui_vram_mb']} MB de VRAM (leitura do driver)"
        )
    else:
        residency += f"; {evidence.get('note') or 'memoria por processo nao identificada'}"
    return {
        "app": {"name": settings.app.name, "host": settings.app.host, "port": settings.app.port, "version": _VERSION},
        "provider": {"name": provider.name, "health": provider_health, "capabilities": provider.capabilities()},
        "gpu": {
            "gpus": gpus,
            "processes": evidence.get("processes"),
            "motor_pid": evidence.get("comfyui_pid"),
            "motor_vram_mb": evidence.get("comfyui_vram_mb"),
            "note": evidence.get("note", ""),
        },
        "gpu_preflight": {"enabled": settings.gpu.enabled, "min_free_vram_mb": settings.gpu.min_free_vram_mb},
        "model_residency": {
            "estimate": residency,
            "tier": residency_tier,
            "disclaimer": (
                "taxonomia: 'confirmada' = evidencia direta do worker (nenhuma API consultada a "
                "expoe hoje — nunca afirmada); 'provavel' = heuristica (sessao/processo/historico "
                "do proprio motor); 'desconhecida' = estimativa a frio. Estimativa com evidencia "
                "do worker; nao e medicao."
            ),
        },
    }


class EngineFreeRequest(BaseModel):
    confirm: bool = False


@router.post("/system/engine/free")
async def free_engine_memory(body: EngineFreeRequest, provider=Depends(get_provider)) -> dict:
    """Liberacao CONTROLADA da memoria do motor (POST /free no ComfyUI, Etapa 8.1).

    So executa quando (1) a instancia e DEDICADA ao ImageCore AI (comfyui.dedicated no
    config — fila vazia nao prova exclusividade), (2) ha confirmacao explicita (confirm=true)
    e (3) a fila do ComfyUI esta comprovadamente vazia — nenhum trabalho em execucao ou
    pendente (inclusive de outras aplicacoes) pode ser afetado. Sem a dedicacao declarada a
    acao fica indisponivel com explicacao objetiva. Nunca e chamada automaticamente pelo
    ImageCore; descarrega os modelos residentes e a proxima geracao recarrega os pesos."""
    free_fn = getattr(provider, "controlled_free", None)
    if not callable(free_fn):
        raise ConflictError("O motor atual nao expoe liberacao de memoria controlada.", code="free_not_supported")
    try:
        return await free_fn(confirm=bool(body.confirm))
    except GenerationUnavailableError:
        raise  # 503 com a mensagem padrao de motor inacessivel
    except GenerationError as exc:
        # 409: recusa controlada (sem confirmacao/fila ocupada); codigo estavel vem do provider.
        raise ConflictError(str(exc), code=getattr(exc, "code", None) or "free_refused") from exc
