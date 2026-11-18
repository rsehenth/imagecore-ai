"""Projetos de thumbnail (Etapa 5): persistencia editavel, preview e exportacao WYSIWYG."""

import base64
import io

import pytest
from PIL import Image

from tests.conftest import wait_terminal

LAYERS = [
    {
        "id": "a" * 32, "text": "TITULO", "font": "Impact, 'Arial Black', sans-serif",
        "sizePct": 18, "color": "#ffffff", "bold": True, "align": "center",
        "rotation": 0, "opacity": 1, "x": 0.5, "y": 0.8,
        "outline": {"on": True, "color": "#000000", "widthPct": 0.09},
        "shadow": {"on": True, "intensity": 60},
    },
]

SECOND_LAYER = {
    "id": "b" * 32, "text": "subtitulo", "font": "'Segoe UI', sans-serif", "sizePct": 6,
    "color": "#1ab6dc", "bold": False, "align": "left", "rotation": -8, "opacity": 0.85,
    "x": 0.3, "y": 0.4, "outline": {"on": False, "color": "#000000", "widthPct": 0.06},
    "shadow": {"on": False, "intensity": 30},
}


def preview_data_url(width: int = 320, height: int = 180) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (20, 120, 160)).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def payload(**overrides) -> dict:
    base = {
        "name": "Meu projeto", "preset": "youtube_thumb", "width": 1280, "height": 720,
        "background": {"source": "none", "fit": "cover", "offset": {"x": 0.1, "y": 0}},
        "layers": [dict(LAYERS[0]), dict(SECOND_LAYER)],
        "canvas": {"guides": True, "snap": True},
        "params": {"seed": 424242, "provider": "simulated"},
        "preview": preview_data_url(),
    }
    base.update(overrides)
    return base


def make_png(width: int, height: int, color=(5, 5, 5)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


# ------------------------------------------------------------------ crud

def test_criar_listar_reabrir_e_atualizar_projeto(client):
    created = client.post("/api/projects", json=payload())
    assert created.status_code == 201
    project = created.json()["project"]
    assert project["name"] == "Meu projeto"
    assert len(project["layers"]) == 2
    assert project["layers"][1]["rotation"] == -8
    assert project["background"]["fit"] == "cover"
    assert project["width"] == 1280 and project["height"] == 720
    previews = [img for img in project["images"] if img["kind"] == "preview"]
    assert len(previews) == 1

    listing = client.get("/api/projects").json()["projects"]
    assert any(item["id"] == project["id"] for item in listing)

    reopened = client.get(f"/api/projects/{project['id']}").json()["project"]
    assert reopened["layers"] == project["layers"]

    updated = client.put(f"/api/projects/{project['id']}", json=payload(name="Editado", layers=[dict(LAYERS[0])]))
    assert updated.status_code == 200
    after = updated.json()["project"]
    assert after["name"] == "Editado"
    assert len(after["layers"]) == 1
    # preview substituido, nunca acumulado
    assert len([img for img in after["images"] if img["kind"] == "preview"]) == 1


def test_projeto_sem_preview_e_sem_camadas(client):
    created = client.post("/api/projects", json=payload(preview=None, layers=[], background={}))
    assert created.status_code == 201
    project = created.json()["project"]
    assert project["layers"] == []
    assert project["images"] == []


def test_limite_de_cinco_camadas(client):
    many = [dict(LAYERS[0], id=f"{index:02x}" * 16) for index in range(6)]
    response = client.post("/api/projects", json=payload(layers=many))
    assert response.status_code in (400, 422)


def test_nome_acima_do_limite(client):
    response = client.post("/api/projects", json=payload(name="x" * 200))
    assert response.status_code == 422


def test_dimensoes_invalidas(client):
    assert client.post("/api/projects", json=payload(width=32)).status_code == 422
    assert client.post("/api/projects", json=payload(width=6272, height=6272)).status_code == 400


def test_preview_invalido_rejeitado(client):
    fake = "data:image/png;base64," + base64.b64encode(b"nao-e-png").decode()
    assert client.post("/api/projects", json=payload(preview=fake)).status_code == 400
    assert client.post("/api/projects", json=payload(preview="data:image/jpeg;base64,AAAA")).status_code == 400


# ------------------------------------------------------------------ vinculacao

def test_projeto_vinculado_a_geracao_e_referencia(client, png_bytes):
    ref = client.post("/api/references", content=png_bytes, headers={"x-filename": "fundo.png"}).json()["reference"]
    created = client.post("/api/generations", json={
        "module": "thumbnail", "prompt": "fundo do projeto", "preset": "youtube_thumb",
    })
    gen = wait_terminal(client, created.json()["generation"]["id"])
    image = gen["images"][0]

    body = payload(
        generation_id=gen["id"], reference_id=ref["id"],
        background={"source": "generation", "generation_id": gen["id"], "image_id": image["id"], "fit": "cover"},
    )
    project = client.post("/api/projects", json=body).json()["project"]
    assert project["generation_id"] == gen["id"]
    assert project["reference_id"] == ref["id"]

    # Excluir a geracao NAO pode apagar nem corromper o projeto (sem FK, por design).
    assert client.delete(f"/api/generations/{gen['id']}").status_code == 200
    reopened = client.get(f"/api/projects/{project['id']}")
    assert reopened.status_code == 200
    assert reopened.json()["project"]["background"]["source"] == "generation"


def test_vinculo_inexistente_rejeitado(client):
    assert client.post("/api/projects", json=payload(generation_id="0" * 32)).status_code == 404
    assert client.post("/api/projects", json=payload(reference_id="1" * 32)).status_code == 404


def test_ids_invalidos(client):
    assert client.get("/api/projects/xyz").status_code == 404
    assert client.get("/api/projects/" + "a" * 32).status_code == 404
    assert client.put("/api/projects/" + "b" * 32, json=payload()).status_code == 404
    assert client.delete("/api/projects/" + "c" * 32).status_code == 404


# ------------------------------------------------------------------ preview e exportacao

def test_preview_servido_com_integridade(client):
    project = client.post("/api/projects", json=payload()).json()["project"]
    preview = next(img for img in project["images"] if img["kind"] == "preview")
    served = client.get(f"/api/projects/{project['id']}/images/{preview['id']}/content")
    assert served.status_code == 200
    with Image.open(io.BytesIO(served.content)) as img:
        assert img.format == "PNG"
    # imagem de outro projeto nao serve
    other = client.post("/api/projects", json=payload(name="outro")).json()["project"]
    crossed = client.get(f"/api/projects/{other['id']}/images/{preview['id']}/content")
    assert crossed.status_code == 404


def test_exportacao_jpeg_com_qualidade(client):
    project = client.post("/api/projects", json=payload()).json()["project"]
    exported = client.post(
        f"/api/projects/{project['id']}/export?format=jpeg&quality=85&width=640&height=360",
        content=make_png(1280, 720),
    )
    assert exported.status_code == 201
    row = exported.json()["image"]
    assert row["kind"] == "export" and row["ext"] == "jpg"
    served = client.get(f"/api/projects/{project['id']}/images/{row['id']}/content")
    with Image.open(io.BytesIO(served.content)) as img:
        assert img.format == "JPEG"
        assert img.size == (640, 360)


def test_exportacao_png_dimensoes_iguais_sem_resize(client):
    """Composicao WYSIWYG no tamanho final: reencode puro, sem Lanczos."""
    project = client.post("/api/projects", json=payload()).json()["project"]
    exported = client.post(
        f"/api/projects/{project['id']}/export?format=png&width=1280&height=720",
        content=make_png(1280, 720, (200, 30, 30)),
    )
    assert exported.status_code == 201
    row = exported.json()["image"]
    served = client.get(f"/api/projects/{project['id']}/images/{row['id']}/content")
    with Image.open(io.BytesIO(served.content)) as img:
        assert img.format == "PNG"
        assert img.size == (1280, 720)
        # pixel de canto preservado sem reamostragem
        assert img.convert("RGB").getpixel((2, 2)) == (200, 30, 30)


def test_exportacao_body_invalido(client):
    project = client.post("/api/projects", json=payload()).json()["project"]
    response = client.post(f"/api/projects/{project['id']}/export?format=png", content=b"\x00" * 64)
    assert response.status_code == 400
    assert "png" in response.json()["error"].lower()


def test_exportacao_acima_do_limite_de_pixels(client):
    project = client.post("/api/projects", json=payload()).json()["project"]
    response = client.post(
        f"/api/projects/{project['id']}/export?format=png&width=6272&height=6272",
        content=make_png(64, 64),
    )
    assert response.status_code == 400


def test_excluir_projeto_remove_arquivos(client, settings):
    project = client.post("/api/projects", json=payload()).json()["project"]
    export = client.post(
        f"/api/projects/{project['id']}/export?format=png&width=640&height=360",
        content=make_png(1280, 720),
    ).json()["image"]
    data_dir = settings.storage.database_path.parent
    project_dir = data_dir / "projects" / project["id"]
    assert project_dir.is_dir()

    assert client.delete(f"/api/projects/{project['id']}").status_code == 200
    assert client.get(f"/api/projects/{project['id']}").status_code == 404
    assert not project_dir.exists()
    for image in [img for img in project["images"]] + [export]:
        assert not (data_dir / image["rel_path"]).exists()


def test_excluir_geracao_nao_afeta_projeto_salvo(client):
    """Requisito 5: historico e projetos sao independentes (migracao aditiva e segura)."""
    created = client.post("/api/generations", json={
        "module": "thumbnail", "prompt": "histórico preservado", "preset": "youtube_thumb",
    })
    gen = wait_terminal(client, created.json()["generation"]["id"])
    project = client.post("/api/projects", json=payload(generation_id=gen["id"])).json()["project"]

    client.delete(f"/api/projects/{project['id']}")
    still = client.get(f"/api/generations/{gen['id']}")
    assert still.status_code == 200
    assert still.json()["generation"]["status"] == "succeeded"
