from types import SimpleNamespace

from langgraph.checkpoint.memory import InMemorySaver


def test_resumed_graph_emits_events_with_the_current_lease_worker(monkeypatch):
    from agents import workflow

    events = []
    monkeypatch.setattr(workflow, "append_run_event", lambda _url, _run, worker, *_args, **_kwargs: (
        events.append(worker) or {"stored": True}
    ))
    monkeypatch.setattr(workflow, "get_run_input", lambda _url, _run: {
        "query": "介绍樱顶", "history": [], "task": {},
    })
    graph = workflow.build_agent_workflow(
        InMemorySaver(),
        agent=lambda *_args, **_kwargs: {"response_kind": "chat", "message": "樱顶在珞珈山上。"},
        interrupt_after=["load_task"],
    )
    config = {"configurable": {"thread_id": "lease-reclaim-test"}}

    graph.invoke(
        {"run_id": "lease-reclaim-test", "worker_id": "worker-old"},
        config,
        context={"worker_id": "worker-old"},
    )
    checkpoint = graph.get_state(config)
    assert checkpoint.values["worker_id"] == "worker-old"
    assert checkpoint.next == ("execute_agent",)

    resumed = graph.invoke(None, config, context={"worker_id": "worker-new"})

    assert resumed["result"]["reply"] == "樱顶在珞珈山上。"
    assert events[:2] == ["worker-old", "worker-old"]
    assert events[2:] and set(events[2:]) == {"worker-new"}


def test_worker_passes_current_identity_when_resuming_a_reclaimed_run(monkeypatch):
    from jobs import worker

    observed = {}
    monkeypatch.setattr(worker, "claim_next_run", lambda *_args: {
        "run_id": "reclaimed-run", "attempt": 2,
    })
    monkeypatch.setattr(worker, "heartbeat_run", lambda *_args: True)
    monkeypatch.setattr(worker, "finish_run", lambda *_args, **_kwargs: True)

    class CheckpointedGraph:
        def get_state(self, _config):
            return SimpleNamespace(values={"run_id": "reclaimed-run", "worker_id": "worker-old"},
                                   next=("execute_agent",))

        def invoke(self, value, _config, *, context):
            observed.update(input=value, context=context)
            return {"result": {"task_type": "chat", "reply": "已完成"}}

    outcome = worker.process_next_run("postgres://test", "worker-new", CheckpointedGraph())

    assert outcome["status"] == "completed"
    assert observed == {"input": None, "context": {"worker_id": "worker-new"}}


def test_systemd_agent_worker_loads_dotenv_before_connecting_to_postgres(monkeypatch):
    from jobs import worker

    calls = []
    monkeypatch.setattr(worker, "load_dotenv", lambda *_args: calls.append("dotenv"), raising=False)
    monkeypatch.setattr(worker, "run_forever", lambda: calls.append("worker"))
    monkeypatch.setattr(worker.signal, "signal", lambda *_args: None)

    worker.main()

    assert calls == ["dotenv", "worker"]


def test_agent_worker_supervisor_replaces_a_slot_that_dies_during_startup(monkeypatch):
    from jobs import worker

    stop = worker.threading.Event()
    replacement_ready = worker.threading.Event()
    attempts = {0: 0, 1: 0}

    def target(_database_url, _idle_seconds, slot):
        attempts[slot] += 1
        if slot == 0:
            replacement_ready.wait(timeout=1)
        elif attempts[slot] > 1:
            replacement_ready.set()
            stop.set()
        else:
            raise RuntimeError("startup connection failed")

    worker._supervise_workers(
        "postgres://test", 0.001, 2, stop_event=stop,
        worker_target=target, poll_seconds=0.001, restart_delay_seconds=0.001,
    )

    assert attempts == {0: 1, 1: 2}


def test_vision_readiness_requires_a_live_ready_worker(monkeypatch, tmp_path):
    import importlib.util
    from api import routes
    from storage import database, vision_repository

    model = tmp_path / "model.pt"
    model.write_bytes(b"model")
    monkeypatch.setattr(routes.config, "VISION_MODEL_PATH", str(model))
    monkeypatch.setattr(database, "initialize", lambda *_args: None)
    monkeypatch.setattr(vision_repository, "has_ready_worker", lambda *_args, **_kwargs: False)

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name == "PIL" else object())
    assert routes._vision_inference_readiness("postgres://test") == (False, True, False, False)

    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())
    assert routes._vision_inference_readiness("postgres://test") == (False, True, True, False)

    monkeypatch.setattr(vision_repository, "has_ready_worker", lambda *_args, **_kwargs: True)
    assert routes._vision_inference_readiness("postgres://test") == (True, True, True, True)


def test_vision_worker_heartbeat_uses_freshness_and_worker_identity(monkeypatch):
    from contextlib import contextmanager
    from storage import vision_repository

    statements = []

    class Result:
        def fetchone(self):
            return {"ready": True}

    class Connection:
        def execute(self, sql, params):
            statements.append((sql, params))
            return Result()

    @contextmanager
    def fake_connect(_url):
        yield Connection()

    monkeypatch.setattr(vision_repository.database, "connect", fake_connect)

    vision_repository.heartbeat_worker(
        "postgres://test", "worker-a", ready=True, status_detail="loaded",
    )
    assert vision_repository.has_ready_worker(
        "postgres://test", max_age_seconds=12, worker_id="worker-a",
    )

    assert "ON CONFLICT(worker_id)" in statements[0][0]
    assert statements[0][1] == ("worker-a", True, "loaded")
    assert "heartbeat_at > now()" in statements[1][0]
    assert statements[1][1] == (12, "worker-a")
