from storage import database, vision_repository


def test_job_cancel_poll_is_read_only_and_scoped_to_the_current_active_lease(monkeypatch):
    calls = {}

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, params):
            calls["statement"] = statement
            calls["params"] = params
            return self

        def fetchone(self):
            return {"cancel_requested": True}

    monkeypatch.setattr(database, "connect", lambda url: FakeConnection())

    result = vision_repository.get_job_cancel_state("postgres://test", "job-1", "worker-1")

    assert result == {"cancel_requested": True}
    assert calls["params"] == ("job-1", "worker-1")
    assert "SELECT cancel_requested" in calls["statement"]
    assert "worker_id=%s" in calls["statement"]
    assert "status='running'" in calls["statement"]
    assert "lease_until>now()" in calls["statement"]
