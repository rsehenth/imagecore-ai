"""Projetos de thumbnail (Etapa 5): salvar/reabrir estado editavel, preview e exportacao.

A exportacao recebe a composicao renderizada pelo proprio editor (PNG sem perdas, fiel ao
preview) e o servidor valida/normaliza em sandbox isolado, convertendo para o formato pedido
(com controle de qualidade JPEG) antes de registrar. Mesmos limites e padroes de seguranca
das rotas de geracoes: streaming com teto, magic bytes, sha256 e caminhos dentro da raiz.
"""

import asyncio
import base64
import binascii
import hashlib
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.api.deps import get_projects, get_repo, get_settings, get_uploads
from backend.core.config import Settings
from backend.core.errors import ValidationError
from backend.core.presets import MAX_SIDE
from backend.storage.gallery import GenerationRepository, NotFoundError
from backend.storage.image_sandbox import run_image_job
from backend.storage.projects import MAX_LAYERS, ProjectRepository, validate_project_id
from backend.storage.safe_fs import safe_rmdir_if_empty, validate_safe_target
from backend.storage.uploads import ReferenceRejected, ReferenceStore, sniff_image_format

router = APIRouter(tags=["projects"])

_EXPORT_MAX_BYTES = 64 * 1024 * 1024
_PREVIEW_MAX_BYTES = 4 * 1024 * 1024


class ProjectSaveRequest(BaseModel):
    name: str = Field(default="Sem titulo", max_length=120)
    generation_id: str | None = Field(default=None, min_length=32, max_length=32)
    reference_id: str | None = Field(default=None, min_length=32, max_length=32)
    preset: str | None = Field(default=None, max_length=64)
    width: int = Field(ge=64, le=MAX_SIDE)
    height: int = Field(ge=64, le=MAX_SIDE)
    background: dict = Field(default_factory=dict)
    layers: list[dict] = Field(default_factory=list, max_length=MAX_LAYERS)
    canvas: dict = Field(default_factory=dict)
    params: dict = Field(default_factory=dict)
    # Data URL PNG (render reduzido do proprio editor) para a lista de projetos.
    preview: str | None = Field(default=None, max_length=(_PREVIEW_MAX_BYTES // 3) * 4 + 64)


def _validate_dims(width: int, height: int, settings: Settings) -> None:
    if width < 64 or height < 64 or max(width, height) > MAX_SIDE:
        raise ValidationError(
            f"Dimensoes devem ficar entre 64 e {MAX_SIDE} px.",
            code="invalid_dimensions", details={"min": 64, "max": MAX_SIDE},
        )
    if width * height > settings.generation.max_output_pixels:
        raise ValidationError(
            f"Dimensoes {width}x{height} excedem o limite de ~{settings.generation.max_output_pixels // 1_000_000} MP.",
            code="resolution_limit_exceeded",
            details={"width": width, "height": height, "limit_mp": settings.generation.max_output_pixels // 1_000_000},
        )


def _check_links(body: ProjectSaveRequest, repo: GenerationRepository, uploads: ReferenceStore) -> None:
    if body.generation_id and repo.get(body.generation_id) is None:
        raise NotFoundError("Geracao vinculada nao encontrada.", code="linked_generation_not_found")
    if body.reference_id and uploads.get(body.reference_id) is None:
        raise NotFoundError("Referencia vinculada nao encontrada.", code="linked_reference_not_found")


async def _save_background_copy(
    projects: ProjectRepository,
    repo: GenerationRepository,
    uploads: ReferenceStore,
    project_id: str,
    background: dict,
) -> None:
    """Etapa 6: copia independente e verificada da imagem de fundo para o armazenamento do
    projeto. O projeto continua editavel mesmo se a geracao/referencia original for excluida
    (a copia nunca substitui nem altera o arquivo original)."""
    if not isinstance(background, dict):
        return
    source = background.get("source")
    try:
        if source == "generation":
            generation_id = background.get("generation_id") or ""
            image_id = background.get("image_id") or ""
            row = repo.get_image(image_id) if image_id else None
            if row is None or row["generation_id"] != generation_id:
                return  # origem ja ausente: projeto segue com copia anterior (ou sem fundo)
            path = validate_safe_target(repo.files_root / row["rel_path"], repo.files_root)
            data = await asyncio.to_thread(path.read_bytes)
            if hashlib.sha256(data).hexdigest() != row["sha256"]:
                return  # integridade comprometida: nao propaga; mantem a copia anterior
            meta = {"mime": row["mime"], "ext": row["ext"], "width": row["width"], "height": row["height"]}
        elif source == "reference":
            reference_id = background.get("reference_id") or ""
            ref = uploads.get(reference_id)
            if ref is None:
                return
            data = await asyncio.to_thread(uploads.read_verified, reference_id)
            meta = {"mime": ref["mime"], "ext": ref["ext"], "width": ref["width"], "height": ref["height"]}
        else:
            return
    except (PermissionError, OSError, NotFoundError, ReferenceRejected):
        return  # leitura indisponivel: nunca impede o salvamento do projeto
    projects.add_image(
        project_id=project_id,
        kind="background", mime=meta["mime"], ext=meta["ext"], data=data,
        width=meta["width"], height=meta["height"],
    )
    # Mantem apenas a copia mais recente (o salvamento substitui, nunca acumula).
    for old in [img for img in projects.images_for(project_id) if img["kind"] == "background"][:-1]:
        projects.delete_image(old["id"])
        projects.delete_file(old["rel_path"])


def _decode_preview(data_url: str) -> bytes:
    if not data_url.startswith("data:image/png;base64,"):
        raise ValidationError("Preview deve ser um PNG em data URL base64.", code="invalid_preview")
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1], validate=True)
    except (binascii.Error, ValueError, IndexError) as exc:
        raise ValidationError("Preview base64 invalido.", code="invalid_preview") from exc
    if not raw:
        raise ValidationError("Preview vazio.", code="invalid_preview")
    if len(raw) > _PREVIEW_MAX_BYTES:
        raise ValidationError("Preview acima de 4 MB.", code="invalid_preview")
    if sniff_image_format(raw) != "PNG":
        raise ValidationError("Preview deve ser PNG.", code="invalid_preview")
    return raw


async def _save_preview(projects: ProjectRepository, project_id: str, preview: str | None, settings: Settings) -> None:
    if not preview:
        return
    raw = _decode_preview(preview)
    meta, clean = await asyncio.to_thread(
        run_image_job,
        "normalize",
        raw,
        {"max_side": 2048, "max_pixels": 4_000_000},
        timeout=30.0,
        memory_limit=512 * 1024 * 1024,
        max_output=_PREVIEW_MAX_BYTES,
    )
    projects.add_image(
        project_id=project_id, kind="preview", mime=meta["mime"], ext=meta["ext"],
        data=clean, width=meta["width"], height=meta["height"],
    )
    # Mantem apenas o preview mais recente (o salvamento substitui, nunca acumula).
    for old in [img for img in projects.images_for(project_id) if img["kind"] == "preview"][:-1]:
        projects.delete_image(old["id"])
        projects.delete_file(old["rel_path"])


@router.post("/projects", status_code=201)
async def create_project(
    body: ProjectSaveRequest,
    settings: Settings = Depends(get_settings),
    repo: GenerationRepository = Depends(get_repo),
    uploads: ReferenceStore = Depends(get_uploads),
    projects: ProjectRepository = Depends(get_projects),
) -> dict:
    _validate_dims(body.width, body.height, settings)
    with projects.db.transaction(immediate=True):
        _check_links(body, repo, uploads)
        project = projects.create(
            name=body.name.strip() or "Sem titulo",
            generation_id=body.generation_id,
            reference_id=body.reference_id,
            preset=body.preset,
            width=body.width,
            height=body.height,
            background=body.background,
            layers=body.layers,
            canvas=body.canvas,
            params=body.params,
        )
    await _save_preview(projects, project["id"], body.preview, settings)
    await _save_background_copy(projects, repo, uploads, project["id"], body.background)
    return {"project": projects.get(project["id"])}


@router.get("/projects")
def list_projects(
    limit: int = 24,
    offset: int = 0,
    projects: ProjectRepository = Depends(get_projects),
) -> dict:
    return {"projects": projects.list(limit=limit, offset=offset)}


@router.get("/projects/{project_id}")
def get_project(project_id: str, projects: ProjectRepository = Depends(get_projects)) -> dict:
    project = projects.get(project_id)
    if project is None:
        raise NotFoundError("Projeto nao encontrado.", code="project_not_found")
    return {"project": project}


@router.put("/projects/{project_id}")
async def update_project(
    project_id: str,
    body: ProjectSaveRequest,
    settings: Settings = Depends(get_settings),
    repo: GenerationRepository = Depends(get_repo),
    uploads: ReferenceStore = Depends(get_uploads),
    projects: ProjectRepository = Depends(get_projects),
) -> dict:
    validate_project_id(project_id)
    if projects.get(project_id) is None:
        raise NotFoundError("Projeto nao encontrado.", code="project_not_found")
    _validate_dims(body.width, body.height, settings)
    # Atualizacao tolera vinculos orfaos (origem excluida depois do salvamento): sao
    # anulados e a copia independente do fundo preserva a edicao. Criacao continua
    # rejeitando vinculos inexistentes ( _check_links ).
    with projects.db.transaction(immediate=True):
        stale_generation = bool(body.generation_id) and repo.get(body.generation_id) is None
        stale_reference = bool(body.reference_id) and uploads.get(body.reference_id) is None
        projects.update(
            project_id,
            name=body.name.strip() or "Sem titulo",
            generation_id=body.generation_id,
            reference_id=body.reference_id,
            preset=body.preset,
            width=body.width,
            height=body.height,
            background=body.background,
            layers=body.layers,
            canvas=body.canvas,
            params=body.params,
        )
        if stale_generation or stale_reference:
            projects.clear_links(project_id, generation=stale_generation, reference=stale_reference)
    await _save_preview(projects, project_id, body.preview, settings)
    await _save_background_copy(projects, repo, uploads, project_id, body.background)
    return {"project": projects.get(project_id)}


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, projects: ProjectRepository = Depends(get_projects)) -> dict:
    paths = projects.delete(project_id)
    for rel_path in paths:
        projects.delete_file(rel_path)
    safe_rmdir_if_empty(projects.files_root / "projects" / project_id, projects.files_root)
    return {"deleted": True, "id": project_id}


@router.get("/projects/{project_id}/images/{image_id}/content")
def get_project_image_content(
    project_id: str, image_id: str, projects: ProjectRepository = Depends(get_projects)
) -> Response:
    project = projects.get(project_id)
    if project is None:
        raise NotFoundError("Projeto nao encontrado.", code="project_not_found")
    row = projects.get_image(image_id)
    if row is None or row["project_id"] != project_id:
        raise NotFoundError("Imagem nao encontrada neste projeto.", code="image_not_found")
    try:
        path = validate_safe_target(projects.files_root / row["rel_path"], projects.files_root)
    except PermissionError as exc:
        raise NotFoundError("Arquivo da imagem invalido.", code="image_file_invalid") from exc
    if not path.is_file():
        raise NotFoundError("Arquivo da imagem nao encontrado.", code="image_file_missing")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise NotFoundError("Integridade da imagem comprometida.", code="image_integrity")
    return Response(
        content=data,
        media_type=row["mime"],
        headers={
            "Content-Disposition": "inline",
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/projects/{project_id}/export", status_code=201)
async def export_project(
    project_id: str,
    request: Request,
    format: Literal["png", "jpeg"] = Query(default="png"),
    quality: int = Query(default=92, ge=1, le=100),
    width: int | None = Query(default=None, ge=64, le=MAX_SIDE),
    height: int | None = Query(default=None, ge=64, le=MAX_SIDE),
    settings: Settings = Depends(get_settings),
    projects: ProjectRepository = Depends(get_projects),
) -> dict:
    """Exporta a composicao WYSIWYG: o editor envia PNG sem perdas; o servidor converte
    (Lanczos quando as dimensoes pedidas diferem) com qualidade JPEG controlada."""
    project = projects.get(project_id)
    if project is None:
        raise NotFoundError("Projeto nao encontrado.", code="project_not_found")
    final_width = width or int(project["width"])
    final_height = height or int(project["height"])
    _validate_dims(final_width, final_height, settings)

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _EXPORT_MAX_BYTES:
        raise ValidationError("Composicao acima do limite de 64 MB.", code="composition_too_large", details={"limit_mb": 64})
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > _EXPORT_MAX_BYTES:
            raise ValidationError("Composicao acima do limite de 64 MB.", code="composition_too_large", details={"limit_mb": 64})
    if not buf or sniff_image_format(bytes(buf[:16])) != "PNG":
        raise ValidationError("A composicao enviada deve ser um PNG valido.", code="invalid_composition")

    meta, out = await asyncio.to_thread(
        run_image_job,
        "resize",
        bytes(buf),
        {
            "width": final_width,
            "height": final_height,
            "format": format,
            "quality": quality,
            "max_side": MAX_SIDE,
            "max_pixels": settings.generation.max_output_pixels,
        },
        timeout=60.0,
        memory_limit=1536 * 1024 * 1024,
        max_output=_EXPORT_MAX_BYTES,
    )
    image = projects.add_image(
        project_id=project_id, kind="export", mime=meta["mime"], ext=meta["ext"],
        data=out, width=meta["width"], height=meta["height"],
    )
    return {"image": image}


__all__ = ["router"]
