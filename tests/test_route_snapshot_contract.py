from types import SimpleNamespace

import agents.tools as tools
from agents.route_state import current_road_condition_version


def test_route_planning_fails_closed_when_road_events_cannot_be_read(monkeypatch):
    graph = object()
    monkeypatch.setattr(tools, "_ensure_graph", lambda: graph)
    monkeypatch.setattr(tools, "list_conditions", lambda **_kwargs: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(tools, "_weather_snapshot", lambda: None)
    monkeypatch.setattr(tools, "compute_route", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not route")))

    result, artifact = tools.execute_tool("plan_route", {
        "start": {"name": "A", "type": "poi"},
        "end": {"name": "B", "type": "poi"},
    })

    assert result["error"] == "road_conditions_unavailable"
    assert artifact is None


def test_route_snapshot_is_reused_and_versioned_from_same_active_event_list(monkeypatch):
    frozen_events = [{"id": "event-v1", "type": "closure"}]
    monkeypatch.setattr(tools, "current_road_condition_version", lambda events=None: current_road_condition_version(events))
    context, error = tools._prepare_route_context({
        "_route_graph_snapshot": object(),
        "_route_conditions_snapshot": frozen_events,
        "_route_weather_snapshot": None,
    })

    assert error is None
    assert context["_route_conditions_snapshot"] is frozen_events
    expected = current_road_condition_version(frozen_events)
    assert tools.current_road_condition_version(context["_route_conditions_snapshot"]) == expected


def test_route_constraints_schema_exposes_explicit_no_steps_request():
    schema = next(item for item in tools.TOOL_SCHEMAS if item["function"]["name"] == "plan_route")

    assert schema["function"]["parameters"]["properties"]["constraints"]["properties"]["avoid_steps"]["type"] == "boolean"
