import os
import uuid

def manager_client(monkeypatch, tmp_path):
    import api.routes as routes
    from app import create_app

    monkeypatch.setattr(routes.config, "ROAD_CONDITION_ADMIN_PASSWORD", "manager-secret")
    monkeypatch.setattr(routes.config, "VISION_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(routes.config, "VISION_MAX_MEDIA_BYTES", 24 * 1024 * 1024)
    monkeypatch.setattr(routes.config, "VISION_MAX_VIDEO_BYTES", 1024, raising=False)
    monkeypatch.setattr(routes.config, "VISION_CHUNK_BYTES", 4, raising=False)
    monkeypatch.setattr(routes, "_vision_inference_readiness", lambda *_args: (True, True, True, True))
    app = create_app()
    app.config.update(TESTING=True, SECRET_KEY="test-secret", DATABASE_URL=None)
    client = app.test_client()
    login = client.post("/api/admin/login", json={"password": "manager-secret"})
    assert login.status_code == 200
    return client, login.get_json()["data"]["csrf_token"]


def test_large_video_upload_resumes_by_offset_and_creates_private_job(monkeypatch, tmp_path):
    import api.routes as routes
    from storage import database, vision_repository

    client, csrf = manager_client(monkeypatch, tmp_path)
    monkeypatch.setattr(database, "initialize", lambda *_args: None)
    created = []

    def create_job(*args, **kwargs):
        created.append(kwargs)
        return {
            "job_id": str(uuid.uuid4()), "status": "queued",
            "original_name": kwargs["original_name"], "media_path": kwargs["media_path"],
            "media_kind": kwargs["media_kind"], "anchor_gcj": kwargs["anchor_gcj"],
            "camera_stabilized": kwargs["camera_stabilized"],
        }

    monkeypatch.setattr(vision_repository, "create_job", create_job)
    headers = {"X-CSRF-Token": csrf}
    started = client.post("/api/manager/vision-uploads", json={
        "filename": "campus.mp4", "size": 12,
        "camera_stabilized": True,
    }, headers=headers)
    assert started.status_code == 201
    upload_id = started.get_json()["data"]["upload_id"]
    assert started.get_json()["data"]["chunk_size"] == 4

    status = client.get(f"/api/manager/vision-uploads/{upload_id}")
    assert status.status_code == 200
    assert status.get_json()["data"]["offset"] == 0

    first = client.put(f"/api/manager/vision-uploads/{upload_id}/chunks",
                       data=b"\x00\x00\x00\x18", headers={**headers, "Upload-Offset": "0"})
    assert first.status_code == 200
    assert first.get_json()["data"]["offset"] == 4
    out_of_order = client.put(f"/api/manager/vision-uploads/{upload_id}/chunks",
                              data=b"isom", headers={**headers, "Upload-Offset": "8"})
    assert out_of_order.status_code == 409

    for offset, chunk in ((4, b"ftyp"), (8, b"isom")):
        response = client.put(f"/api/manager/vision-uploads/{upload_id}/chunks",
                              data=chunk, headers={**headers, "Upload-Offset": str(offset)})
        assert response.status_code == 200
    finished = client.post(f"/api/manager/vision-uploads/{upload_id}/complete",
                           json={}, headers=headers)
    assert finished.status_code == 202
    job = finished.get_json()["data"]["job"]
    assert job["status"] == "queued"
    assert "media_path" not in job and "sha256" not in job
    assert created[0]["camera_stabilized"] is True
    assert created[0]["anchor_gcj"] is None
    assert os.path.isfile(os.path.join(str(tmp_path), created[0]["media_path"]))
    resumed = client.get(f"/api/manager/vision-uploads/{upload_id}")
    assert resumed.status_code == 200
    assert resumed.get_json()["data"]["completed"] is True
    assert resumed.get_json()["data"]["job"]["job_id"] == job["job_id"]
    repeated = client.post(f"/api/manager/vision-uploads/{upload_id}/complete", json={}, headers=headers)
    assert repeated.status_code == 202
    assert repeated.get_json()["data"]["job"]["job_id"] == job["job_id"]
    assert len(created) == 1


def test_video_upload_requires_admin_csrf_and_enforces_declared_size(monkeypatch, tmp_path):
    from app import create_app

    client, csrf = manager_client(monkeypatch, tmp_path)
    started_without_csrf = client.post("/api/manager/vision-uploads", json={
        "filename": "campus.mp4", "size": 12, "lng": 114.363, "lat": 30.5365,
    })
    assert started_without_csrf.status_code == 403
    oversized = client.post("/api/manager/vision-uploads", json={
        "filename": "campus.mp4", "size": 1025, "lng": 114.363, "lat": 30.5365,
    }, headers={"X-CSRF-Token": csrf})
    assert oversized.status_code == 413

    anonymous = create_app().test_client()
    denied = anonymous.post("/api/manager/vision-uploads", json={
        "filename": "campus.mp4", "size": 12, "lng": 114.363, "lat": 30.5365,
    })
    assert denied.status_code == 401
