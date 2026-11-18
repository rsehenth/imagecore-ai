"""Seguranca: middleware local, headers, CSP e travessia de caminhos."""

import asyncio
import io

import httpx
import pytest
from PIL import Image

from backend.core.config import AppSettings, ConfigError, load_settings
from backend.main import create_app


def test_bind_exige_opt_in_para_lan(tmp_path, monkeypatch):
    monkeypatch.delenv("IMAGECORE_LAN", raising=False)
    monkeypatch.delenv("IMAGECORE_HOST", raising=False)
    local = load_settings(root=tmp_path).app
    assert local.host == "127.0.0.1"
    assert local.allow_lan is False

    monkeypatch.setenv("IMAGECORE_HOST", "0.0.0.0")
    with pytest.raises(ConfigError, match="IMAGECORE_HOST"):
        load_settings(root=tmp_path)

    monkeypatch.setenv("IMAGECORE_LAN", "1")
    lan = load_settings(root=tmp_path).app
    assert lan.host == "0.0.0.0"
    assert lan.allow_lan is True


@pytest.mark.parametrize("allow_lan, expected", [(False, 403), (True, 200)])
def test_leitura_lan_exige_opt_in(settings, allow_lan, expected):
    settings.app = AppSettings(allow_lan=allow_lan)
    transport = httpx.ASGITransport(app=create_app(settings), client=("192.168.1.20", 50000))

    async def check():
        async with httpx.AsyncClient(transport=transport, base_url="http://192.168.1.10:8765") as client:
            response = await client.get("/api/health")
            assert response.status_code == expected
            if not allow_lan:
                assert response.json()["error_code"] == "local_only"

    asyncio.run(check())


@pytest.mark.parametrize(
    "method, path",
    [
        ("DELETE", "/api/generations/" + "a" * 32),
        ("POST", "/api/gallery/bulk-delete"),
        ("POST", "/api/references"),
        ("PUT", "/api/projects/" + "a" * 32),
        ("POST", "/api/system/engine/free"),
    ],
)
def test_lan_so_permite_leitura(settings, method, path):
    settings.app = AppSettings(allow_lan=True)
    transport = httpx.ASGITransport(app=create_app(settings), client=("192.168.1.20", 50000))

    async def check():
        async with httpx.AsyncClient(transport=transport, base_url="http://192.168.1.10:8765") as client:
            response = await client.request(method, path)
            assert response.status_code == 403
            assert response.json()["error_code"] == "lan_read_only"

    asyncio.run(check())


def test_modo_lan_preserva_escrita_via_loopback(settings):
    settings.app = AppSettings(allow_lan=True)
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))

    async def check():
        base_url = "http://127.0.0.1:8765"
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url=base_url) as client:
            response = await client.post(
                "/api/system/engine/free",
                json={"confirm": False},
                headers={"Origin": base_url, "Sec-Fetch-Site": "same-origin"},
            )
            assert response.status_code == 409
            assert response.json()["error_code"] == "free_not_supported"

    asyncio.run(check())


@pytest.mark.parametrize(
    "header, value, code",
    [
        ("Origin", "https://127.0.0.1:8765", "origin_unauthorized"),
        ("Origin", "http://localhost:8765", "origin_unauthorized"),
        ("Origin", "http://127.0.0.1", "origin_unauthorized"),
        ("Origin", "http://127.0.0.1:invalid", "origin_unauthorized"),
        ("Referer", "http://localhost:8765/settings", "referer_unauthorized"),
    ],
)
def test_metodos_de_escrita_exigem_mesma_origem(client, header, value, code):
    response = client.post("/api/system/engine/free", headers={"Host": "127.0.0.1:8765", header: value})
    assert response.status_code == 403
    assert response.json()["error_code"] == code


def test_host_externo_bloqueado(client):
    response = client.get("/api/health", headers={"Host": "malicioso.example"})
    assert response.status_code == 403


def test_cross_site_bloqueado(client):
    response = client.get("/api/health", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403


def test_origin_ajena_bloqueada_em_post(client):
    response = client.post(
        "/api/generations",
        json={"module": "image", "prompt": "x", "ratio": "1:1", "tier": "720p"},
        headers={"Origin": "http://evil.example"},
    )
    assert response.status_code == 403


def test_headers_de_seguranca_presentes(client):
    response = client.get("/")
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert "default-src 'self'" in response.headers.get("Content-Security-Policy", "")


def test_index_servido(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "ImageCore AI" in response.text


def test_ids_fora_do_padrao(client):
    assert client.get("/api/generations/%2e%2e%2fetc").status_code in (400, 404)
    assert client.get("/api/generations/" + "a" * 32 + "/images/" + ".." + "/content").status_code in (400, 404)


def test_upload_cortado_no_streaming(client):
    """Sem Content-Length confiavel: o corte pelo limite acontece durante o streaming."""
    response = client.post(
        "/api/references",
        content=b"\x00" * (16 * 1024 * 1024),
        headers={"x-filename": "chunk.png", "Transfer-Encoding": "chunked"},
    )
    assert response.status_code == 413
