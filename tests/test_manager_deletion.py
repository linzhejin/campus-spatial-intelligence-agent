import uuid

import pytest

from app import create_app
from storage import database, vision_repository


@pytest.mark.parametrize(("attempts", "expected_status"), [(2, "queued"), (3, "failed")])
def test_failed_delete_restores_running_job_to_a_recoverable_state(
        monkeypatch, attempts, expected_status):
    job_id = str(uuid.uuid4())
    job = {"job_id": job_id, "status": "running", "attempts": attempts,
           "deleted_at": object(), "worker_id": None, "lease_until": None, "error": None}

    class Cursor:
        def fetchone(self):
            return {"job_id": job_id}

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, params):
            normalized = " ".join(query.split())
            assert "status=CASE WHEN status='running' AND attempts>=3 THEN 'failed'" in normalized
            assert "WHEN status='running' THEN 'queued' ELSE status END" in normalized
            assert "error=CASE WHEN status='running' AND attempts>=3 THEN %s" in normalized
            assert "worker_id=NULL, lease_until=NULL" in normalized
            assert "deleted_at=NULL" in normalized
            assert len(params) == 2 and params[1] == job_id
            if job["status"] == "running" and job["attempts"] >= 3:
                job["status"] = "failed"
                job["error"] = params[0].obj
            elif job["status"] == "running":
                job["status"] = "queued"
            job["deleted_at"] = None
            job["worker_id"] = None
            job["lease_until"] = None
            return Cursor()

    monkeypatch.setattr(database, "connect", lambda _url: Connection())

    assert vision_repository.restore_failed_delete_job("postgresql://test", job_id)
    assert job["status"] == expected_status
    assert job["deleted_at"] is None
    assert job["worker_id"] is None and job["lease_until"] is None
    if expected_status == "failed":
        assert job["error"]["code"] == "vision_delete_interrupted"
        assert "重新提交影像" in job["error"]["message"]


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
    stored = {"job_id": job_id, "media_path": "../outside.png", "deleted_at": None}

    monkeypatch.setattr(routes.config, "ROAD_CONDITION_ADMIN_TOKEN", "manager-delete-token")
    monkeypatch.setattr(routes.config, "VISION_UPLOAD_DIR", str(upload_dir))
    monkeypatch.setattr(database, "initialize", lambda *_args: None)
    def begin_delete(_url, requested_id):
        if requested_id != job_id:
            return None
        stored["deleted_at"] = True
        return {"job_id": job_id, "media_path": stored["media_path"]}

    def restore_delete(_url, requested_id):
        assert requested_id == job_id
        stored["deleted_at"] = None
        if stored.get("status") == "running":
            stored["status"] = "queued"
        return True

    monkeypatch.setattr(vision_repository, "begin_delete_job", begin_delete, raising=False)
    monkeypatch.setattr(vision_repository, "restore_failed_delete_job", restore_delete, raising=False)

    app = create_app()
    app.config.update(TESTING=True)
    response = app.test_client().delete(
        "/api/manager/vision-jobs/" + job_id,
        headers={"X-Admin-Token": "manager-delete-token"},
    )

    assert response.status_code == 500
    assert outside.read_bytes() == b"keep"
    assert stored["deleted_at"] is None


def test_manager_delete_restores_job_when_media_unlink_fails(monkeypatch, tmp_path):
    import api.routes as routes
    from pathlib import Path

    job_id = str(uuid.uuid4())
    media_name = job_id + ".png"
    media_file = tmp_path / media_name
    media_file.write_bytes(b"private aerial image")
    stored = {"job_id": job_id, "media_path": media_name, "deleted_at": None,
              "status": "running", "attempts": 2, "worker_id": "vision-1", "lease_until": 123}

    monkeypatch.setattr(routes.config, "ROAD_CONDITION_ADMIN_TOKEN", "manager-delete-token")
    monkeypatch.setattr(routes.config, "VISION_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(database, "initialize", lambda *_args: None)

    def begin_delete(_url, requested_id):
        if requested_id != job_id:
            return None
        stored["deleted_at"] = True
        stored["worker_id"] = None
        stored["lease_until"] = None
        return {"job_id": job_id, "media_path": media_name}

    def restore_delete(_url, requested_id):
        assert requested_id == job_id
        stored["deleted_at"] = None
        if stored.get("status") == "running":
            stored["status"] = "failed" if stored.get("attempts", 0) >= 3 else "queued"
        return True

    def read_job(_url, requested_id):
        return stored.copy() if requested_id == job_id and not stored.get("deleted_at") else None

    def list_jobs(_url, limit=50):
        return [stored.copy()] if stored and not stored.get("deleted_at") else []

    monkeypatch.setattr(vision_repository, "begin_delete_job", begin_delete, raising=False)
    monkeypatch.setattr(vision_repository, "restore_failed_delete_job", restore_delete, raising=False)
    monkeypatch.setattr(vision_repository, "get_job", read_job)
    monkeypatch.setattr(vision_repository, "list_jobs", list_jobs)

    original_unlink = Path.unlink

    def failing_unlink(path, *args, **kwargs):
        if path.resolve() == media_file.resolve():
            raise OSError("simulated locked media file")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    headers = {"X-Admin-Token": "manager-delete-token"}

    response = client.delete("/api/manager/vision-jobs/" + job_id, headers=headers)

    assert response.status_code == 503
    assert stored["deleted_at"] is None
    assert stored["status"] == "queued"
    assert media_file.exists()
    listing = client.get("/api/manager/vision-jobs?limit=20", headers=headers).get_json()["data"]["jobs"]
    assert [job["job_id"] for job in listing] == [job_id]
