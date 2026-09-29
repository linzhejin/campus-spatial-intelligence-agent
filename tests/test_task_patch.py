"""State changes preserve omitted intent and reject stale or foreign changes."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError


def contracts():
    # An explicit feature assertion gives the initial red run a useful failure.
    import importlib.util
    assert importlib.util.find_spec("agents.state_models"), "validated task contracts are missing"
    from agents.state_models import TaskState, TaskPatch
    from agents.task_patch import validate_and_apply_patch
    return TaskState, TaskPatch, validate_and_apply_patch


def state():
    TaskState, _, _ = contracts()
    return TaskState.model_validate({
        "task_id": "task-a", "conversation_id": "conversation-a",
        "route_spec": {
            "start": {"poi_id": "poi-yulan2", "name": "玉兰2门"},
            "end": {"poi_id": "poi-xinghu", "name": "星湖园食堂"},
            "stops": [{"poi_id": "poi-lib", "name": "图书馆"}],
            "hard_constraints": {"avoid_slope": True, "avoid_steps": True},
            "data_version": "graph-1", "event_version": "events-1",
        },
    })


def change(current, *operations, **kwargs):
    _, TaskPatch, apply = contracts()
    patch = TaskPatch.model_validate({
        "task_id": current.task_id, "base_revision": current.revision,
        "source_message_id": f"message-{current.revision}",
        "operations": list(operations),
    })
    return apply(current, patch, **kwargs)


def test_mode_patch_preserves_endpoints_stops_constraints_and_input():
    old = state()
    original = old.model_dump()
    new = change(old, {"op": "set", "path": "route_spec.travel_mode", "value": "bike"})
    assert new.revision == old.revision + 1
    assert new.route_spec.travel_mode == "bike"
    assert new.route_spec.start == old.route_spec.start
    assert new.route_spec.end == old.route_spec.end
    assert new.route_spec.stops == old.route_spec.stops
    assert new.route_spec.hard_constraints == old.route_spec.hard_constraints
    assert old.model_dump() == original
    assert new.field_provenance["route_spec.travel_mode"].source_message_id == "message-0"


def test_explicit_remove_cancels_avoid_slope_without_removing_other_constraints():
    new = change(state(), {"op": "remove", "path": "route_spec.hard_constraints.avoid_slope"})
    assert new.route_spec.hard_constraints.avoid_slope is False
    assert new.route_spec.hard_constraints.avoid_steps is True


def test_false_is_an_explicit_value_not_an_omitted_field():
    new = change(state(), {"op": "set", "path": "route_spec.hard_constraints.avoid_slope", "value": False})
    assert new.route_spec.hard_constraints.avoid_slope is False


def test_clear_and_add_stops_are_explicit_and_ordered():
    old = state()
    new = change(old, {"op": "add", "path": "route_spec.stops", "value": {"poi_id": "poi-gym"}})
    assert [stop.poi_id for stop in new.route_spec.stops] == ["poi-lib", "poi-gym"]
    cleared = change(new, {"op": "clear", "path": "route_spec.stops"})
    assert cleared.route_spec.stops == []
    assert old.route_spec.stops[0].poi_id == "poi-lib"


def test_remove_stop_uses_stable_reference():
    new = change(state(), {"op": "remove", "path": "route_spec.stops", "value": {"poi_id": "poi-lib", "name": "图书馆"}})
    assert new.route_spec.stops == []


@pytest.mark.parametrize("path", ["task_id", "status", "revision", "artifact_ids", "route_spec.data_version", "route_spec.event_version", "route_spec", "__dict__", "route_spec.unknown"])
def test_patch_cannot_write_server_owned_or_unknown_fields(path):
    with pytest.raises((ValueError, ValidationError)):
        change(state(), {"op": "set", "path": path, "value": "injected"})


@pytest.mark.parametrize("scope", ["profile", "session"])
def test_task_patch_never_writes_other_memory_scopes(scope):
    with pytest.raises(ValueError, match="scope"):
        change(state(), {"op": "set", "path": "route_spec.travel_mode", "value": "drive", "scope": scope})


def test_stale_revision_and_wrong_task_are_rejected():
    _, TaskPatch, apply = contracts()
    old = state()
    base = {"task_id": old.task_id, "base_revision": 0, "source_message_id": "message-x", "operations": [{"op": "set", "path": "route_spec.travel_mode", "value": "drive"}]}
    updated = change(old, {"op": "set", "path": "route_spec.travel_mode", "value": "bike"})
    with pytest.raises(ValueError, match="revision"):
        apply(updated, TaskPatch.model_validate(base))
    with pytest.raises(ValueError, match="task"):
        apply(old, TaskPatch.model_validate({**base, "task_id": "foreign"}))


def test_invalid_later_operation_rolls_back_whole_patch():
    old = state()
    with pytest.raises(ValueError):
        change(old, {"op": "set", "path": "route_spec.travel_mode", "value": "bike"}, {"op": "set", "path": "route_spec.hard_constraints.avoid_steps", "value": "false"})
    assert old.revision == 0
    assert old.route_spec.travel_mode == "walk"


def test_undo_requires_trusted_same_task_previous_revision_and_keeps_new_revision():
    old = state()
    new = change(old, {"op": "set", "path": "route_spec.travel_mode", "value": "bike"})
    with pytest.raises(ValueError, match="history"):
        change(new, {"op": "undo"})
    reverted = change(new, {"op": "undo"}, trusted_history=[old])
    assert reverted.revision == 2
    assert reverted.route_spec == old.route_spec
    foreign = old.model_copy(update={"conversation_id": "foreign"})
    with pytest.raises(ValueError, match="history"):
        change(new, {"op": "undo"}, trusted_history=[foreign])


def test_undo_cannot_contain_client_supplied_state_or_other_operations():
    _, TaskPatch, _ = contracts()
    common = {"task_id": "a", "base_revision": 0, "source_message_id": "m"}
    for ops in ([{"op": "undo", "value": state().model_dump()}], [{"op": "undo"}, {"op": "clear", "path": "route_spec.stops"}]):
        with pytest.raises(ValidationError):
            TaskPatch.model_validate({**common, "operations": ops})


def test_default_commute_is_pure_shortest_and_contract_forbids_extra_fields():
    TaskState, _, _ = contracts()
    task = TaskState(task_id="a", conversation_id="b")
    assert task.route_spec.strategy.weights.model_dump() == {"distance": 1.0, "slope": 0.0, "scenery": 0.0}
    assert task.route_spec.objective == "shortest_distance"
    with pytest.raises(ValidationError):
        TaskState(task_id="a", conversation_id="b", hidden="ignored")


def test_crs_coordinates_and_stable_location_identifiers():
    contracts()
    from agents.state_models import LocationRef
    # A name-only place is a valid unresolved slot while the agent asks for
    # clarification; coordinates still require an explicit CRS.
    assert LocationRef(name="图书馆").name == "图书馆"
    with pytest.raises(ValidationError):
        LocationRef(coordinates={"longitude": 114.0, "latitude": 30.0})
    valid = LocationRef(coordinates={"longitude": 114.36, "latitude": 30.53, "crs": "WGS84"})
    assert valid.coordinates.crs == "WGS84"
    for lon in (float("nan"), 181.0):
        with pytest.raises(ValidationError):
            LocationRef(coordinates={"longitude": lon, "latitude": 30.0, "crs": "GCJ02"})


def test_timestamps_require_timezone_and_normalize_utc_roundtrip():
    TaskState, _, _ = contracts()
    task = TaskState(task_id="a", conversation_id="b", created_at="2026-09-29T08:00:00+08:00", updated_at="2026-09-29T09:00:00+08:00")
    assert task.created_at.hour == 0
    assert task.created_at.tzinfo == timezone.utc
    assert TaskState.model_validate_json(task.model_dump_json()) == task
    with pytest.raises(ValidationError):
        TaskState(task_id="a", conversation_id="b", created_at=datetime(2026, 9, 29))


def test_twenty_rounds_keep_unmodified_route_intent():
    current = state()
    for i in range(20):
        current = change(current, {"op": "set", "path": "route_spec.travel_mode", "value": "bike" if i % 2 else "walk"})
        assert current.revision == i + 1
        assert current.route_spec.end.poi_id == "poi-xinghu"
        assert current.route_spec.stops[0].poi_id == "poi-lib"
        assert current.route_spec.hard_constraints.avoid_steps
