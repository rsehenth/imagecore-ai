"""Armazenamento das imagens de referencia enviadas pelo usuario.

Pipeline: triagem por bytes (magic bytes, tamanho — MIME/extensao do cliente sao ignorados) ->
normalizacao isolada (backend/storage/image_sandbox.py) -> gravacao atomica -> registro em SQLite.
O cliente nunca informa caminho; o nome de disco e sempre gerado pelo servidor.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.core.config import UploadSettings
from backend.storage.database import Database
from backend.storage.image_sandbox import SandboxRejected, run_image_job
from backend.storage.safe_fs import safe_unlink_within, validate_safe_target

_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def sanitize_display_name(raw: str) -> str:
    """Nome apenas para exibicao: nunca vira caminho."""
    base = re.split(r"[\\/]", raw or "")[-1]
    clean = re.sub(r"[^\w .()\-]", "_", base, flags=re.UNICODE).strip(" .")
    return (clean[:60] or "imagem")


def sniff_image_format(data: bytes) -> str | None:
    """Assinatura real dos bytes; o MIME declarado pelo cliente e ignorado por design."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    return None


class ReferenceRejected(Exception):
    """Referencia recusada. `code`: identificador estavel para i18n (backend/core/errors.py)."""

    def __init__(self, message: str, status: int = 400, code: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.details = details


def validate_reference_id(value: str) -> str:
    if not _ID_RE.match(value or ""):
        raise ReferenceRejected("Identificador de referencia invalido.", 400, code="reference_id_invalid")
    return value


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ReferenceStore:
    def __init__(self, db: Database, base_dir: Path, cfg: UploadSettings):
        self.db = db
        self.base_dir = Path(base_dir)
        self.cfg = cfg

    def save(self, data: bytes, display_name: str) -> dict:
        if not data:
            raise ReferenceRejected("Arquivo vazio.", 415, code="reference_empty")
        limit = self.cfg.max_image_bytes
        if len(data) > limit:
            raise ReferenceRejected(
                f"Imagem acima do limite de {limit // (1024 * 1024)} MB.", 413,
                code="reference_too_large", details={"limit_mb": limit // (1024 * 1024)},
            )
        fmt = sniff_image_format(data)
        if fmt is None:
            raise ReferenceRejected("O arquivo nao e uma imagem PNG, JPEG ou WebP valida.", 415, code="reference_invalid_image")
        if self.count() >= self.cfg.max_pending:
            raise ReferenceRejected("Limite de referencias pendentes atingido; remova algumas antes de enviar.", 429, code="reference_limit_reached")

        meta, clean = run_image_job(
            "normalize",
            data,
            {
                "max_side": self.cfg.max_image_side,
                "max_pixels": self.cfg.max_image_pixels,
            },
            timeout=self.cfg.processing_timeout_seconds,
            memory_limit=self.cfg.processing_memory_mb * 1024 * 1024,
            max_output=limit,
        )

        ref_id = uuid.uuid4().hex
        ext = meta["ext"]
        rel_path = f"references/{ref_id}.{ext}"
        dst = self.base_dir / rel_path
        _atomic_write(dst, clean)

        row = {
            "id": ref_id,
            "display_name": sanitize_display_name(display_name),
            "mime": meta["mime"],
            "ext": ext,
            "sha256": hashlib.sha256(clean).hexdigest(),
            "size_bytes": len(clean),
            "width": int(meta["width"]),
            "height": int(meta["height"]),
            "rel_path": rel_path,
            "created_at": _now(),
        }
        self.db.write(
            "INSERT INTO reference_images (id, display_name, mime, ext, sha256, size_bytes, width, height, rel_path, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"], row["display_name"], row["mime"], row["ext"], row["sha256"],
                row["size_bytes"], row["width"], row["height"], row["rel_path"], row["created_at"],
            ),
        )
        return row

    def get(self, ref_id: str) -> dict | None:
        validate_reference_id(ref_id)
        row = self.db.read_one("SELECT * FROM reference_images WHERE id = ?", (ref_id,))
        return dict(row) if row else None

    def get_many(self, ref_ids: list[str]) -> list[dict]:
        rows: list[dict] = []
        for ref_id in ref_ids:
            row = self.get(ref_id)
            if row is None:
                raise ReferenceRejected(f"Referencia nao encontrada: {ref_id}", 404, code="reference_not_found")
            rows.append(row)
        return rows

    def count(self) -> int:
        row = self.db.read_one("SELECT COUNT(*) AS n FROM reference_images")
        return int(row["n"]) if row else 0

    def path_of(self, row: dict) -> Path:
        try:
            return validate_safe_target(self.base_dir / row["rel_path"], self.base_dir)
        except PermissionError as exc:
            raise ReferenceRejected(f"Caminho da referencia invalido: {exc}", 403, code="reference_path_invalid") from exc

    def read_verified(self, ref_id: str) -> bytes:
        row = self.get(ref_id)
        if row is None:
            raise ReferenceRejected("Referencia nao encontrada.", 404, code="reference_not_found")
        data = self.path_of(row).read_bytes()
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ReferenceRejected("Integridade da referencia comprometida.", 409, code="reference_integrity")
        return data

    def delete(self, ref_id: str) -> bool:
        row = self.get(ref_id)
        if row is None:
            return False
        try:
            safe_unlink_within(self.base_dir / row["rel_path"], self.base_dir)
        except PermissionError:
            pass  # arquivo fora do padrao: o registro ainda e removido
        self.db.write("DELETE FROM reference_images WHERE id = ?", (ref_id,))
        return True


__all__ = [
    "ReferenceRejected",
    "ReferenceStore",
    "SandboxRejected",
    "sanitize_display_name",
    "sniff_image_format",
    "validate_reference_id",
]
