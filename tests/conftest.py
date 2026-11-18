"""Fixtures: app isolado em tmp_path com provider simulado + TestClient."""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.core.config import ProviderSettings, Settings, StorageSettings, UploadSettings
from backend.main import create_app


def make_settings(tmp_path, **overrides) -> Settings:
    upload = UploadSettings(max_pending=8, **overrides)
    return Settings(
        storage=StorageSettings(database_path=tmp_path / "data" / "test.db"),
        upload=upload,
        provider=ProviderSettings(name="simulated"),
    )


@pytest.fixture()
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture()
def client(settings):
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 40, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


def wait_terminal(client, gen_id: str, timeout_s: float = 30.0) -> dict:
    """Sonda a geracao ate um estado terminal (worker roda no loop do TestClient)."""
    import time

    deadline = time.time() + timeout_s
    last: dict = {}
    while time.time() < deadline:
        response = client.get(f"/api/generations/{gen_id}")
        assert response.status_code == 200
        last = response.json()["generation"]
        if last["status"] in ("succeeded", "failed", "cancelled"):
            return last
        time.sleep(0.15)
    raise AssertionError(f"Geracao {gen_id} nao terminou a tempo: {last.get('status')}")
