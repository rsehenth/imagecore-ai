"""Etapa 6: caixa de texto por camada, copia independente do fundo, migracao aditiva,
integracao projeto-galeria e fidelidade total de exportacao (comparacao de pixels)."""

import io
import sqlite3

from PIL import Image, ImageChops

from backend.storage.database import Database
from tests.conftest import wait_terminal
from tests.test_projects import LAYERS, make_png, payload


def layer_with_width(width_pct: float) -> dict:
    return dict(LAYERS[0], id="c" * 32, widthPct=width_pct)


# ------------------------------------------------------------------ caixa de texto por camada

def test_widthpct_salvo_e_reaberto(client):
    body = payload(layers=[layer_with_width(0.55)])
    project = client.post("/api/projects", json=body).json()["project"]
    reopened = client.get(f"/api/projects/{project['id']}").json()["project"]
    assert reopened["layers"][0]["widthPct"] == 0.55

    # atualizacao com nova largura persiste
    updated = client.put(
        f"/api/projects/{project['id']}",
        json=payload(layers=[layer_with_width(0.3)]),
    ).json()["project"]
    assert updated["layers"][0]["widthPct"] == 0.3


def test_camada_antiga_sem_widthpct_permanece_opaca(client):
    """Compatibilidade: servidor nao inventa campo; o padrao 92% e aplicado no editor."""
    body = payload(layers=[dict(LAYERS[0])])
    project = client.post("/api/projects", json=body).json()["project"]
    reopened = client.get(f"/api/projects/{project['id']}").json()["project"]
    assert "widthPct" not in reopened["layers"][0]


# ------------------------------------------------------------------ copia independente do fundo

def test_copia_de_fundo_sobrevive_a_exclusao_da_geracao(client):
    created = client.post("/api/generations", json={
        "module": "thumbnail", "prompt": "fundo copiado", "preset": "youtube_thumb",
    })
    gen = wait_terminal(client, created.json()["generation"]["id"])
    image = gen["images"][0]
    original = client.get(f"/api/generations/{gen['id']}/images/{image['id']}/content").content

    project = client.post("/api/projects", json=payload(
        generation_id=gen["id"],
        background={"source": "generation", "generation_id": gen["id"], "image_id": image["id"], "fit": "cover"},
        preview=None,
    )).json()["project"]

    copies = [img for img in project["images"] if img["kind"] == "background"]
    assert len(copies) == 1
    copy = copies[0]
    assert copy["sha256"] == image["sha256"]
    served = client.get(f"/api/projects/{project['id']}/images/{copy['id']}/content")
    assert served.status_code == 200
    with Image.open(io.BytesIO(served.content)) as a, Image.open(io.BytesIO(original)) as b:
        assert ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is None
    assert client.get(f"/api/projects/{project['id']}").json()["project"]["background"]["copy_image_id"] == copy["id"]

    # Origem excluida: projeto continua intacto, editavel e com fundo servido da copia.
    assert client.delete(f"/api/generations/{gen['id']}").status_code == 200
    reopened = client.get(f"/api/projects/{project['id']}")
    assert reopened.status_code == 200
    body = reopened.json()["project"]
    assert body["background"]["copy_image_id"] == copy["id"]
    assert client.get(f"/api/projects/{project['id']}/images/{copy['id']}/content").status_code == 200

    # Re-salvar (editar depois da exclusao) NAO pode falhar por vinculo orfao.
    resaved = client.put(
        f"/api/projects/{project['id']}",
        json=payload(
            name="Editado apos exclusao",
            generation_id=gen["id"],  # link que agora aponta para nada
            background={"source": "generation", "generation_id": gen["id"], "image_id": image["id"], "fit": "cover"},
            preview=None,
        ),
    )
    assert resaved.status_code == 200
    after = resaved.json()["project"]
    assert after["name"] == "Editado apos exclusao"
    assert after["generation_id"] is None  # vinculo orfao anulado, nunca bloqueou a edicao
    still = [img for img in after["images"] if img["kind"] == "background"]
    assert len(still) == 1


def test_copia_de_fundo_local_sobrevive_a_exclusao_da_referencia(client, png_bytes):
    ref = client.post("/api/references", content=png_bytes, headers={"x-filename": "fundo.png"}).json()["reference"]
    project = client.post("/api/projects", json=payload(
        reference_id=ref["id"],
        background={"source": "reference", "reference_id": ref["id"], "fit": "cover"},
        preview=None,
    )).json()["project"]
    copies = [img for img in project["images"] if img["kind"] == "background"]
    assert len(copies) == 1 and copies[0]["ext"] == "png"

    assert client.delete(f"/api/references/{ref['id']}").status_code == 200
    copy_id = copies[0]["id"]
    assert client.get(f"/api/projects/{project['id']}/images/{copy_id}/content").status_code == 200
    resaved = client.put(
        f"/api/projects/{project['id']}",
        json=payload(
            reference_id=ref["id"],
            background={"source": "reference", "reference_id": ref["id"], "fit": "cover"},
            preview=None,
        ),
    )
    assert resaved.status_code == 200
    assert resaved.json()["project"]["reference_id"] is None


def test_copia_nao_acumula_entre_salvamentos(client):
    created = client.post("/api/generations", json={
        "module": "thumbnail", "prompt": "fundo duas copias", "preset": "youtube_thumb",
    })
    gen = wait_terminal(client, created.json()["generation"]["id"])
    image = gen["images"][0]
    bg = {"source": "generation", "generation_id": gen["id"], "image_id": image["id"], "fit": "cover"}
    project = client.post("/api/projects", json=payload(generation_id=gen["id"], background=bg, preview=None)).json()["project"]
    again = client.put(f"/api/projects/{project['id']}", json=payload(generation_id=gen["id"], background=bg, preview=None)).json()["project"]
    assert len([img for img in again["images"] if img["kind"] == "background"]) == 1


def test_salvamento_com_fundo_origem_ja_inexistente_nao_falha(client):
    """Cenario adverso: fundo aponta para geracao inexistente e sem copia anterior."""
    body = payload(background={"source": "generation", "generation_id": "9" * 32, "image_id": "9" * 32}, preview=None)
    project = client.post("/api/projects", json=body)
    assert project.status_code == 201  # origem ausente: salva sem copia, nunca quebra


# ------------------------------------------------------------------ migracao aditiva

OLD_PROJECT_IMAGES_SCHEMA = """
CREATE TABLE thumbnail_projects (
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
CREATE TABLE thumbnail_project_images (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES thumbnail_projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'export' CHECK (kind IN ('export', 'preview')),
    mime TEXT NOT NULL,
    ext TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    rel_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def test_migracao_preserva_registros_e_libera_kind_background(tmp_path):
    db_path = tmp_path / "data" / "legacy.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    legacy = sqlite3.connect(db_path)
    legacy.executescript(OLD_PROJECT_IMAGES_SCHEMA)
    legacy.execute(
        "INSERT INTO thumbnail_projects (id, name, width, height, created_at, updated_at) "
        "VALUES (?, 'Projeto antigo', 1280, 720, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
        ("a" * 32,),
    )
    legacy.execute(
        "INSERT INTO thumbnail_project_images (id, project_id, kind, mime, ext, sha256, size_bytes, width, height, rel_path, created_at) "
        "VALUES (?, ?, 'export', 'image/png', 'png', ?, 3, 64, 48, 'projects/x/1.png', '2026-01-01T00:00:00+00:00')",
        ("b" * 32, "a" * 32, "d" * 64),
    )
    legacy.commit()
    legacy.close()

    db = Database(db_path)
    db.connect()
    try:
        row = db.read_one("SELECT * FROM thumbnail_project_images WHERE id = ?", ("b" * 32,))
        assert row["kind"] == "export"  # registro anterior preservado intacto
        name = db.read_one("SELECT name FROM thumbnail_projects WHERE id = ?", ("a" * 32,))["name"]
        assert name == "Projeto antigo"
        # novo kind aceito na mesma tabela
        db.write(
            "INSERT INTO thumbnail_project_images (id, project_id, kind, mime, ext, sha256, size_bytes, width, height, rel_path, created_at) "
            "VALUES (?, ?, 'background', 'image/png', 'png', ?, 3, 64, 48, 'projects/x/2.png', '2026-01-01T00:00:00+00:00')",
            ("c" * 32, "a" * 32, "e" * 64),
        )
        kinds = {r["kind"] for r in db.read_all("SELECT kind FROM thumbnail_project_images")}
        assert kinds == {"export", "background"}
    finally:
        db.close()


# ------------------------------------------------------------------ exportacao e detalhe

def test_exportacao_png_identidade_total_de_pixels(client):
    """Composicao no tamanho final = reencode puro: TODOS os pixels identicos."""
    composition = io.BytesIO()
    Image.effect_mandelbrot((1280, 720), (-2.0, -1.2, 1.0, 1.2), 80).convert("RGB").save(composition, format="PNG")
    project = client.post("/api/projects", json=payload(preview=None)).json()["project"]
    exported = client.post(
        f"/api/projects/{project['id']}/export?format=png&width=1280&height=720",
        content=composition.getvalue(),
    )
    assert exported.status_code == 201
    row = exported.json()["image"]
    served = client.get(f"/api/projects/{project['id']}/images/{row['id']}/content")
    with Image.open(io.BytesIO(served.content)) as out, Image.open(io.BytesIO(composition.getvalue())) as src:
        assert out.size == src.size
        assert ImageChops.difference(out.convert("RGB"), src.convert("RGB")).getbbox() is None

    # Redimensionamento declarado: dims finais respeitadas (Lanczos, rotulado como tal).
    resized = client.post(
        f"/api/projects/{project['id']}/export?format=png&width=1920&height=1080",
        content=composition.getvalue(),
    ).json()["image"]
    served2 = client.get(f"/api/projects/{project['id']}/images/{resized['id']}/content")
    with Image.open(io.BytesIO(served2.content)) as out2:
        assert out2.size == (1920, 1080)


def test_detalhe_do_projeto_para_galeria(client):
    project = client.post("/api/projects", json=payload(preview=None)).json()["project"]
    client.post(f"/api/projects/{project['id']}/export?format=png&width=640&height=360", content=make_png(1280, 720))
    detail = client.get(f"/api/projects/{project['id']}").json()["project"]
    kinds = sorted(img["kind"] for img in detail["images"])
    assert kinds == ["export"]
    listing = client.get("/api/projects").json()["projects"]
    card = next(item for item in listing if item["id"] == project["id"])
    assert card["name"] == "Meu projeto"
    assert [img for img in card["images"] if img["kind"] == "export"]
