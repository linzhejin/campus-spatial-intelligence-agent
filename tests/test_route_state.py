from copy import deepcopy

import pytest

from agents.route_state import apply_change, validate_route_state


BASE = {
    "schema_version": 1,
    "route_id": "route-test",
    "route_kind": "via",
    "original_query": "从牌坊经樱顶到图书馆",
    "start": {"name": "牌坊", "type": "poi"},
    "end": {"name": "图书馆", "type": "poi"},
    "via": {"name": "樱顶", "type": "poi"},
    "tour": None,
    "legs": [],
    "travel_mode": "walk",
    "hard_constraints": {"slope": "normal"},
    "strategy": {
        "name": "shortest",
        "source": "commute_default",
        "task_class": "commute",
        "weights": {"distance": 1.0, "slope": 0.0, "scenery": 0.0},
        "detour_cap": 1.0,
    },
    "data_version": "data-v1",
    "road_condition_version": "roads-v1",
}


def test_mode_change_preserves_every_route_semantic_field():
    changed = apply_change(validate_route_state(BASE), {"travel_mode": "bike"})

    assert changed["travel_mode"] == "bike"
    for key in (
        "route_kind",
        "original_query",
        "start",
        "end",
        "via",
        "tour",
        "legs",
        "hard_constraints",
        "strategy",
    ):
        assert changed[key] == BASE[key]
    assert changed["route_id"] != BASE["route_id"]


def test_strategy_change_preserves_hard_constraints_and_route_shape():
    changed = apply_change(validate_route_state(BASE), {"strategy": "flat"})

    assert changed["strategy"] == {"name": "flat", "source": "button"}
    assert changed["hard_constraints"] == BASE["hard_constraints"]
    assert changed["start"] == BASE["start"]
    assert changed["end"] == BASE["end"]
    assert changed["via"] == BASE["via"]


def test_validation_sanitizes_client_computed_fields_without_mutating_input():
    poisoned = deepcopy(BASE)
    poisoned.update(
        recommended_edge_ids=[[1, 2, 0]],
        distance_m=1,
        recommended=[1, 2],
    )

    clean = validate_route_state(poisoned)

    assert "recommended_edge_ids" not in clean
    assert "distance_m" not in clean
    assert "recommended" not in clean
    assert poisoned["recommended_edge_ids"] == [[1, 2, 0]]


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"travel_mode": "bike", "strategy": "flat"},
        {"unknown": "value"},
    ],
)
def test_rejects_zero_multiple_or_unknown_changes(change):
    with pytest.raises(ValueError, match="exactly one"):
        apply_change(BASE, change)


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("schema_version", 99, "schema_version"),
        ("route_kind", "other", "route_kind"),
        ("travel_mode", "fly", "travel_mode"),
        ("start", None, "endpoints"),
    ],
)
def test_rejects_invalid_core_state(field, value, message):
    raw = deepcopy(BASE)
    raw[field] = value

    with pytest.raises(ValueError, match=message):
        validate_route_state(raw)
