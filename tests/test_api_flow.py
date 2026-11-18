"""Fluxo completo de geracoes: criar, acompanhar, exportar, compor, listar e excluir."""

import io

import pytest
from PIL import Image

from tests.conftest import wait_terminal


def test_health_e_presets(client):
    assert client.get("/api/health").json()["ok"] is True
    presets = client.get("/api/presets").json()
    assert presets["platform_presets"]
    assert len(presets["aspect_ratios"]) == 8
    assert any(style["key"] == "none" for style in presets["styles"])


def test_geracao_thumbnail_ponta_a_ponta(client):
    created = client.post("/api/generations", json={
        "module": "thumbnail",
        "prompt": "gamer animado com fundo neon",
        "style": "photoreal",
        "preset": "youtube_thumb",
    })
    assert created.status_code == 201
    gen = created.json()["generation"]
    assert gen["status"] == "queued"
    assert gen["width"] == 1280 and gen["height"] == 720
    assert gen["provider"] == "simulated"

    final = wait_terminal(client, gen["id"])
    assert final["status"] == "succeeded", final.get("error")
    generated = final["images"][0]
    assert generated["kind"] == "generated"
    assert (generated["width"], generated["height"]) == (1280, 720)

    served = client.get(f"/api/generations/{gen['id']}/images/{generated['id']}/content")
    assert served.status_code == 200
    with Image.open(io.BytesIO(served.content)) as img:
        assert img.size == (1280, 720)

    listing = client.get("/api/generations?module=thumbnail").json()["generations"]
    entry = next(item for item in listing if item["id"] == gen["id"])
    assert entry["images"] and entry["images"][0]["kind"] == "generated"


def test_geracao_image_com_proporcao_e_tier(client):
    created = client.post("/api/generations", json={
        "module": "image",
        "prompt": "estufa de vidro ao entardecer",
        "ratio": "16:9",
        "tier": "1080p",
    })
    assert created.status_code == 201
    gen = created.json()["generation"]
    assert (gen["width"], gen["height"]) == (1920, 1080)
    final = wait_terminal(client, gen["id"])
    assert final["status"] == "succeeded"


def test_geracao_com_referencias(client, png_bytes):
    ref = client.post("/api/references", content=png_bytes, headers={"x-filename": "ref.png"}).json()["reference"]
    created = client.post("/api/generations", json={
        "module": "image",
        "prompt": "variação usando a referencia",
        "ratio": "1:1",
        "tier": "720p",
        "reference_ids": [ref["id"]],
    })
    assert created.status_code == 201
    final = wait_terminal(client, created.json()["generation"]["id"])
    assert final["status"] == "succeeded"
    assert final["reference_ids"] == [ref["id"]]


def test_referencia_em_motor_sem_suporte(client, settings, png_bytes, monkeypatch):
    from backend.generation.registry import PROVIDERS

    class NoRefsStub(PROVIDERS["simulated"]):
        def capabilities(self):
            return {"references": False, "max_references": 0, "editable": False, "note": "stub"}

    monkeypatch.setitem(PROVIDERS, "simulated", NoRefsStub)
    from backend.main import create_app as _create
    from fastapi.testclient import TestClient as _TC

    with _TC(_create(settings)) as client:
        ref = client.post("/api/references", content=png_bytes, headers={"x-filename": "r.png"}).json()["reference"]
        response = client.post("/api/generations", json={
            "module": "image", "prompt": "x", "ratio": "1:1", "tier": "720p",
            "reference_ids": [ref["id"]],
        })
        assert response.status_code == 400
        assert "nao suporta" in response.json()["error"].lower()


def test_validacao_de_entrada(client):
    assert client.post("/api/generations", json={
        "module": "thumbnail", "prompt": "x", "preset": "inexistente",
    }).status_code == 400
    assert client.post("/api/generations", json={
        "module": "image", "prompt": "x",
    }).status_code == 400
    assert client.post("/api/generations", json={
        "module": "image", "prompt": "x", "ratio": "16:9", "tier": "8k",
    }).status_code == 400
    assert client.post("/api/generations", json={
        "module": "image", "prompt": "x", "ratio": "16:9", "tier": "1080p", "style": "hack",
    }).status_code == 400
    assert client.post("/api/generations", json={
        "module": "thumbnail", "prompt": "", "preset": "youtube_thumb",
    }).status_code in (400, 422)


def test_cancelamento(client):
    created = client.post("/api/generations", json={
        "module": "image", "prompt": "para cancelar", "ratio": "1:1", "tier": "4k",
    })
    gen_id = created.json()["generation"]["id"]
    assert client.post(f"/api/generations/{gen_id}/cancel").status_code == 200
    final = wait_terminal(client, gen_id)
    assert final["status"] in ("cancelled", "succeeded")  # corrida amigavel com o worker
    again = client.post(f"/api/generations/{gen_id}/cancel")
    assert again.status_code == 409


def test_fila_limitada_a_uma_geracao(client):
    ids = []
    for index in range(5):
        created = client.post("/api/generations", json={
            "module": "image", "prompt": f"job {index}", "ratio": "1:1", "tier": "720p",
        })
        assert created.status_code == 201
        ids.append(created.json()["generation"]["id"])
    sixth = client.post("/api/generations", json={
        "module": "image", "prompt": "estoura a fila", "ratio": "1:1", "tier": "720p",
    })
    assert sixth.status_code == 409
    for gen_id in ids:
        final = wait_terminal(client, gen_id, timeout_s=60)
        assert final["status"] == "succeeded"


def test_export_resize_e_composite(client):
    created = client.post("/api/generations", json={
        "module": "thumbnail", "prompt": "para exportar", "preset": "youtube_thumb",
    })
    gen = wait_terminal(client, created.json()["generation"]["id"])
    original = gen["images"][0]

    exported = client.post(f"/api/generations/{gen['id']}/export", json={
        "image_id": original["id"], "width": 640, "height": 360, "format": "jpeg",
    })
    assert exported.status_code == 201
    export_row = exported.json()["image"]
    assert export_row["kind"] == "export"
    served = client.get(f"/api/generations/{gen['id']}/images/{export_row['id']}/content")
    with Image.open(io.BytesIO(served.content)) as img:
        assert img.size == (640, 360)
        assert img.format == "JPEG"

    buffer = io.BytesIO()
    Image.new("RGB", (1280, 720), (5, 5, 5)).save(buffer, format="PNG")
    composite = client.post(f"/api/generations/{gen['id']}/composite", content=buffer.getvalue())
    assert composite.status_code == 201
    assert composite.json()["image"]["width"] == 1280


def test_export_acima_do_limite_de_pixels(client):
    created = client.post("/api/generations", json={
        "module": "thumbnail", "prompt": "limite", "preset": "youtube_thumb",
    })
    gen = wait_terminal(client, created.json()["generation"]["id"])
    original = gen["images"][0]
    response = client.post(f"/api/generations/{gen['id']}/export", json={
        "image_id": original["id"], "width": 6272, "height": 6272,
    })
    assert response.status_code == 400


def test_excluir_geracao_remove_arquivos(client, settings):
    created = client.post("/api/generations", json={
        "module": "image", "prompt": "para excluir", "ratio": "1:1", "tier": "720p",
    })
    gen = wait_terminal(client, created.json()["generation"]["id"])
    data_dir = settings.storage.database_path.parent
    gen_dir = data_dir / "generations" / gen["id"]
    assert gen_dir.is_dir()

    assert client.delete(f"/api/generations/{gen['id']}").status_code == 200
    assert client.get(f"/api/generations/{gen['id']}").status_code == 404
    assert not gen_dir.exists()


def test_detalhe_404(client):
    assert client.get("/api/generations/0" * 32).status_code == 404
    assert client.get("/api/generations/xyz").status_code == 404


# ------------------------------------------------------------------ seed (auto/manual)

def test_seed_automatica_ignora_valor_e_registra_modo(client):
    created = client.post("/api/generations", json={
        "module": "image", "prompt": "seed auto", "ratio": "1:1", "tier": "720p",
        "seed_mode": "auto", "seed": 123,
    })
    assert created.status_code == 201
    gen = created.json()["generation"]
    assert gen["seed"] != 123  # modo automatico sempre sorteia uma seed nova
    assert gen["params"]["seed_mode"] == "auto"


def test_seed_manual_aceita_faixa_32bits(client):
    created = client.post("/api/generations", json={
        "module": "image", "prompt": "seed manual", "ratio": "1:1", "tier": "720p",
        "seed_mode": "manual", "seed": 4294967295,
    })
    assert created.status_code == 201
    gen = created.json()["generation"]
    assert gen["seed"] == 4294967295
    assert gen["params"]["seed_mode"] == "manual"
    final = wait_terminal(client, gen["id"])
    assert final["status"] == "succeeded"


def test_seed_manual_sem_valor_rejeitado(client):
    response = client.post("/api/generations", json={
        "module": "image", "prompt": "sem seed", "ratio": "1:1", "tier": "720p",
        "seed_mode": "manual",
    })
    assert response.status_code == 400
    assert "seed" in response.json()["error"].lower()


def test_seed_acima_de_32bits_rejeitada(client):
    response = client.post("/api/generations", json={
        "module": "image", "prompt": "seed grande", "ratio": "1:1", "tier": "720p",
        "seed_mode": "manual", "seed": 2**32,
    })
    assert response.status_code == 422


def test_seeds_automaticas_diferentes_entre_geracoes(client):
    seeds = set()
    for _ in range(2):
        created = client.post("/api/generations", json={
            "module": "image", "prompt": "auto variacoes", "ratio": "1:1", "tier": "720p",
        })
        assert created.status_code == 201
        seeds.add(created.json()["generation"]["seed"])
    assert len(seeds) == 2
