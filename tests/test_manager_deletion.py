import uuid

from app import create_app
from storage import database, vision_repository


def test_manager_deletes_vision_file_and_record_permanently(monkeypatch, tmp_path):
    import api.routes as routes

    job_id = str(uuid.uuid4())
    media_name = job_id + ".png"
    media_file = tmp_path / media_name
    media_file.write_bytes(b"private aerial image")
    stored = {"job_id": job_id, "media_path": media_name, "deleted_at": None}

    monkeypatch.setattr(routes.config, "ROAD_CONDITION_ADMIN_TOKEN", "manager-delete-token")
    monkeypatch.setattr(routes.config, "VISION_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(database, "initialize", lambda *_args: None)

    def begin_delete(_url, requested_id):
        if requested_id != job_id:
            return None
        stored["deleted_at"] = True
        return {"job_id": job_id, "media_path": media_name}

    def finalize_delete(_url, requested_id):
        if requested_id != job_id or not stored["deleted_at"]:
            return False
        stored.clear()
        return True

    def read_job(_url, requested_id):
        return stored.copy() if requested_id == job_id and not stored.get("deleted_at") else None

    def list_jobs(_url, limit=50):
        return [stored.copy()] if stored and not stored.get("deleted_at") else []

    monkeypatch.setattr(vision_repository, "begin_delete_job", begin_delete, raising=False)
    monkeypatch.setattr(vision_repository, "finalize_delete_job", finalize_delete, raising=False)
    monkeypatch.setattr(vision_repository, "get_job", read_job)
    monkeypatch.setattr(vision_repository, "list_jobs", list_jobs)

    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    headers = {"X-Admin-Token": "manager-delete-token"}

    response = client.delete("/api/manager/vision-jobs/" + job_id, headers=headers)

    assert response.status_code == 200
    assert not media_file.exists()
    assert client.get("/api/manager/vision-jobs?limit=20", headers=headers).get_json()["data"]["jobs"] == []
    assert client.get("/api/manager/vision-jobs/" + job_id, headers=headers).status_code == 404
    assert client.get("/api/manager/vision-jobs/" + job_id + "/media", headers=headers).status_code == 404


def test_manager_delete_rejects_path_escape_without_deleting_outside_file(monkeypatch, tmp_path):
    import api.routes as routes

    job_id = str(uuid.uuid4())
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"keep")

    monkeypatch.setattr(routes.config, "ROAD_CONDITION_ADMIN_TOKEN", "manager-delete-token")
    monkeypatch.setattr(routes.config, "VISION_UPLOAD_DIR", str(upload_dir))
    monkeypatch.setattr(database, "initialize", lambda *_args: None)
    monkeypatch.setattr(vision_repository, "begin_delete_job", lambda *_args: {
        "job_id": job_id, "media_path": "../outside.png",
    }, raising=False)

    app = create_app()
    app.config.update(TESTING=True)
    response = app.test_client().delete(
        "/api/manager/vision-jobs/" + job_id,
        headers={"X-Admin-Token": "manager-delete-token"},
    )

    assert response.status_code == 500
    assert outside.read_bytes() == b"keep"
