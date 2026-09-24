"""用户明确偏好与普通通勤的边界；不让历史游览偏好污染新行程。"""

import pytest


@pytest.mark.parametrize("query,expected", [
    ("从教五到图书馆", False),
    ("从珞珈门到樱花大道", False),
    ("去樱顶上课，快一点", False),
    ("走最短路线，不看风景", False),
    ("从教五到图书馆，避开陡坡", True),
    ("赶时间，但还是要避开陡坡", True),
    ("从教五到图书馆，走风景好的路", True),
    ("随便走走", True),
    ("膝盖不好，去樱顶", True),
    ("从教五到图书馆，景色好一点", True),
    ("从教五到图书馆，不想上坡", True),
    ("从教五到运动场", False),
])
def test_preference_must_come_from_user(query, expected):
    from agents.preferences import route_preference_requested
    assert route_preference_requested(query) is expected


def test_followup_keeps_explicit_preference():
    from agents.preferences import route_preference_requested
    context = {"history": [{"role": "user", "content": "从教五到樱顶，避开陡坡"}]}
    assert route_preference_requested("还是骑车吧", context)
    assert not route_preference_requested("从梅园到桂园上课", context)
    assert not route_preference_requested("改成最短路线", context)


def test_legacy_default_weights_do_not_depend_on_destination():
    from api.routes import _weights_for_destination, SHORTCUT_MODE_PRESETS
    commute = {"distance": 0.90, "slope": 0.05, "scenery": 0.05}
    for mode in ("walk", "bike", "drive"):
        for poi in ({"type": "study"}, {"type": "scenery"}, {"type": "coord"}):
            assert _weights_for_destination(poi, mode) == commute
    assert SHORTCUT_MODE_PRESETS["distance_first"]["weights"] == commute


def test_preference_survives_agent_start_clarification():
    from agents.preferences import route_preference_requested
    context = {"history": [
        {"role": "user", "content": "想去樱顶，走风景好的路"},
        {"role": "assistant", "content": "你从哪里出发？"},
    ]}
    assert route_preference_requested("珞珈门", context)
    assert not route_preference_requested("从教五到图书馆", context)


def test_legacy_commute_clears_unrequested_weights():
    from agents.parser import TaskIntent, _t011_post_process
    intent = TaskIntent(task_type="path_planning", constraints={"distance": "medium", "slope": "normal", "scenery": "normal"}, weights={"distance": 0.2, "slope": 0.1, "scenery": 0.7})
    assert _t011_post_process(intent, "从教五到图书馆", None).weights is None
