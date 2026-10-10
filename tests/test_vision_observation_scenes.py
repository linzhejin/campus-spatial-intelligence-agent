import uuid
from types import SimpleNamespace


def manager_client(monkeypatch, tmp_path):
    import api.routes as routes
    from app import create_app

    monkeypatch.setattr(routes.config, "ROAD_CONDITION_ADMIN_PASSWORD", "manager-secret")
    monkeypatch.setattr(routes.config, "VISION_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "_vision_inference_readiness", lambda *_args: (True, True, True, True))
    app = create_app()
    app.config.update(TESTING=True, SECRET_KEY="test-secret", DATABASE_URL=None)
    client = app.test_client()
    login = client.post("/api/admin/login", json={"password": "manager-secret"})
    assert login.status_code == 200
    return client, login.get_json()["data"]["csrf_token"]


def test_manager_can_save_and_list_a_reusable_scene_with_server_snapped_road_links(
    monkeypatch, tmp_path,
):
    import api.routes as routes
    from storage import database

    client, csrf = manager_client(monkeypatch, tmp_path)
    monkeypatch.setattr(database, "initialize", lambda *_args: None)
    monkeypatch.setattr(routes, "_ensure_network", lambda: (object(), None))
    snap = {
        "u": 11, "v": 12, "key": 0, "edges": [[11, 12, 0]],
        "chain_length_m": 83.4, "road_name": "樱园西路", "dist_m": 1.7,
        "snap_lng_gcj": 114.36, "snap_lat_gcj": 30.54,
        "geometry_gcj": [[114.36, 30.54], [114.361, 30.541]],
    }
    snapped_points = []

    def snap_to_edge(_graph, lng, lat):
        snapped_points.append((lng, lat))
        return snap

    monkeypatch.setattr(routes, "snap_to_edge", snap_to_edge)
    saved = []

    def create_scene(_database_url, *, name, created_by, anchor_gcj,
                     camera_stabilized, frame_signature, observation_regions):
        record = {
            "scene_id": str(uuid.uuid4()), "name": name, "created_by": created_by,
            "anchor_gcj": anchor_gcj, "camera_stabilized": camera_stabilized,
            "frame_signature": frame_signature,
            "observation_regions": observation_regions,
        }
        saved.append(record)
        return record

    monkeypatch.setattr(routes, "vision_scene_repository", SimpleNamespace(
        create_scene=create_scene, list_scenes=lambda *_args: list(saved),
    ), raising=False)
    body = {
        "name": "工学部东门机位",
        "anchor_gcj": {"lng": 114.36, "lat": 30.54},
        "camera_stabilized": True,
        "frame_signature": "0123456789abcdef",
        "observation_regions": [{
            "id": "east-lane", "kind": "vehicle_lane",
            "polygon": [[0.1, 0.2], [0.8, 0.2], [0.8, 0.7], [0.1, 0.7]],
            "road_point_gcj": {"lng": 114.3601, "lat": 30.5401},
        }],
    }
    headers = {"X-CSRF-Token": csrf}

    response = client.post("/api/manager/vision-scenes", json=body, headers=headers)

    assert response.status_code == 201
    created = response.get_json()["data"]["scene"]
    assert created["observation_regions"][0]["road_link"]["road_name"] == "樱园西路"
    assert created["observation_regions"][0]["road_link"]["edges"] == [[11, 12, 0]]
    assert snapped_points == [(114.3601, 30.5401)]
    listed = client.get("/api/manager/vision-scenes")
    assert listed.status_code == 200
    assert listed.get_json()["data"]["scenes"][0]["scene_id"] == created["scene_id"]


def test_scene_save_requires_road_binding_for_each_analysis_region(monkeypatch, tmp_path):
    import api.routes as routes

    client, csrf = manager_client(monkeypatch, tmp_path)
    monkeypatch.setattr(routes, "_ensure_network", lambda: (object(), None))
    response = client.post("/api/manager/vision-scenes", json={
        "name": "未绑定道路的场景",
        "anchor_gcj": {"lng": 114.36, "lat": 30.54},
        "camera_stabilized": False,
        "frame_signature": "0123456789abcdef",
        "observation_regions": [{
            "id": "walkway", "kind": "pedestrian",
            "polygon": [[0, 0], [1, 0], [1, 1]],
        }],
    }, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 400
    assert response.get_json()["error"] == "vision_scene_road_link_required"


def test_scene_save_requires_admin_and_csrf(monkeypatch, tmp_path):
    from app import create_app

    client, _csrf = manager_client(monkeypatch, tmp_path)
    no_csrf = client.post("/api/manager/vision-scenes", json={})
    assert no_csrf.status_code == 403

    anonymous = create_app().test_client()
    denied = anonymous.get("/api/manager/vision-scenes")
    assert denied.status_code == 401


def test_manager_can_update_existing_scene_after_rebinding(monkeypatch, tmp_path):
    import api.routes as routes
    from storage import database

    client, csrf = manager_client(monkeypatch, tmp_path)
    monkeypatch.setattr(database, "initialize", lambda *_args: None)
    scene_id = str(uuid.uuid4())
    existing = {
        "scene_id": scene_id, "name": "东门机位", "anchor_gcj": {"lng": 114.36, "lat": 30.54},
        "camera_stabilized": False, "frame_signature": "0123456789abcdef",
        "observation_regions": [],
    }
    updated = []

    def update_scene(_url, target_id, **fields):
        record = {**existing, **fields, "scene_id": target_id}
        updated.append(record)
        return record

    monkeypatch.setattr(routes, "vision_scene_repository", SimpleNamespace(
        list_scenes=lambda *_args: [existing], update_scene=update_scene,
    ))
    response = client.post("/api/manager/vision-scenes", json={
        "scene_id": scene_id, "name": "东门机位",
        "anchor_gcj": {"lng": 114.361, "lat": 30.541},
        "camera_stabilized": True, "frame_signature": "fedcba9876543210",
        "observation_regions": [{
            "id": "parking", "kind": "parking", "polygon": [[0, 0], [1, 0], [1, 1]],
        }],
    }, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert updated[0]["scene_id"] == scene_id
    assert updated[0]["observation_regions"][0]["kind"] == "parking"


def test_scene_signature_hamming_distance_is_bounded():
    from vision.scene_signature import signature_hamming_distance

    assert signature_hamming_distance("0000000000000000", "0000000000000000") == 0
    assert signature_hamming_distance("0000000000000000", "0000000000000001") == 1
    assert signature_hamming_distance("0000000000000000", "ffffffffffffffff") == 64


def test_scene_template_is_server_checked_before_image_job_is_queued(monkeypatch, tmp_path):
    import io

    from PIL import Image
    import api.routes as routes
    from storage import database

    client, csrf = manager_client(monkeypatch, tmp_path)
    monkeypatch.setattr(database, "initialize", lambda *_args: None)
    image_bytes = io.BytesIO()
    Image.new("RGB", (32, 32), (40, 90, 120)).save(image_bytes, format="PNG")
    payload = image_bytes.getvalue()
    image_path = tmp_path / "signature.png"
    image_path.write_bytes(payload)
    from vision.scene_signature import media_signature
    actual_signature = media_signature(image_path, "image")
    scene_id = str(uuid.uuid4())
    scene = {
        "scene_id": scene_id, "name": "工学部东门机位", "frame_signature": actual_signature,
        "anchor_gcj": {"lng": 114.36, "lat": 30.54, "crs": "GCJ02"},
        "camera_stabilized": True,
        "observation_regions": [{
            "id": "exclude-1", "kind": "exclude",
            "polygon": [[0, 0], [1, 0], [1, 1]],
        }],
    }
    monkeypatch.setattr(routes.vision_scene_repository, "get_scene", lambda *_args: scene)
    created = []
    from storage import vision_repository
    monkeypatch.setattr(vision_repository, "create_job", lambda _url, **fields: created.append(fields) or {
        "job_id": str(uuid.uuid4()), "status": "queued", "created_at": "2026-10-10T00:00:00Z",
    })
    response = client.post(
        "/api/manager/vision-jobs",
        data={
            "media": (io.BytesIO(payload), "drone.png"),
            "captured_at": "2026-10-10T10:00:00+08:00", "scene_id": scene_id,
            "lng": "114.361", "lat": "30.541", "camera_stabilized": "false",
            "observation_regions": '[{"id":"updated","kind":"parking","polygon":[[0,0],[1,0],[1,1]]}]',
        },
        headers={"X-CSRF-Token": csrf}, content_type="multipart/form-data",
    )
    assert response.status_code == 202
    assert created[0]["observation_scene_id"] == scene_id
    assert created[0]["anchor_gcj"]["lng"] == 114.361
    assert created[0]["camera_stabilized"] is False
    assert created[0]["observation_regions"][0]["id"] == "updated"
    assert created[0]["observation_regions"][0]["kind"] == "parking"


def test_scene_view_mismatch_rejects_upload_without_queueing(monkeypatch, tmp_path):
    import io

    from PIL import Image
    import api.routes as routes
    from storage import database, vision_repository

    client, csrf = manager_client(monkeypatch, tmp_path)
    monkeypatch.setattr(database, "initialize", lambda *_args: None)
    payload_stream = io.BytesIO()
    Image.new("RGB", (32, 32), (40, 90, 120)).save(payload_stream, format="PNG")
    payload = payload_stream.getvalue()
    scene = {
        "scene_id": str(uuid.uuid4()), "name": "旧机位", "frame_signature": "000000000000ffff",
        "anchor_gcj": {"lng": 114.36, "lat": 30.54}, "camera_stabilized": False,
        "observation_regions": [{
            "id": "exclude-1", "kind": "exclude", "polygon": [[0, 0], [1, 0], [1, 1]],
        }],
    }
    monkeypatch.setattr(routes.vision_scene_repository, "get_scene", lambda *_args: scene)
    queued = []
    monkeypatch.setattr(vision_repository, "create_job", lambda *_args, **kwargs: queued.append(kwargs))
    response = client.post(
        "/api/manager/vision-jobs",
        data={"media": (io.BytesIO(payload), "drone.png"),
              "captured_at": "2026-10-10T10:00:00+08:00", "scene_id": scene["scene_id"]},
        headers={"X-CSRF-Token": csrf}, content_type="multipart/form-data",
    )
    assert response.status_code == 409
    assert response.get_json()["error"] == "vision_scene_view_mismatch"
    assert not queued
