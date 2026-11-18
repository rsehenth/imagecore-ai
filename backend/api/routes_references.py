"""Upload, visualizacao e remocao de imagens de referencia (bytes crus; MIME do cliente ignorado)."""

import asyncio
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import Response

from backend.api.deps import get_uploads
from backend.storage.uploads import ReferenceRejected, ReferenceStore, validate_reference_id

router = APIRouter(tags=["references"])


@router.post("/references", status_code=201)
async def upload_reference(
    request: Request,
    x_filename: str = Header(default="imagem.png", min_length=1, max_length=400),
    store: ReferenceStore = Depends(get_uploads),
) -> dict:
    """Recebe uma imagem (PNG, JPEG ou WebP), valida/normaliza em subprocesso isolado e guarda."""
    limit = store.cfg.max_image_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise ReferenceRejected(
            f"Imagem acima do limite de {limit // (1024 * 1024)} MB.", 413,
            code="reference_too_large", details={"limit_mb": limit // (1024 * 1024)},
        )
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise ReferenceRejected(
                f"Imagem acima do limite de {limit // (1024 * 1024)} MB.", 413,
                code="reference_too_large", details={"limit_mb": limit // (1024 * 1024)},
            )
    reference = await asyncio.to_thread(store.save, bytes(buf), unquote(x_filename))
    return {"reference": reference}


@router.get("/references/{ref_id}")
def get_reference(ref_id: str, store: ReferenceStore = Depends(get_uploads)) -> Response:
    validate_reference_id(ref_id)
    row = store.get(ref_id)
    if row is None:
        raise ReferenceRejected("Referencia nao encontrada.", 404, code="reference_not_found")
    data = store.read_verified(ref_id)
    return Response(
        content=data,
        media_type=row["mime"],
        headers={
            "Content-Disposition": "inline",
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/references/{ref_id}")
def delete_reference(ref_id: str, store: ReferenceStore = Depends(get_uploads)) -> dict:
    if not store.delete(ref_id):
        raise ReferenceRejected("Referencia nao encontrada.", 404, code="reference_not_found")
    return {"deleted": True, "id": ref_id}
