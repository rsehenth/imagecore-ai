"""Seguranca: middleware local, headers, CSP e travessia de caminhos."""

import io

from PIL import Image


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
