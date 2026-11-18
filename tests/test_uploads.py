"""Uploads de referencia: validacao, sandbox, integridade e remocao."""

import io

import pytest
from PIL import Image

from fastapi.testclient import TestClient

from backend.main import create_app
from tests.conftest import make_settings


def _jpeg_bytes(size=(32, 24), color=(10, 120, 250)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_upload_e_download(client, png_bytes):
    response = client.post("/api/references", content=png_bytes, headers={"x-filename": "minha ref.png"})
    assert response.status_code == 201
    ref = response.json()["reference"]
    assert ref["display_name"] == "minha ref.png"
    assert ref["width"] == 64 and ref["height"] == 48
    assert ref["mime"] == "image/png"

    served = client.get(f"/api/references/{ref['id']}")
    assert served.status_code == 200
    with Image.open(io.BytesIO(served.content)) as img:
        assert img.size == (64, 48)
        assert img.format == "PNG"


def test_upload_recusa_lixo(client):
    response = client.post("/api/references", content=b"isto nao e uma imagem", headers={"x-filename": "x.png"})
    assert response.status_code == 415


def test_upload_recusa_vazio(client):
    response = client.post("/api/references", content=b"", headers={"x-filename": "x.png"})
    assert response.status_code == 415


def test_nome_de_exibicao_sanitizado(client, png_bytes):
    response = client.post(
        "/api/references",
        content=png_bytes,
        headers={"x-filename": r"..\\..\\..\\windows\\evil<>|.png"},
    )
    assert response.status_code == 201
    name = response.json()["reference"]["display_name"]
    assert ".." not in name
    assert "\\" not in name and "/" not in name


def test_limite_de_tamanho(tmp_path):
    import os

    from PIL import Image as PILImage

    buffer = io.BytesIO()
    noise = PILImage.frombytes("L", (512, 512), os.urandom(512 * 512))  # incompressivel (~260 KB)
    noise.save(buffer, format="PNG")
    data = buffer.getvalue()
    assert len(data) > 2048

    settings = make_settings(tmp_path, max_image_bytes=1024)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post("/api/references", content=data, headers={"x-filename": "grande.png"})
        assert response.status_code == 413


def test_delete_referencia(client, png_bytes):
    ref = client.post("/api/references", content=png_bytes, headers={"x-filename": "a.png"}).json()["reference"]
    assert client.delete(f"/api/references/{ref['id']}").status_code == 200
    assert client.get(f"/api/references/{ref['id']}").status_code == 404


def test_id_invalido(client):
    assert client.get("/api/references/nao-e-hex").status_code == 400
    assert client.delete("/api/references/..%2Fescape").status_code in (400, 404)
