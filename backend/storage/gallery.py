"""Repositorio de geracoes e imagens (galeria/historico) em SQLite proprio."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.core.errors import ValidationError
from backend.storage.safe_fs import safe_rmdir_if_empty, safe_unlink_within, validate_safe_target

_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_IMAGE_PATH = re.compile(r"generations/[0-9a-f]{32}/[0-9a-f]{32}\.(png|jpg|jpeg|webp)")
GALLERY_DELETE_LIMIT = 10_000
logger = logging.getLogger(__name__)


class NotFoundError(ValueError):
    """Recurso nao encontrado. `code`: identificador estavel para i18n (backend/core/errors.py)."""

    def __init__(self, message: str, code: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details


class ConflictError(ValueError):
    """Estado atual nao permite a operacao. `code`: identificador estavel para i18n."""

    def __init__(self, message: str, code: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details


def validate_generation_id(value: str) -> str:
    if not _ID_RE.fullmatch(value or ""):
        raise NotFoundError("Identificador de geracao invalido.", code="generation_id_invalid")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _row_to_dict(row) -> dict:
    data = dict(row)
    try:
        data["reference_ids"] = json.loads(data.get("reference_ids") or "[]")
    except json.JSONDecodeError:
        data["reference_ids"] = []
    try:
        data["params"] = json.loads(data.get("params") or "{}")
    except json.JSONDecodeError:
        data["params"] = {}
    return data


class GenerationRepository:
    def __init__(self, db, files_root: Path):
        self.db = db
        self.files_root = Path(files_root)

    # ------------------------------------------------------------- geracoes

    def create(
        self,
        *,
        module: str,
        prompt: str,
        style: str,
        preset: str | None,
        width: int,
        height: int,
        export_width: int | None,
        export_height: int | None,
        seed: int,
        reference_ids: list[str],
        provider: str,
        params: dict,
    ) -> dict:
        gen_id = uuid.uuid4().hex
        ts = _now()
        self.db.write(
            "INSERT INTO generations (id, module, prompt, style, preset, width, height, export_width, export_height, "
            "seed, reference_ids, provider, status, created_at, params) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
            (
                gen_id, module, prompt, style, preset, width, height, export_width, export_height,
                seed, json.dumps(reference_ids), provider, ts, json.dumps(params),
            ),
        )
        return self.get(gen_id)  # type: ignore[return-value]

    def get(self, gen_id: str) -> dict | None:
        validate_generation_id(gen_id)
        row = self.db.read_one("SELECT * FROM generations WHERE id = ?", (gen_id,))
        if row is None:
            return None
        data = _row_to_dict(row)
        data["images"] = self.images_for(gen_id)
        return data

    def list(
        self, *, limit: int = 50, offset: int = 0, module: str | None = None, status: str | None = None
    ) -> list[dict]:
        clauses, params = [], []
        if module in ("thumbnail", "image"):
            clauses.append("module = ?")
            params.append(module)
        if status in ("queued", "running", "succeeded", "failed", "cancelled"):
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        rows = self.db.read_all(
            f"SELECT * FROM generations {where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            tuple(params),
        )
        items = [_row_to_dict(row) for row in rows]
        if items:
            # imagens de todas as geracoes da pagina em uma unica consulta
            ids = [item["id"] for item in items]
            placeholders = ",".join("?" * len(ids))
            by_gen: dict[str, list[dict]] = {}
            for image_row in self.db.read_all(
                f"SELECT * FROM generation_images WHERE generation_id IN ({placeholders}) ORDER BY created_at, id",
                tuple(ids),
            ):
                by_gen.setdefault(image_row["generation_id"], []).append(dict(image_row))
            for item in items:
                item["images"] = by_gen.get(item["id"], [])
        return items

    def update(self, gen_id: str, **fields) -> None:
        validate_generation_id(gen_id)
        if not fields:
            return
        allowed = {"status", "stage", "progress", "error", "started_at", "finished_at"}
        sets, params = [], []
        for key, value in fields.items():
            if key not in allowed or value is None:
                continue
            sets.append(f"{key} = ?")
            params.append(value)
        if not sets:
            return
        params.append(gen_id)
        self.db.write(f"UPDATE generations SET {', '.join(sets)} WHERE id = ?", tuple(params))

    def set_running(self, gen_id: str) -> None:
        self.update(gen_id, status="running", stage="iniciando", progress=1, started_at=_now())

    def finish(self, gen_id: str, status: str, error: str | None = None) -> None:
        self.db.write(
            "UPDATE generations SET status = ?, error = ?, finished_at = ?, progress = CASE WHEN ? IN ('succeeded','failed','cancelled') THEN 100 ELSE progress END WHERE id = ?",
            (status, error, _now(), status, gen_id),
        )

    def set_progress(self, gen_id: str, stage: str, progress: int) -> None:
        self.db.write(
            "UPDATE generations SET stage = ?, progress = ? WHERE id = ? AND status = 'running'",
            (stage, max(0, min(100, int(progress))), gen_id),
        )

    def _deletion_path(self, rel_path: str) -> Path:
        if not _IMAGE_PATH.fullmatch(rel_path):
            raise PermissionError("Caminho fora do armazenamento da galeria.")
        return validate_safe_target(self.files_root.absolute() / rel_path, self.files_root.absolute())

    def _live_paths(self, conn) -> set[Path]:
        rows = conn.execute(
            "SELECT rel_path FROM generation_images UNION SELECT rel_path FROM reference_images "
            "UNION SELECT rel_path FROM thumbnail_project_images"
        )
        root = self.files_root.resolve()
        paths = set()
        for row in rows:
            path = Path(os.path.abspath(self.files_root / row["rel_path"]))
            if path.is_relative_to(root):
                paths.add(path.resolve())
        return paths

    def _protect_project_backgrounds(self, conn, images) -> None:
        originals = {(row["generation_id"], row["id"]): row["sha256"] for row in images}
        for project in conn.execute("SELECT id, background FROM thumbnail_projects"):
            background = json.loads(project["background"])
            if not isinstance(background, dict) or background.get("source") != "generation":
                continue
            gen_id, image_id = background.get("generation_id"), background.get("image_id")
            if not isinstance(gen_id, str) or not isinstance(image_id, str):
                continue
            digest = originals.get((gen_id, image_id))
            if digest is None:
                continue
            copy = conn.execute(
                "SELECT rel_path, sha256 FROM thumbnail_project_images WHERE project_id = ? AND kind = 'background' "
                "ORDER BY created_at DESC, id DESC LIMIT 1", (project["id"],),
            ).fetchone()
            if copy and copy["sha256"] == digest:
                try:
                    expected_dir = self.files_root.absolute() / "projects" / project["id"]
                    path = validate_safe_target(self.files_root.absolute() / copy["rel_path"], self.files_root.absolute())
                    if path.is_relative_to(expected_dir):
                        with path.open("rb") as original:
                            if hashlib.file_digest(original, "sha256").hexdigest() == digest:
                                continue
                except OSError:
                    pass
            raise ConflictError("Um projeto ainda depende da imagem original.", code="gallery_project_dependency")

    def delete_many(self, ids: list[str], *, exports: bool = False) -> dict:
        if not ids or len(ids) > GALLERY_DELETE_LIMIT or len(ids) != len(set(ids)):
            raise ValidationError("Selecao de exclusao invalida.", code="gallery_invalid_selection")
        for value in ids:
            if not _ID_RE.fullmatch(value):
                code = "image_id_invalid" if exports else "generation_id_invalid"
                raise NotFoundError("Identificador invalido.", code=code)
        selection = (json.dumps(ids),)
        table = "generation_images" if exports else "generations"
        try:
            with self.db.transaction(immediate=True) as conn:
                rows = conn.execute(f"SELECT * FROM {table} WHERE id IN (SELECT value FROM json_each(?))", selection).fetchall()
                if len(rows) != len(ids) or (exports and any(row["kind"] != "export" for row in rows)):
                    code = "image_not_found" if exports else "generation_not_found"
                    raise NotFoundError("Um ou mais itens nao foram encontrados.", code=code)
                if not exports and any(row["status"] in ("queued", "running") for row in rows):
                    raise ConflictError("Cancele as geracoes em andamento antes de excluir.", code="gallery_generation_active")
                images = rows if exports else conn.execute(
                    "SELECT * FROM generation_images WHERE generation_id IN (SELECT value FROM json_each(?))", selection,
                ).fetchall()
                self._protect_project_backgrounds(conn, images)
                candidates = {}
                for row in images:
                    path = self._deletion_path(row["rel_path"])
                    candidates[path] = (row["rel_path"], row["sha256"])
                conn.execute(f"DELETE FROM {table} WHERE id IN (SELECT value FROM json_each(?))", selection)
                live = self._live_paths(conn)
                queued = [entry for path, entry in candidates.items() if path not in live]
                conn.executemany("INSERT OR IGNORE INTO gallery_file_deletions VALUES (?, ?)", queued)
        except OSError as exc:
            raise ConflictError("Nao foi possivel validar os arquivos da galeria.", code="gallery_file_unsafe") from exc
        except (sqlite3.Error, json.JSONDecodeError) as exc:
            logger.exception("Gallery deletion transaction failed")
            raise ConflictError("Nao foi possivel excluir a selecao.", code="gallery_delete_failed") from exc

        _, pending = self.retry_file_deletions()
        return {"deleted": len(ids), "files_preserved": len(candidates) - len(queued), "pending_files": pending}

    def retry_file_deletions(self) -> tuple[int, int]:
        # The queue commits with the catalog. A crash or a locked file can only leave work to retry.
        pending = removed = 0
        try:
            with self.db.transaction(immediate=True) as conn:
                rows = conn.execute("SELECT rel_path, sha256 FROM gallery_file_deletions").fetchall()
                live = self._live_paths(conn) if rows else set()
                for row in rows:
                    rel_path = row["rel_path"]
                    try:
                        path = self._deletion_path(rel_path)
                        if path not in live and path.is_file():
                            with path.open("rb") as original:
                                if hashlib.file_digest(original, "sha256").hexdigest() != row["sha256"]:
                                    raise PermissionError("O arquivo mudou depois da exclusao.")
                            removed += safe_unlink_within(path, self.files_root)
                        if path not in live:
                            safe_rmdir_if_empty(path.parent, self.files_root)
                    except OSError:
                        logger.warning("Gallery file cleanup postponed", exc_info=True)
                        pending += 1
                        continue
                    conn.execute("DELETE FROM gallery_file_deletions WHERE rel_path = ?", (rel_path,))
        except sqlite3.Error:
            logger.exception("Gallery file cleanup queue unavailable")
            pending = max(1, pending)
        return removed, pending

    # ------------------------------------------------------------- imagens

    def add_image(
        self,
        *,
        generation_id: str,
        kind: str,
        mime: str,
        ext: str,
        data: bytes,
        width: int,
        height: int,
    ) -> dict:
        validate_generation_id(generation_id)
        image_id = uuid.uuid4().hex
        rel_path = f"generations/{generation_id}/{image_id}.{ext}"
        row = {
            "id": image_id,
            "generation_id": generation_id,
            "kind": kind,
            "mime": mime,
            "ext": ext,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "width": int(width),
            "height": int(height),
            "rel_path": rel_path,
            "created_at": _now(),
        }
        path = self._deletion_path(rel_path)
        written = False
        try:
            with self.db.transaction(immediate=True) as conn:
                if not conn.execute("SELECT 1 FROM generations WHERE id = ?", (generation_id,)).fetchone():
                    raise NotFoundError("Geracao nao encontrada.", code="generation_not_found")
                _atomic_write(path, data)
                written = True
                conn.execute(
                    "INSERT INTO generation_images (id, generation_id, kind, mime, ext, sha256, size_bytes, width, height, rel_path, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    tuple(row.values()),
                )
        except Exception:
            if written:
                safe_unlink_within(path, self.files_root)
            raise
        return row

    def get_image(self, image_id: str) -> dict | None:
        if not _ID_RE.match(image_id or ""):
            raise NotFoundError("Identificador de imagem invalido.", code="image_id_invalid")
        row = self.db.read_one("SELECT * FROM generation_images WHERE id = ?", (image_id,))
        return dict(row) if row else None

    def images_for(self, generation_id: str) -> list[dict]:
        return [dict(row) for row in self.db.read_all(
            "SELECT * FROM generation_images WHERE generation_id = ? ORDER BY created_at, id", (generation_id,)
        )]
