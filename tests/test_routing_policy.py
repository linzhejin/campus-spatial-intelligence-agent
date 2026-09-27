import pytest

from agents.routing_policy import STRATEGY_WEIGHTS, select_route_strategy


@pytest.mark.parametrize(
    "query",
    ["从梅园到教五", "去食堂", "赶课，快点到", "到樱顶"],
)
def test_commute_is_exact_shortest_even_for_scenic_destination(query):
    decision = select_route_strategy(
        query=query,
        end_poi={"name": "樱顶", "type": "scenery"},
    )

    assert decision.as_dict() == {
        "name": "shortest",
        "source": "commute_default" if query != "赶课，快点到" else "explicit_nl",
        "task_class": "commute",
        "weights": {"distance": 1.0, "slope": 0.0, "scenery": 0.0},
        "detour_cap": 1.0,
    }


def test_leisure_uses_balanced_default_without_profile():
    decision = select_route_strategy(query="带朋友逛武大，推荐一条路线")

    assert decision.name == "recommended"
    assert decision.source == "tour_default"
    assert decision.weights == {"distance": 0.5, "slope": 0.2, "scenery": 0.3}
    assert decision.detour_cap == 1.25


def test_scenic_poi_is_only_supporting_evidence_for_leisure_language():
    decision = select_route_strategy(
        query="去看看樱顶",
        end_poi={"name": "樱顶", "type": "scenery"},
    )

    assert decision.name == "recommended"
    assert decision.task_class == "leisure"


def test_button_beats_profile_and_query_inference():
    decision = select_route_strategy(
        query="从梅园到教五",
        explicit_strategy="scenery",
        strategy_source="button",
        profile={
            "weights": {"distance": 0.5, "slope": 0.4, "scenery": 0.1},
            "accepted_count": 8,
        },
    )

    assert decision.name == "scenery"
    assert decision.source == "button"
    assert decision.weights == {"distance": 0.5, "slope": 0.1, "scenery": 0.4}


@pytest.mark.parametrize(
    "strategy,expected",
    [
        ("shortest", {"distance": 1.0, "slope": 0.0, "scenery": 0.0}),
        ("scenery", {"distance": 0.5, "slope": 0.1, "scenery": 0.4}),
        ("flat", {"distance": 0.5, "slope": 0.4, "scenery": 0.1}),
    ],
)
def test_fixed_button_strategies_have_exact_weights(strategy, expected):
    decision = select_route_strategy(
        explicit_strategy=strategy,
        strategy_source="button",
    )

    assert decision.weights == expected
    assert decision.detour_cap == (1.0 if strategy == "shortest" else 1.25)


def test_explicit_detour_language_raises_but_caps_ratio():
    decision = select_route_strategy(query="远一点没关系，尽量走风景好的路")

    assert decision.name == "scenery"
    assert decision.source == "explicit_nl"
    assert decision.detour_cap == 1.5


def test_profile_is_bounded_before_use():
    decision = select_route_strategy(
        query="随便逛逛，推荐一条路线",
        profile={
            "weights": {"distance": 0.1, "slope": 0.1, "scenery": 0.8},
            "accepted_count": 9,
        },
    )

    assert decision.source == "profile"
    assert decision.weights["distance"] >= 0.40
    assert decision.weights["slope"] <= 0.50
    assert decision.weights["scenery"] <= 0.50
    assert sum(decision.weights.values()) == pytest.approx(1.0)


def test_unreliable_profile_falls_back_to_balanced_recommendation():
    decision = select_route_strategy(
        query="随便逛逛，推荐一条路线",
        profile={
            "weights": {"distance": 0.1, "slope": 0.1, "scenery": 0.8},
            "accepted_count": 2,
        },
    )

    assert decision.source == "tour_default"
    assert decision.weights == STRATEGY_WEIGHTS["recommended"]


def test_custom_numeric_weights_remain_custom_and_bounded():
    decision = select_route_strategy(
        query="距离坡度风景按 6:3:1",
        explicit_strategy="custom",
        custom_weights={"distance": 0.6, "slope": 0.3, "scenery": 0.1},
    )

    assert decision.name == "custom"
    assert decision.weights == pytest.approx(
        {"distance": 0.6, "slope": 0.3, "scenery": 0.1}
    )
