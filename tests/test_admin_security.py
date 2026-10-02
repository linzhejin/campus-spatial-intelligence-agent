import pytest


@pytest.mark.parametrize("secret", [None, "whu-walker-dev-secret-change-me", "short"])
def test_production_refuses_missing_or_weak_session_secret(monkeypatch, secret):
    from app import create_app

    monkeypatch.setenv("FLASK_ENV", "production")
    if secret is None:
        monkeypatch.delenv("SECRET_KEY", raising=False)
    else:
        monkeypatch.setenv("SECRET_KEY", secret)

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app()


def test_private_agent_api_responses_are_never_cacheable():
    from app import create_app

    app = create_app()
    app.config.update(TESTING=True, SECRET_KEY="test-secret")
    response = app.test_client().get("/api/conversations/not-a-real-id")

    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Vary"] == "Cookie"


def test_session_admin_mutations_require_csrf_token(monkeypatch):
    import api.routes as routes
    from app import create_app

    monkeypatch.setattr(routes.config, "ROAD_CONDITION_ADMIN_PASSWORD", "manager-secret")
    app = create_app()
    app.config.update(TESTING=True, SECRET_KEY="test-secret")
    client = app.test_client()
    assert client.post("/api/admin/login", json={"password": "manager-secret"}).status_code == 200
    status = client.get("/api/admin/status").get_json()["data"]
    assert status["is_admin"] is True
    assert status.get("csrf_token")

    called = []
    monkeypatch.setattr(routes, "update_condition", lambda cond_id, changes: called.append(cond_id) or {
        "id": cond_id, "type": "closure", "name": "封路", "end_time": 123,
    })
    denied = client.patch("/api/road-conditions/event-1", json={"action": "end"})
    assert denied.status_code == 403
    assert called == []

    allowed = client.patch(
        "/api/road-conditions/event-1", json={"action": "end"},
        headers={"X-CSRF-Token": status["csrf_token"]},
    )
    assert allowed.status_code == 200
    assert called == ["event-1"]


def test_vision_model_status_jobs_and_media_require_manager_authentication():
    from app import create_app

    app = create_app()
    app.config.update(TESTING=True, SECRET_KEY="test-secret")
    client = app.test_client()
    for path in (
        "/api/manager/vision-status",
        "/api/manager/vision-jobs?limit=1",
        "/api/manager/vision-jobs/00000000-0000-0000-0000-000000000000/media",
    ):
        response = client.get(path)
        assert response.status_code == 401
