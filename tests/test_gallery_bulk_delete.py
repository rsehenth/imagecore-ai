from pathlib import Path

import pytest

from backend.storage import gallery


def add_generation(client, png_bytes, *, kind="generated", status="succeeded"):
    repo = client.app.state.repo
    gen = repo.create(
        module="image", prompt="Test image", style="none", preset=None, width=64, height=48,
        export_width=None, export_height=None, seed=1, reference_ids=[], provider="simulated", params={},
    )
    image = repo.add_image(
        generation_id=gen["id"], kind=kind, mime="image/png", ext="png", data=png_bytes, width=64, height=48,
    )
    repo.finish(gen["id"], status)
    return gen["id"], image


@pytest.mark.parametrize(("invalid", "status"), [("empty", 400), ("duplicate", 400), ("missing", 404), ("active", 409)])
def test_invalid_selection_leaves_the_whole_batch_intact(client, png_bytes, invalid, status):
    repo = client.app.state.repo
    gen_id, image = add_generation(client, png_bytes)
    if invalid == "active":
        active, _ = add_generation(client, png_bytes, status="running")
    else:
        active = None
    ids = {"empty": [], "duplicate": [gen_id, gen_id], "missing": [gen_id, "0" * 32], "active": [gen_id, active]}[invalid]
    response = client.post("/api/gallery/bulk-delete", json={"kind": "generations", "ids": ids})
    assert response.status_code == status
    assert repo.get(gen_id) and (repo.files_root / image["rel_path"]).read_bytes() == png_bytes


def test_shared_file_and_exports_are_deleted_only_with_their_last_owner(client, png_bytes):
    repo = client.app.state.repo
    first, original = add_generation(client, png_bytes)
    second, export = add_generation(client, png_bytes, kind="export")
    (repo.files_root / export["rel_path"]).unlink()
    repo.db.write("UPDATE generation_images SET rel_path = ? WHERE id = ?", (original["rel_path"], export["id"]))
    file = repo.files_root / original["rel_path"]

    response = client.post("/api/gallery/bulk-delete", json={"kind": "exports", "ids": [original["id"], export["id"]]})
    assert response.status_code == 404
    response = client.post("/api/gallery/bulk-delete", json={"kind": "generations", "ids": [first]})
    assert response.json() == {"deleted": 1, "files_preserved": 1, "pending_files": 0}
    assert file.is_file() and repo.get(second) and not repo.get(first)

    response = client.post("/api/gallery/bulk-delete", json={"kind": "exports", "ids": [export["id"]]})
    assert response.json() == {"deleted": 1, "files_preserved": 0, "pending_files": 0}
    assert not file.exists() and repo.get(second)["images"] == []


def test_legacy_background_blocks_deletion_until_project_has_its_own_copy(client, png_bytes):
    repo = client.app.state.repo
    projects = client.app.state.projects
    gen_id, image = add_generation(client, png_bytes)
    other, _ = add_generation(client, png_bytes)
    project = projects.create(
        name="Legacy project", generation_id=gen_id, reference_id=None, preset=None, width=64, height=48,
        background={"source": "generation", "generation_id": gen_id, "image_id": image["id"]},
        layers=[], canvas={}, params={},
    )
    payload = {"kind": "generations", "ids": [other, gen_id]}
    response = client.post("/api/gallery/bulk-delete", json=payload)
    assert response.status_code == 409 and response.json()["error_code"] == "gallery_project_dependency"
    assert repo.get(other)
    background = projects.add_image(
        project_id=project["id"], kind="background", mime="image/png", ext="png", data=png_bytes, width=64, height=48,
    )
    assert client.post("/api/gallery/bulk-delete", json=payload).json()["deleted"] == 2
    assert not repo.get(other) and not repo.get(gen_id)
    assert projects.get(project["id"])["background"]["copy_image_id"] == background["id"]
    assert (repo.files_root / background["rel_path"]).read_bytes() == png_bytes


def test_unsafe_path_and_sql_failure_leave_records_and_files_intact(client, png_bytes):
    repo = client.app.state.repo
    good, image = add_generation(client, png_bytes)
    bad, unsafe = add_generation(client, png_bytes)
    payload = {"kind": "generations", "ids": [good, bad]}
    outside = repo.files_root.parent / "outside.png"
    outside.write_bytes(png_bytes)
    repo.db.write("UPDATE generation_images SET rel_path = ? WHERE id = ?", ("../outside.png", unsafe["id"]))
    response = client.post("/api/gallery/bulk-delete", json=payload)
    assert response.status_code == 409 and response.json()["error_code"] == "gallery_file_unsafe"
    repo.db.write("UPDATE generation_images SET rel_path = ? WHERE id = ?", (unsafe["rel_path"], unsafe["id"]))
    repo.db.write(
        f"CREATE TRIGGER fail_delete BEFORE DELETE ON generations WHEN OLD.id = '{bad}' "
        "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )
    response = client.post("/api/gallery/bulk-delete", json=payload)
    assert response.status_code == 409 and response.json()["error_code"] == "gallery_delete_failed"
    assert repo.get(good) and repo.get(bad)
    assert (repo.files_root / image["rel_path"]).is_file() and outside.is_file()
    assert repo.db.read_all("SELECT * FROM gallery_file_deletions") == []


def test_file_cleanup_failure_is_durable_and_retries(client, png_bytes, monkeypatch):
    repo = client.app.state.repo
    first, image = add_generation(client, png_bytes)
    second, other = add_generation(client, png_bytes)
    path = repo.files_root / image["rel_path"]
    unlink = gallery.safe_unlink_within

    def fail_for_one(target, root):
        if Path(target) == path:
            raise PermissionError("locked for test")
        return unlink(target, root)

    with monkeypatch.context() as patch:
        patch.setattr(gallery, "safe_unlink_within", fail_for_one)
        response = client.post("/api/gallery/bulk-delete", json={"kind": "generations", "ids": [first, second]})
    assert response.json() == {"deleted": 2, "files_preserved": 0, "pending_files": 1}
    assert not repo.get(first) and not repo.get(second)
    assert path.is_file() and not (repo.files_root / other["rel_path"]).exists()
    assert len(repo.db.read_all("SELECT * FROM gallery_file_deletions")) == 1
    assert repo.retry_file_deletions() == (1, 0)
    assert not path.exists()
