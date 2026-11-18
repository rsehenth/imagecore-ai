"""Geracoes: criar job, acompanhar, cancelar, listar (galeria), exportar e servir imagens."""

import asyncio
import random
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.api.deps import get_jobs, get_provider, get_repo, get_settings, get_uploads
from backend.core.config import Settings
from backend.core.errors import ValidationError
from backend.core.presets import (
    CFG_RANGE,
    DENOISE_RANGE,
    I2I_DEFAULT_DENOISE,
    MAX_SIDE,
    PLATFORM_PRESETS,
    QUALITY_PRESETS,
    SAMPLERS,
    STEPS_RANGE,
    STYLE_LABELS,
    STYLE_SUFFIXES,
    dimensions_for_tier,
    i2i_latent_dims,
    sdxl_bucket_for,
    style_suffix,
)
from backend.generation.base import GenerationError, ImageProvider
from backend.generation.profiles import PROFILES, assert_runnable, get_profile, validate_request_params
from backend.jobs.manager import GenerationJobManager
from backend.storage.gallery import GALLERY_DELETE_LIMIT, ConflictError, GenerationRepository, NotFoundError, validate_generation_id
from backend.storage.image_sandbox import run_image_job
from backend.storage.safe_fs import validate_safe_target
from backend.storage.uploads import ReferenceStore

router = APIRouter(tags=["generations"])

_EXPORT_MAX_BYTES = 64 * 1024 * 1024
_FORMATS = {"png": ("image/png", "png"), "jpeg": ("image/jpeg", "jpg")}

_PRESET_BY_KEY = {preset["key"]: preset for preset in PLATFORM_PRESETS}


class GenerationRequest(BaseModel):
    module: Literal["thumbnail", "image"]
    prompt: str = Field(min_length=1, max_length=8000)
    negative_prompt: str | None = Field(default=None, max_length=2000)
    style: str = "none"
    mode: Literal["t2i", "i2i"] = "t2i"  # texto->imagem | imagem->imagem (1 referencia)
    denoise: float | None = Field(default=None, ge=0.0, le=1.0)
    quality: Literal["rapida", "padrao", "alta"] = "padrao"
    steps: int | None = Field(default=None, ge=1, le=200)
    cfg: float | None = Field(default=None, ge=0.0, le=30.0)
    sampler: str | None = Field(default=None, max_length=32)
    preset: str | None = Field(default=None, max_length=64)
    ratio: str | None = Field(default=None, max_length=8)
    tier: str | None = Field(default=None, max_length=8)
    width: int | None = Field(default=None, ge=64, le=MAX_SIDE)
    height: int | None = Field(default=None, ge=64, le=MAX_SIDE)
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)
    seed_mode: Literal["auto", "manual"] = "auto"
    reference_ids: list[str] = Field(default_factory=list, max_length=16)
    profile: str = "sdxl"  # Etapa 7: perfil de modelo (veja backend/generation/profiles.py)


class ExportRequest(BaseModel):
    image_id: str = Field(min_length=32, max_length=32)
    width: int | None = Field(default=None, ge=64, le=MAX_SIDE)
    height: int | None = Field(default=None, ge=64, le=MAX_SIDE)
    format: Literal["png", "jpeg"] = "png"
    quality: int | None = Field(default=None, ge=1, le=100)  # JPEG/WebP; PNG ignora


class GalleryDeleteRequest(BaseModel):
    kind: Literal["generations", "exports"]
    ids: list[str] = Field(max_length=GALLERY_DELETE_LIMIT)


@router.post("/generations", status_code=201)
async def create_generation(
    body: GenerationRequest,
    settings: Settings = Depends(get_settings),
    repo: GenerationRepository = Depends(get_repo),
    uploads: ReferenceStore = Depends(get_uploads),
    jobs: GenerationJobManager = Depends(get_jobs),
    provider: ImageProvider = Depends(get_provider),
) -> dict:
    prompt = body.prompt.strip()
    if not prompt:
        raise ValidationError("Prompt obrigatorio.", code="prompt_required")
    if len(prompt) > settings.generation.max_prompt_chars:
        raise ValidationError(
            f"Prompt acima do limite de {settings.generation.max_prompt_chars} caracteres.",
            code="prompt_too_long", details={"limit": settings.generation.max_prompt_chars},
        )

    style = (body.style or "none").strip().lower()
    if style not in STYLE_SUFFIXES:
        raise ValidationError(f"Estilo desconhecido: {style!r}.", code="unknown_style", details={"value": style})

    # Etapa 7 (perfis): o perfil define o contrato de parametros — nunca herda os do SDXL.
    profile = get_profile(body.profile or "sdxl")
    if profile is None:
        raise ValidationError(
            f"Perfil de modelo desconhecido: {body.profile!r}. Disponiveis: {', '.join(PROFILES)}.",
            code="unknown_profile", details={"value": body.profile},
        )
    validate_request_params(
        profile, steps=body.steps, cfg=body.cfg, sampler=body.sampler, negative_prompt=body.negative_prompt
    )
    # Disponibilidade honesta: um candidato sem pesos/workflow ativo e recusado com 409
    # (nunca substituido silenciosamente por outro modelo ou pela simulacao).
    if provider.name == "simulated":
        if profile.id != "sdxl":
            raise ConflictError(
                f"O motor simulado so atende o perfil 'sdxl'; '{profile.label}' foi recusado "
                "(sem substituicao silenciosa por simulacao).",
                code="simulated_profile_rejected",
            )
    else:
        try:
            assert_runnable(profile, settings.comfyui)
        except GenerationError as exc:
            raise ConflictError(str(exc), code=getattr(exc, "code", None) or "profile_not_runnable") from exc

    reference_ids = list(dict.fromkeys(body.reference_ids))
    if len(reference_ids) > settings.upload.max_references_per_job:
        raise ValidationError(
            f"Maximo de {settings.upload.max_references_per_job} referencias por geracao.",
            code="too_many_references", details={"limit": settings.upload.max_references_per_job},
        )
    caps = provider.capabilities()
    reference_rows = uploads.get_many(reference_ids) if reference_ids else []
    mode = body.mode
    if mode == "i2i":
        if not (profile.img2img and caps.get("img2img")):
            raise ValidationError(
                f"O motor atual ({provider.name}) nao suporta imagem para imagem no workflow configurado.",
                code="i2i_unsupported", details={"engine": provider.name},
            )
        if len(reference_rows) != 1:
            raise ValidationError("Imagem para imagem requer exatamente 1 imagem de referencia.", code="i2i_requires_reference")
    elif reference_rows:
        if not caps.get("references"):
            hint = (
                " Use o modo 'imagem para imagem' para transformar uma referencia."
                if caps.get("img2img")
                else ""
            )
            raise ValidationError(
                f"O motor atual ({provider.name}) nao suporta imagens de referencia no modo texto para imagem.{hint}",
                code="references_unsupported", details={"engine": provider.name},
            )
        max_refs = int(caps.get("max_references") or 0)
        if len(reference_rows) > max_refs:
            raise ValidationError(
                f"O workflow configurado aceita no maximo {max_refs} referencia(s); recebidas {len(reference_rows)}.",
                code="too_many_references", details={"limit": max_refs},
            )

    # Qualidade: os presets (rapida/padrao/alta) sao do SDXL; outros perfis usam os
    # padroes dos seus templates oficiais (ex.: Z-Image 8 passos, Klein 4 passos).
    if profile.id == "sdxl":
        steps, cfg = QUALITY_PRESETS[body.quality]["steps"], QUALITY_PRESETS[body.quality]["cfg"]
    else:
        steps, cfg = profile.steps_default, profile.cfg_default
    if body.steps is not None:
        steps = int(body.steps)  # faixa ja validada pelo contrato do perfil
    if body.cfg is not None:
        cfg = float(body.cfg)
    sampler = None
    if body.sampler:
        sampler = body.sampler.strip()
        if not profile.sampler_fixed and sampler not in SAMPLERS:
            raise ValidationError(
                f"Sampler desconhecido: {sampler!r}. Disponiveis: {', '.join(SAMPLERS)}.",
                code="unknown_sampler", details={"value": sampler, "options": ", ".join(SAMPLERS)},
            )

    export_width = export_height = None
    if mode == "i2i":
        # Latent vem da referencia: dimensoes seguras derivadas dela (orcamento de pixels do SDXL).
        reference = reference_rows[0]
        width, height = i2i_latent_dims(int(reference["width"]), int(reference["height"]))
        if body.module == "thumbnail":
            preset = _PRESET_BY_KEY.get(body.preset or "")
            if preset is None:
                raise ValidationError("Selecione um preset de plataforma valido para thumbnail.", code="unknown_preset")
            export_width, export_height = int(preset["width"]), int(preset["height"])
            preset_key, ratio, tier = preset["key"], preset["ratio"], None
        else:
            preset_key, ratio, tier = None, body.ratio, "custom"
            export_width, export_height = width, height
    elif body.module == "thumbnail":
        preset = _PRESET_BY_KEY.get(body.preset or "")
        if preset is None:
            raise ValidationError("Selecione um preset de plataforma valido para thumbnail.", code="unknown_preset")
        width, height = int(preset["width"]), int(preset["height"])
        export_width, export_height = width, height
        ratio, tier = preset["ratio"], None
        preset_key = preset["key"]
    else:
        preset_key = None
        if body.width is not None and body.height is not None:
            width, height = int(body.width), int(body.height)
            ratio, tier = body.ratio, "custom"
        elif body.ratio:
            tier = (body.tier or "1080p").strip().lower()
            width, height = dimensions_for_tier(body.ratio, tier)
            ratio = body.ratio
        else:
            raise ValidationError("Informe proporcao + resolucao ou dimensoes personalizadas.", code="missing_dimensions")
        export_width, export_height = width, height

    # Politica de resolucao: com perfis nativos (SDXL) a geracao acontece no bucket nativo da
    # proporcao; as dimensoes finais exatas sao alcancadas por redimensionamento na exportacao.
    # i2i ja usa dimensoes seguras derivadas da referencia.
    resolution_policy = "nativa"
    if mode != "i2i" and caps.get("native_profiles"):
        bucket_w, bucket_h = sdxl_bucket_for(width, height)
        resolution_policy = f"nativa ({bucket_w}x{bucket_h}) + redimensionamento para {width}x{height} na exportacao"
        width, height = bucket_w, bucket_h

    if width * height > settings.generation.max_output_pixels:
        raise ValidationError(
            f"Dimensoes {width}x{height} excedem o limite de geracao (~{settings.generation.max_output_pixels // 1_000_000} MP).",
            code="resolution_limit_exceeded",
            details={"width": width, "height": height, "limit_mp": settings.generation.max_output_pixels // 1_000_000},
        )

    denoise = None
    if mode == "i2i":
        denoise = I2I_DEFAULT_DENOISE if body.denoise is None else float(body.denoise)
        if not DENOISE_RANGE[0] <= denoise <= DENOISE_RANGE[1]:
            raise ValidationError(
                f"Denoise fora da faixa util ({DENOISE_RANGE[0]} a {DENOISE_RANGE[1]}).",
                code="invalid_denoise", details={"min": DENOISE_RANGE[0], "max": DENOISE_RANGE[1]},
            )

    if body.seed_mode == "manual":
        if body.seed is None:
            raise ValidationError("Seed obrigatoria no modo manual (0 a 4294967295).", code="seed_required")
        seed = int(body.seed)
    else:
        seed = random.randint(0, 2**32 - 1)  # modo automatico: seed nova a cada geracao
    params = {
        "mode": mode,
        "ratio": ratio,
        "tier": tier,
        "style_label": STYLE_LABELS.get(style, style),  # rotulo em pt-BR para exibicao
        "final_prompt": prompt + style_suffix(style),
        "seed_mode": body.seed_mode,
        "quality": body.quality,
        "steps": steps,
        "cfg": cfg,
        "resolution_policy": resolution_policy,
        "profile": profile.id,  # Etapa 7: modelo persistido nos metadados de cada geracao
    }
    # Negative prompt: o texto efetivo vai ao no CLIP negativo e fica registrado no
    # historico (so quando o motor realmente o aceita — honestidade do registro).
    negative_supported = bool(caps.get("negative_prompt_supported")) and profile.negative_prompt_supported
    if negative_supported:
        negative = (
            body.negative_prompt
            if body.negative_prompt is not None
            else settings.comfyui.default_negative_prompt
        ).strip()
        params["negative_prompt"] = negative
        params["final_negative_prompt"] = negative
    if sampler:
        params["sampler"] = sampler
    if denoise is not None:
        params["denoise"] = denoise
    if mode != "i2i" and export_width is not None and (export_width, export_height) != (width, height):
        params["requested"] = f"{export_width}x{export_height}"
    # Reproducao exige mesmo seed + modelo + workflow: registra o que o motor usara.
    if caps.get("workflow"):
        params["workflow"] = caps["workflow"]
    if mode == "i2i" and caps.get("workflow_i2i"):
        params["workflow"] = caps["workflow_i2i"]
    if caps.get("model"):
        params["model"] = caps["model"]
    generation = repo.create(
        module=body.module,
        prompt=prompt,
        style=style,
        preset=preset_key,
        width=width,
        height=height,
        export_width=export_width,
        export_height=export_height,
        seed=seed,
        reference_ids=reference_ids,
        provider=provider.name,
        params=params,
    )
    jobs.submit(generation["id"])
    return {"generation": generation}


@router.get("/generations")
def list_generations(
    limit: int = 50,
    offset: int = 0,
    module: str | None = None,
    status: str | None = None,
    repo: GenerationRepository = Depends(get_repo),
) -> dict:
    limit = max(1, min(200, limit))
    offset = max(0, offset)
    return {"generations": repo.list(limit=limit, offset=offset, module=module, status=status)}


@router.get("/generations/{gen_id}")
def get_generation(gen_id: str, repo: GenerationRepository = Depends(get_repo)) -> dict:
    generation = repo.get(gen_id)
    if generation is None:
        raise NotFoundError("Geracao nao encontrada.", code="generation_not_found")
    return {"generation": generation}


@router.post("/generations/{gen_id}/cancel")
def cancel_generation(gen_id: str, jobs: GenerationJobManager = Depends(get_jobs)) -> dict:
    validate_generation_id(gen_id)
    if not jobs.cancel(gen_id):
        raise ConflictError("Geracao nao esta mais em andamento.", code="generation_not_running")
    return {"cancelled": True, "id": gen_id}


@router.delete("/generations/{gen_id}")
def delete_generation(gen_id: str, repo: GenerationRepository = Depends(get_repo)) -> dict:
    result = repo.delete_many([gen_id])
    return {**result, "deleted": True, "id": gen_id}


@router.post("/gallery/bulk-delete")
def delete_gallery_items(body: GalleryDeleteRequest, repo: GenerationRepository = Depends(get_repo)) -> dict:
    return repo.delete_many(body.ids, exports=body.kind == "exports")


@router.get("/generations/{gen_id}/images/{image_id}/content")
def get_image_content(
    gen_id: str, image_id: str, repo: GenerationRepository = Depends(get_repo)
) -> Response:
    import hashlib

    generation = repo.get(gen_id)
    if generation is None:
        raise NotFoundError("Geracao nao encontrada.", code="generation_not_found")
    row = repo.get_image(image_id)
    if row is None or row["generation_id"] != gen_id:
        raise NotFoundError("Imagem nao encontrada nesta geracao.", code="image_not_found")
    try:
        path = validate_safe_target(repo.files_root / row["rel_path"], repo.files_root)
    except PermissionError as exc:
        raise NotFoundError("Arquivo da imagem invalido.", code="image_file_invalid") from exc
    if not path.is_file():
        raise NotFoundError("Arquivo da imagem nao encontrado.", code="image_file_missing")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise ConflictError("Integridade da imagem comprometida.", code="image_integrity")
    return Response(
        content=data,
        media_type=row["mime"],
        headers={
            "Content-Disposition": "inline",
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/generations/{gen_id}/export", status_code=201)
async def export_generation(
    gen_id: str,
    body: ExportRequest,
    settings: Settings = Depends(get_settings),
    repo: GenerationRepository = Depends(get_repo),
) -> dict:
    """Re-encode Lanczos da imagem original para outra resolucao/formato (upscale declarado como tal)."""
    import hashlib

    generation = repo.get(gen_id)
    if generation is None:
        raise NotFoundError("Geracao nao encontrada.", code="generation_not_found")
    row = repo.get_image(body.image_id)
    if row is None or row["generation_id"] != gen_id:
        raise NotFoundError("Imagem nao encontrada nesta geracao.", code="image_not_found")
    width = body.width or int(row["width"])
    height = body.height or int(row["height"])
    if width * height > settings.generation.max_output_pixels:
        raise ValidationError(
            f"Exportacao {width}x{height} excede o limite de ~{settings.generation.max_output_pixels // 1_000_000} MP.",
            code="export_limit_exceeded",
            details={"width": width, "height": height, "limit_mp": settings.generation.max_output_pixels // 1_000_000},
        )
    try:
        path = validate_safe_target(repo.files_root / row["rel_path"], repo.files_root)
    except PermissionError as exc:
        raise NotFoundError("Arquivo da imagem invalido.", code="image_file_invalid") from exc
    if not path.is_file():
        raise NotFoundError("Arquivo da imagem nao encontrado.", code="image_file_missing")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise ConflictError("Integridade da imagem comprometida.", code="image_integrity")

    mime, ext = _FORMATS[body.format]
    resize_params: dict = {
        "width": width,
        "height": height,
        "format": body.format,
        "max_side": MAX_SIDE,
        "max_pixels": settings.generation.max_output_pixels,
    }
    if body.quality is not None:
        resize_params["quality"] = body.quality
    meta, out = await asyncio.to_thread(
        run_image_job,
        "resize",
        data,
        resize_params,
        timeout=60.0,
        memory_limit=1536 * 1024 * 1024,
        max_output=_EXPORT_MAX_BYTES,
    )
    image = repo.add_image(
        generation_id=gen_id, kind="export", mime=mime, ext=ext, data=out, width=width, height=height
    )
    return {"image": image}


@router.post("/generations/{gen_id}/composite", status_code=201)
async def upload_composite(
    gen_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
    repo: GenerationRepository = Depends(get_repo),
) -> dict:
    """Recebe a composicao final (imagem + camada de texto) montada no cliente e registra como exportacao."""
    generation = repo.get(gen_id)
    if generation is None:
        raise NotFoundError("Geracao nao encontrada.", code="generation_not_found")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _EXPORT_MAX_BYTES:
        raise ValidationError("Composicao acima do limite de 64 MB.", code="composition_too_large", details={"limit_mb": 64})
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > _EXPORT_MAX_BYTES:
            raise ValidationError("Composicao acima do limite de 64 MB.", code="composition_too_large", details={"limit_mb": 64})
    meta, clean = await asyncio.to_thread(
        run_image_job,
        "normalize",
        bytes(buf),
        {
            "max_side": MAX_SIDE,
            "max_pixels": settings.generation.max_output_pixels,
        },
        timeout=30.0,
        memory_limit=1536 * 1024 * 1024,
        max_output=_EXPORT_MAX_BYTES,
    )
    image = repo.add_image(
        generation_id=gen_id,
        kind="export",
        mime=meta["mime"],
        ext=meta["ext"],
        data=clean,
        width=meta["width"],
        height=meta["height"],
    )
    return {"image": image}
