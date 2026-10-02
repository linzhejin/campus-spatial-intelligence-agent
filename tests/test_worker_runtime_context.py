from concurrent.futures import ThreadPoolExecutor
import json
import threading
from pathlib import Path
from types import SimpleNamespace

from langgraph.checkpoint.memory import InMemorySaver


def _c1_runtime_case(case_id):
    project_root = Path(__file__).resolve().parents[1]
    replay = json.loads(
        (project_root / "experiments/datasets/c1_production_context_replay_v1.json")
        .read_text(encoding="utf-8")
    )
    return next(case for case in replay["runtime_cases"] if case["id"] == case_id)


def test_c1_runtime_case_manifest_matches_context_and_cancellation_regressions():
    project_root = Path(__file__).resolve().parents[1]
    replay = json.loads(
        (project_root / "experiments/datasets/c1_production_context_replay_v1.json")
        .read_text(encoding="utf-8")
    )
    runtime_cases = replay["runtime_cases"]

    assert {case["id"] for case in runtime_cases} == {
        "parallel_runs_keep_contexts_isolated",
        "lease_loss_stops_before_agent",
        "late_result_after_invalidation_is_not_published",
    }
    for case in runtime_cases:
        assert case["implemented_by"] in globals()
        assert callable(globals()[case["implemented_by"]])


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


def test_clarification_answer_keeps_original_multitool_request(monkeypatch):
    from agents import workflow

    original = "骑车从玉兰2门到樱顶，经过卓尔体育馆，避开台阶，顺便查天气"
    observed = {}
    monkeypatch.setattr(workflow, "append_run_event", lambda *_args, **_kwargs: {"stored": True})
    monkeypatch.setattr(workflow, "get_run_input", lambda _url, _run: {
        "query": "从信息学部出发", "task_origin_query": original,
        "task_revision": 2,
        "history": [
            {"role": "user", "content": original},
            {"role": "assistant", "content": "你想从哪里出发？"},
        ],
        "task": {"route_spec": {"travel_mode": "bike"}},
    })
    monkeypatch.setattr(workflow, "_run_weather_tool", lambda: {"weather": "晴", "temperature": 22})

    def fake_agent(query, *, context, travel_mode, **_kwargs):
        observed.update(query=query, context=context, travel_mode=travel_mode)
        return {"response_kind": "chat", "message": "路线已规划"}

    graph = workflow.build_agent_workflow(InMemorySaver(), agent=fake_agent)
    result = graph.invoke(
        {"run_id": "clarification-multitool"},
        {"configurable": {"thread_id": "clarification-multitool"}},
        context={"worker_id": "worker-a"},
    )["result"]

    assert observed["query"] == "从信息学部出发"
    assert observed["context"]["active_task_request"] == original
    assert observed["travel_mode"] == "bike"
    assert result["requirement_results"] == {"route": "satisfied", "weather": "satisfied"}
    assert result["weather"]["temperature"] == 22


def test_clarification_of_new_trip_does_not_import_older_completed_route(monkeypatch):
    from agents import workflow
    from agents.route_state import build_route_state

    observed = {}
    prior = json.loads((Path(__file__).resolve().parents[1]
                        / "experiments/datasets/c1_production_context_replay_v1.json")
                       .read_text(encoding="utf-8"))["prior_route_state"]
    monkeypatch.setattr(workflow, "append_run_event", lambda *_args, **_kwargs: {"stored": True})
    monkeypatch.setattr(workflow, "get_run_input", lambda _url, _run: {
        "query": "从信息学部出发", "task_origin_query": "去卓尔体育馆，避开台阶",
        "task_id": "current-task", "task_revision": 2,
        "history": [{"role": "user", "content": "去卓尔体育馆，避开台阶"},
                    {"role": "assistant", "content": "你从哪里出发？"}],
        "task": {"route_spec": {}},
        "previous_route_state": build_route_state(**prior),
        "previous_route_state_task_id": "older-task",
    })

    def fake_agent(_query, *, context, **_kwargs):
        observed.update(context)
        return {"response_kind": "chat", "message": "继续当前任务"}

    workflow.build_agent_workflow(InMemorySaver(), agent=fake_agent).invoke(
        {"run_id": "no-cross-task-route"},
        {"configurable": {"thread_id": "no-cross-task-route"}},
        context={"worker_id": "worker-a"},
    )
    assert "previous_route_state" not in observed
    assert observed["active_task_request"] == "去卓尔体育馆，避开台阶"


def test_production_workflow_keeps_concurrent_task_contexts_isolated(monkeypatch):
    from agents import workflow
    from agents.route_state import build_route_state

    case = _c1_runtime_case("parallel_runs_keep_contexts_isolated")
    inputs = {}
    for run in case["runs"]:
        inputs[run["run_id"]] = {
            "query": run["query"],
            "history": [],
            "task_revision": run["task_revision"],
            "task": {"route_spec": {}},
            "previous_route_state": build_route_state(**run["previous_route_state"]),
        }
    monkeypatch.setattr(workflow, "get_run_input", lambda _url, run_id: inputs[run_id])
    monkeypatch.setattr(workflow, "append_run_event", lambda *_args, **_kwargs: {"stored": True})

    rendezvous = threading.Barrier(2)
    observed = {}

    def fake_agent(query, *, context, **_kwargs):
        rendezvous.wait(timeout=3)
        observed[query] = context
        return {"response_kind": "chat", "message": f"完成：{query}"}

    graph = workflow.build_agent_workflow(InMemorySaver(), agent=fake_agent)

    def invoke(run_id):
        return graph.invoke(
            {"run_id": run_id},
            {"configurable": {"thread_id": run_id}},
            context={"worker_id": f"worker-{run_id}"},
        )

    run_ids = [run["run_id"] for run in case["runs"]]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, run_ids))

    for query, expected_route_id in case["expected_route_id_by_query"].items():
        context = observed[query]
        if expected_route_id is None:
            assert "previous_route_state" not in context
            assert "previous_intent" not in context
        else:
            assert context["previous_route_state"]["route_id"] == expected_route_id
    assert [result["result"]["reply"] for result in results] == [
        f"完成：{run['query']}" for run in case["runs"]
    ]


def test_production_workflow_stops_before_agent_when_run_lease_is_lost(monkeypatch):
    import pytest
    from agents import workflow

    case = _c1_runtime_case("lease_loss_stops_before_agent")
    monkeypatch.setattr(workflow, "append_run_event", lambda *_args, **_kwargs: None)
    agent_calls = []
    graph = workflow.build_agent_workflow(
        InMemorySaver(),
        agent=lambda *_args, **_kwargs: agent_calls.append("called"),
    )

    with pytest.raises(RuntimeError, match=case["expected_error"]):
        graph.invoke(
            {"run_id": case["run_id"]},
            {"configurable": {"thread_id": case["run_id"]}},
            context={"worker_id": case["worker_id"]},
        )

    assert len(agent_calls) == case["expected_agent_calls"]


def test_worker_does_not_publish_result_after_cancellation_or_lease_loss(monkeypatch):
    from jobs import worker

    case = _c1_runtime_case("late_result_after_invalidation_is_not_published")
    heartbeat_failed = threading.Event()
    finish_calls = []
    monkeypatch.setattr(worker, "claim_next_run", lambda *_args: {
        "run_id": case["run_id"], "attempt": 1,
    })

    def lose_lease(*_args):
        heartbeat_failed.set()
        return False

    monkeypatch.setattr(worker, "heartbeat_run", lose_lease)
    monkeypatch.setattr(worker, "finish_run", lambda *_args, **_kwargs: finish_calls.append(True))

    class SlowGraph:
        def invoke(self, _value, _config, *, context):
            assert context["worker_id"] == case["worker_id"]
            assert heartbeat_failed.wait(timeout=2)
            # Simulate a route/LLM call that returns after its run was cancelled.
            return {"result": {"task_type": "path_planning", "reply": case["late_reply"]}}

    result = worker.process_next_run(
        "postgres://test", case["worker_id"], SlowGraph(), lease_seconds=3,
    )

    assert result == {"run_id": case["run_id"], "status": case["expected_status"]}
    assert len(finish_calls) == case["expected_finish_calls"]


def test_c1_production_context_replay_cases_follow_route_inheritance_policy(monkeypatch):
    from agents import workflow

    project_root = Path(__file__).resolve().parents[1]
    replay = json.loads(
        (project_root / "experiments/datasets/c1_production_context_replay_v1.json")
        .read_text(encoding="utf-8")
    )
    route_state = replay["prior_route_state"]
    inputs = {
        f"c1-{case['id']}": {
            "query": case["query"],
            "history": replay["history"],
            "task_revision": case["task_revision"],
            "task": {"route_spec": {}},
            "previous_route_state": route_state,
        }
        for case in replay["cases"]
    }
    monkeypatch.setattr(workflow, "get_run_input", lambda _url, run_id: inputs[run_id])
    monkeypatch.setattr(workflow, "append_run_event", lambda *_args, **_kwargs: {"stored": True})
    active_case = {"fail_weather": False, "fail_route": False, "query": ""}

    def weather_tool():
        if active_case["fail_weather"]:
            raise RuntimeError("simulated weather outage")
        return {"weather": "多云", "temperature": 23, "windpower": "2级", "advice": "适合步行"}

    monkeypatch.setattr(workflow, "_run_weather_tool", weather_tool)
    observed = {}

    def fake_agent(query, *, context, **_kwargs):
        observed[query] = context
        if active_case["fail_route"] and query == active_case["query"]:
            raise RuntimeError("simulated route agent failure")
        return {"response_kind": "chat", "message": f"AGENT:{query}"}

    graph = workflow.build_agent_workflow(InMemorySaver(), agent=fake_agent)
    results = {}
    for case in replay["cases"]:
        active_case.update(
            fail_weather=case.get("fail_weather", False),
            fail_route=case.get("fail_route", False),
            query=case["query"],
        )
        run_id = f"c1-{case['id']}"
        results[case["id"]] = graph.invoke(
            {"run_id": run_id},
            {"configurable": {"thread_id": run_id}},
            context={"worker_id": f"worker-{run_id}"},
        )

    for case in replay["cases"]:
        context = observed[case["query"]]
        has_previous_route = "previous_route_state" in context
        assert has_previous_route is case["inherit_route_state"], case["id"]
        if has_previous_route:
            inherited = context["previous_route_state"]
            assert inherited["route_id"] == route_state["route_id"]
            assert inherited["route_kind"] == "via"
            assert inherited["via"] == route_state["via"]
            assert inherited["travel_mode"] == "walk"
            assert inherited["strategy"] == route_state["strategy"]
            assert inherited["hard_constraints"] == route_state["hard_constraints"]

    for case in replay["cases"]:
        if case.get("execution_kind") != "composite":
            continue
        result = results[case["id"]]["result"]
        assert result["execution_status"] == case.get("expected_status", "completed")
        assert result["requirement_results"]["route"] == (
            "failed" if case.get("fail_route") else "satisfied"
        )
        assert result["requirement_results"]["weather"] == (
            "failed" if case.get("fail_weather") else "satisfied"
        )
        if case.get("fail_weather"):
            assert result["weather"] is None
            assert result["requirement_errors"]["weather"] == "RuntimeError"
            assert f"AGENT:{case['query']}" in result["reply"]
        else:
            assert result["weather"]["temperature"] == 23
            assert workflow._weather_message(result["weather"]) in result["reply"]
        if case.get("fail_route"):
            assert result["requirement_errors"]["route"] == "RuntimeError"
            assert "路线暂时无法规划" in result["reply"]
        else:
            assert f"AGENT:{case['query']}" in result["reply"]


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
