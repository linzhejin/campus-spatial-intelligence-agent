"""P4 埋点端点 + uid 透传单测。"""

import json
from unittest.mock import patch

import pytest

from agents import profile
from api.routes import _TELEMETRY_PATH


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, "_PROFILES_PATH", tmp_path / "profiles.json")
    monkeypatch.setattr(profile, "_cache", None)
    monkeypatch.setattr("api.routes._TELEMETRY_PATH", tmp_path / "telemetry.jsonl")
    yield


@pytest.fixture
def client():
    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


W = {"distance": 0.7, "slope": 0.2, "scenery": 0.1}


class TestTelemetry:
    def test_navigation_start_updates_profile_once_with_complete_route_signal(self, client):
        r = client.post("/api/telemetry",
                        json={
                            "uid": "u1", "event": "navigation_started",
                            "route_id": "r1", "strategy": "scenery",
                            "strategy_source": "button", "applied_weights": W,
                        })
        assert r.status_code == 200
        assert r.get_json()["data"]["recorded"] is True
        p = profile.get_profile("u1")
        assert p["accepted_count"] == 1

        client.post("/api/telemetry", json={
            "uid": "u1", "event": "navigation_completed",
            "route_id": "r1", "strategy": "scenery",
            "strategy_source": "button", "applied_weights": W,
        })
        assert profile.get_profile("u1")["accepted_count"] == 1

    def test_route_shown_only_exposure(self, client):
        client.post("/api/telemetry",
                    json={
                        "uid": "u1", "event": "route_shown", "route_id": "r1",
                        "strategy": "scenery", "strategy_source": "button",
                        "applied_weights": W,
                    })
        p = profile.get_profile("u1")
        assert p["exposure_count"] == 1
        assert p["accepted_count"] == 0

    def test_strategy_selection_does_not_update_weights(self, client):
        client.post("/api/telemetry", json={
            "uid": "u1", "event": "strategy_selected", "route_id": "r1",
            "strategy": "flat", "strategy_source": "button",
            "applied_weights": W,
        })
        assert profile.get_profile("u1")["accepted_count"] == 0

    def test_route_signal_missing_identity_is_not_recorded(self, client):
        r = client.post("/api/telemetry", json={
            "uid": "u1", "event": "navigation_started", "applied_weights": W,
        })
        assert r.get_json()["data"]["recorded"] is False
        assert profile.get_profile("u1") is None

    def test_events_written_to_jsonl(self, client, tmp_path):
        client.post("/api/telemetry",
                    json={"uid": "u1", "event": "candidate_click", "name": "梅园食堂"})
        lines = (tmp_path / "telemetry.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["event"] == "candidate_click"
        assert rec["payload"]["name"] == "梅园食堂"

    def test_invalid_event_not_recorded(self, client):
        r = client.post("/api/telemetry", json={"uid": "u1", "event": "hack"})
        assert r.status_code == 200
        assert r.get_json()["data"]["recorded"] is False

    def test_missing_uid_not_recorded(self, client):
        r = client.post("/api/telemetry", json={
            "event": "navigation_started", "route_id": "r1",
            "strategy": "scenery", "strategy_source": "button", "applied_weights": W,
        })
        assert r.status_code == 200
        assert r.get_json()["data"]["recorded"] is False

    def test_garbage_body_still_ok(self, client):
        r = client.post("/api/telemetry", data="not json", content_type="application/json")
        assert r.status_code == 200


class TestChatUidPassthrough:
    def test_chat_passes_uid_to_agent(self, client):
        fake_resp = {"response_kind": "chat", "message": "hi", "route": None,
                     "route_kind": None, "candidates": None, "clarify": None, "turns": 1}
        with patch("agents.planner.run_agent", return_value=fake_resp) as mock_run:
            r = client.post("/api/chat", json={"query": "你好", "whu_uid": "u_abc"})
        assert r.status_code == 200
        assert mock_run.call_args.kwargs["uid"] == "u_abc"

    def test_chat_without_uid_passes_none(self, client):
        fake_resp = {"response_kind": "chat", "message": "hi", "route": None,
                     "route_kind": None, "candidates": None, "clarify": None, "turns": 1}
        with patch("agents.planner.run_agent", return_value=fake_resp) as mock_run:
            r = client.post("/api/chat", json={"query": "你好"})
        assert r.status_code == 200
        assert mock_run.call_args.kwargs["uid"] is None
