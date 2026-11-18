"""Projetos de thumbnail editaveis (Etapa 5): estado de camadas/fundo/composicao em SQLite.

Tabelas criadas por migracao aditiva (backend/storage/database.py); o historico de geracoes
fica intocado. Padroes herdados de gallery.py: ids hex, escrita atomica, JSON nas colunas
de texto e remocao de arquivos apenas dentro da raiz autorizada (safe_fs).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.core.errors import ValidationError
from backend.storage.gallery import NotFoundError
from backend.storage.safe_fs import safe_unlink_within

_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_LAYERS = 5
_LAYER_JSON_LIMIT = 16_000  # bytes por camada
_MAP_JSON_LIMIT = 32_000  # bytes por background/canvas/params
_NAME_MAX = 120

_UPDATABLE = {"name", "generation_id", "reference_id", "preset", "width", "height",
              "background", "layers", "canvas", "params"}


def validate_project_id(value: str) -> str:
    if not _ID_RE.match(value or ""):
        raise NotFoundError("Identificador de projeto invalido.", code="project_id_invalid")
    return value


def validate_layers(layers: list) -> list:
    """Fronteira de confianca: quantidade e tamanho limitados; conteudo e opaco ao servidor."""
    if not isinstance(layers, list):
        raise ValidationError("Camadas devem ser uma lista.", code="invalid_layers")
    if len(layers) > MAX_LAYERS:
        raise ValidationError(f"Maximo de {MAX_LAYERS} camadas de texto.", code="too_many_layers", details={"limit": MAX_LAYERS})
    for layer in layers:
        if not isinstance(layer, dict):
            raise ValidationError("Cada camada deve ser um objeto.", code="invalid_layer")
        if len(json.dumps(layer, ensure_ascii=False).encode("utf-8")) > _LAYER_JSON_LIMIT:
            raise ValidationError("Camada de texto acima do limite de tamanho.", code="layer_too_large")
        text = layer.get("text")
        if text is not None and not isinstance(text, str):
            raise ValidationError("Texto da camada deve ser string.", code="invalid_layer")
    return layers


def _json_map(value: dict, label: str) -> str:
    if not isinstance(value, dict):
        raise ValidationError(f"{label} deve ser um objeto.", code="invalid_field", details={"field": label})
    encoded = json.dumps(value, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > _MAP_JSON_LIMIT:
        raise ValidationError(f"{label} acima do limite de tamanho.", code="field_too_large", details={"field": label})
    return encoded


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
    for key in ("background", "canvas", "params"):
        try:
            data[key] = json.loads(data.get(key) or "{}")
        except json.JSONDecodeError:
            data[key] = {}
    try:
        data["layers"] = json.loads(data.get("layers") or "[]")
    except json.JSONDecodeError:
        data["layers"] = []
    return data


class ProjectRepository:
    def __init__(self, db, files_root: Path):
        self.db = db
        self.files_root = Path(files_root)

    # ------------------------------------------------------------- projetos

    def create(
        self,
        *,
        name: str,
        generation_id: str | None,
        reference_id: str | None,
        preset: str | None,
        width: int,
        height: int,
        background: dict,
        layers: list,
        canvas: dict,
        params: dict,
    ) -> dict:
        project_id = uuid.uuid4().hex
        ts = _now()
        self.db.write(
            "INSERT INTO thumbnail_projects (id, name, generation_id, reference_id, preset, width, height, "
            "background, layers, canvas, params, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id, name[:_NAME_MAX], generation_id, reference_id, preset, int(width), int(height),
                _json_map(background, "Fundo"), json.dumps(validate_layers(layers), ensure_ascii=False),
                _json_map(canvas, "Composicao"), _json_map(params, "Parametros"), ts, ts,
            ),
        )
        return self.get(project_id)  # type: ignore[return-value]

    def update(self, project_id: str, **fields) -> None:
        validate_project_id(project_id)
        sets, params = ["updated_at = ?"], [_now()]
        for key, value in fields.items():
            if key not in _UPDATABLE or value is None:
                continue
            if key == "layers":
                value = json.dumps(validate_layers(value), ensure_ascii=False)
            elif key in ("background", "canvas", "params"):
                value = _json_map(value, "Fundo" if key == "background" else ("Composicao" if key == "canvas" else "Parametros"))
            elif key == "name":
                value = str(value)[:_NAME_MAX]
            elif key in ("width", "height"):
                value = int(value)
            sets.append(f"{key} = ?")
            params.append(value)
        if len(sets) == 1:
            return
        params.append(project_id)
        cursor = self.db.write(f"UPDATE thumbnail_projects SET {', '.join(sets)} WHERE id = ?", tuple(params))
        if cursor.rowcount == 0:
            raise NotFoundError("Projeto nao encontrado.", code="project_not_found")

    def get(self, project_id: str) -> dict | None:
        validate_project_id(project_id)
        row = self.db.read_one("SELECT * FROM thumbnail_projects WHERE id = ?", (project_id,))
        if row is None:
            return None
        data = _row_to_dict(row)
        data["images"] = self.images_for(project_id)
        # Etapa 6: aponta a copia independente do fundo (sobrevive a exclusao da origem).
        copies = [img for img in data["images"] if img["kind"] == "background"]
        if copies and isinstance(data.get("background"), dict):
            data["background"]["copy_image_id"] = copies[-1]["id"]
        return data

    def clear_links(self, project_id: str, *, generation: bool = False, reference: bool = False) -> None:
        """Anula vinculos que apontam para origens inexistentes (a copia do fundo permanece)."""
        validate_project_id(project_id)
        sets = []
        if generation:
            sets.append("generation_id = NULL")
        if reference:
            sets.append("reference_id = NULL")
        if not sets:
            return
        self.db.write(
            f"UPDATE thumbnail_projects SET {', '.join(sets)}, updated_at = ? WHERE id = ?",
            (_now(), project_id),
        )

    def list(self, *, limit: int = 50, offset: int = 0) -> list[dict]:
        params: list = [max(0, min(200, limit)), max(0, offset)]
        rows = self.db.read_all(
            "SELECT * FROM thumbnail_projects ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
            tuple(params),
        )
        items = [_row_to_dict(row) for row in rows]
        if items:
            ids = [item["id"] for item in items]
            placeholders = ",".join("?" * len(ids))
            by_project: dict[str, list[dict]] = {}
            for image_row in self.db.read_all(
                f"SELECT * FROM thumbnail_project_images WHERE project_id IN ({placeholders}) ORDER BY created_at, id",
                tuple(ids),
            ):
                by_project.setdefault(image_row["project_id"], []).append(dict(image_row))
            for item in items:
                item["images"] = by_project.get(item["id"], [])
        return items

    def delete(self, project_id: str) -> list[str]:
        """Remove o registro (imagens por CASCADE) e devolve os caminhos relativos para limpeza."""
        validate_project_id(project_id)
        paths = [row["rel_path"] for row in self.db.read_all(
            "SELECT rel_path FROM thumbnail_project_images WHERE project_id = ?", (project_id,)
        )]
        cursor = self.db.write("DELETE FROM thumbnail_projects WHERE id = ?", (project_id,))
        if cursor.rowcount == 0:
            raise NotFoundError("Projeto nao encontrado.")
        return paths

    def delete_file(self, rel_path: str) -> None:
        try:
            safe_unlink_within(self.files_root / rel_path, self.files_root)
        except PermissionError:
            pass  # caminho fora do padrao: nunca apagar fora da raiz autorizada

    # ------------------------------------------------------------- imagens (preview/export)

    def add_image(
        self,
        *,
        project_id: str,
        kind: str,
        mime: str,
        ext: str,
        data: bytes,
        width: int,
        height: int,
    ) -> dict:
        validate_project_id(project_id)
        if kind not in ("export", "preview", "background"):
            raise ValidationError("Tipo de imagem de projeto invalido.", code="invalid_image_kind")
        image_id = uuid.uuid4().hex
        rel_path = f"projects/{project_id}/{image_id}.{ext}"
        _atomic_write(self.files_root / rel_path, data)
        row = {
            "id": image_id,
            "project_id": project_id,
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
        self.db.write(
            "INSERT INTO thumbnail_project_images (id, project_id, kind, mime, ext, sha256, size_bytes, width, height, rel_path, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"], row["project_id"], row["kind"], row["mime"], row["ext"], row["sha256"],
                row["size_bytes"], row["width"], row["height"], row["rel_path"], row["created_at"],
            ),
        )
        return row

    def get_image(self, image_id: str) -> dict | None:
        if not _ID_RE.match(image_id or ""):
            raise NotFoundError("Identificador de imagem invalido.", code="image_id_invalid")
        row = self.db.read_one("SELECT * FROM thumbnail_project_images WHERE id = ?", (image_id,))
        return dict(row) if row else None

    def images_for(self, project_id: str) -> list[dict]:
        return [dict(row) for row in self.db.read_all(
            "SELECT * FROM thumbnail_project_images WHERE project_id = ? ORDER BY created_at, id", (project_id,)
        )]

    def delete_image(self, image_id: str) -> str:
        row = self.get_image(image_id)
        if row is None:
            raise NotFoundError("Imagem nao encontrada.", code="image_not_found")
        self.db.write("DELETE FROM thumbnail_project_images WHERE id = ?", (image_id,))
        return row["rel_path"]


__all__ = ["MAX_LAYERS", "ProjectRepository", "validate_layers", "validate_project_id"]
