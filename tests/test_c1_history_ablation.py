"""History-only ablation keeps the transcript identical across both arms."""

from experiments.c1_history_ablation import contexts_for_target


def test_contexts_differ_only_by_validated_route_state():
    history = [{"role": "user", "content": "从玉兰2门经卓尔体育馆到樱顶"},
               {"role": "assistant", "content": "路线已规划。"}]
    route_state = {"route_kind": "via", "via": {"name": "卓尔体育馆"}}
    structured, history_only = contexts_for_target(history, route_state)
    assert structured["history"] == history_only["history"] == history
    assert structured["previous_route_state"] == route_state
    assert "previous_route_state" not in history_only
    route_state["via"]["name"] = "改变了"
    assert structured["previous_route_state"]["via"]["name"] == "卓尔体育馆"
