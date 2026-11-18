"""Acesso SQLite local (WAL) para aplicacao single-user.

Classe Database copiada de um projeto anterior do autor (backend/storage/database.py), projeto de
referencia pessoal; esquema reescrito para o dominio do ImageCore AI (geracoes, imagens e
referencias). Sem ORM; migracao aditiva.
"""

from contextlib import contextmanager
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS generations (
    id TEXT PRIMARY KEY,
    module TEXT NOT NULL CHECK (module IN ('thumbnail', 'image')),
    prompt TEXT NOT NULL,
    style TEXT NOT NULL DEFAULT 'none',
    preset TEXT,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    export_width INTEGER,
    export_height INTEGER,
    seed INTEGER NOT NULL,
    reference_ids TEXT NOT NULL DEFAULT '[]',
    provider TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    stage TEXT NOT NULL DEFAULT '',
    progress INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    params TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_generations_created ON generations (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_generations_status ON generations (status);

CREATE TABLE IF NOT EXISTS generation_images (
    id TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'generated' CHECK (kind IN ('generated', 'export')),
    mime TEXT NOT NULL,
    ext TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    rel_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_images_generation ON generation_images (generation_id);

CREATE TABLE IF NOT EXISTS gallery_file_deletions (
    rel_path TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reference_images (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    mime TEXT NOT NULL,
    ext TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    rel_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Etapa 5 (migracao aditiva): estado editavel das thumbnails. Sem FK para generations/
-- reference_images de proposito: excluir uma geracao NAO pode apagar nem corromper projetos;
-- o editor trata fundo indisponivel como estado visivel ("sem fundo").
CREATE TABLE IF NOT EXISTS thumbnail_projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT 'Sem titulo',
    generation_id TEXT,
    reference_id TEXT,
    preset TEXT,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    background TEXT NOT NULL DEFAULT '{}',
    layers TEXT NOT NULL DEFAULT '[]',
    canvas TEXT NOT NULL DEFAULT '{}',
    params TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_projects_updated ON thumbnail_projects (updated_at DESC);

CREATE TABLE IF NOT EXISTS thumbnail_project_images (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES thumbnail_projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'export' CHECK (kind IN ('export', 'preview', 'background')),
    mime TEXT NOT NULL,
    ext TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    rel_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_images_project ON thumbnail_project_images (project_id);
"""

_MIGRATION_PROJECT_IMAGE_KINDS = """
CREATE TABLE thumbnail_project_images_new (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES thumbnail_projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'export' CHECK (kind IN ('export', 'preview', 'background')),
    mime TEXT NOT NULL,
    ext TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    rel_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);
INSERT INTO thumbnail_project_images_new
    (id, project_id, kind, mime, ext, sha256, size_bytes, width, height, rel_path, created_at)
SELECT id, project_id, kind, mime, ext, sha256, size_bytes, width, height, rel_path, created_at
FROM thumbnail_project_images;
DROP TABLE thumbnail_project_images;
ALTER TABLE thumbnail_project_images_new RENAME TO thumbnail_project_images;
CREATE INDEX IF NOT EXISTS idx_project_images_project ON thumbnail_project_images (project_id);
"""


def _migrate_project_image_kinds(conn: sqlite3.Connection) -> None:
    """Etapa 6 (migracao aditiva): permite kind 'background' (copia independente do fundo).

    Reconstroi a tabela preservando TODAS as linhas; roda uma unica vez (guarda pelo SQL
    armazenado em sqlite_master). Nunca apaga nem altera registros existentes.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'thumbnail_project_images'"
    ).fetchone()
    if row and row[0] and "background" not in row[0]:
        conn.executescript(_MIGRATION_PROJECT_IMAGE_KINDS)


class Database:
    """Acesso SQLite local (WAL) para aplicacao single-user."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._tx_depth: int = 0

    def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)
            _migrate_project_image_kinds(self._conn)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @contextmanager
    def transaction(self, *, immediate: bool = False):
        with self._lock:
            assert self._conn is not None, "banco nao conectado"
            outer = self._tx_depth == 0
            if outer and immediate:
                self._conn.execute("BEGIN IMMEDIATE")
            self._tx_depth += 1
            try:
                yield self._conn
                if outer:
                    self._conn.commit()
            except Exception:
                if outer:
                    self._conn.rollback()
                raise
            finally:
                self._tx_depth -= 1

    def write(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            assert self._conn is not None, "banco nao conectado"
            cursor = self._conn.execute(sql, params)
            if self._tx_depth == 0:
                self._conn.commit()
            return cursor

    def read_all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            assert self._conn is not None, "banco nao conectado"
            return self._conn.execute(sql, params).fetchall()

    def read_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            assert self._conn is not None, "banco nao conectado"
            return self._conn.execute(sql, params).fetchone()
