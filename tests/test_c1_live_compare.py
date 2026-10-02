"""C1 live comparison scoring contract; no network calls."""

from experiments.c1_live_compare import compare_cases, project_run, score_target


def test_project_run_extracts_route_slots_and_weather_requirement():
    run = {
        "status": "completed",
        "result": {
            "response_kind": "route",
            "route_state": {
                "start": {"name": "玉兰2门"},
                "end": {"name": "樱顶"},
                "via": {"name": "卓尔体育馆"},
                "travel_mode": "walk",
                "hard_constraints": {"avoid_steps": True},
                "strategy": {"name": "flat"},
                "data_version": "data-123",
                "road_condition_version": "roads-123",
            },
            "requirement_results": {"route": "satisfied", "weather": "satisfied"},
        },
    }
    assert project_run(run) == {
        "status": "completed", "kind": "route", "start": "玉兰2门",
        "end": "樱顶", "via": ["卓尔体育馆"], "mode": "walk",
        "strategy": "flat", "hard_constraints": {"avoid_steps": True},
        "requirements": {"route": "satisfied", "weather": "satisfied"},
        "data_version": "data-123", "road_condition_version": "roads-123",
        "error": None,
    }


def test_score_target_requires_all_declared_slots_and_never_counts_unknown_as_pass():
    expected = {"kind": "route", "start": "玉兰2门", "end": "樱顶",
                "via": ["卓尔体育馆"], "hard_constraints": {"avoid_steps": True}}
    good = {"status": "completed", **expected}
    assert score_target(expected, good)["passed"] is True
    missing_via = {**good, "via": []}
    assert score_target(expected, missing_via)["passed"] is False
    assert "via" in score_target(expected, missing_via)["mismatches"]
    unknown = {**good, "hard_constraints": {}}
    assert score_target(expected, unknown)["passed"] is False


def test_score_target_matches_unambiguous_poi_alias_to_canonical_name():
    expected = {"kind": "route", "start": "玉兰2门", "end": "武汉大学老斋舍"}
    observed = {"status": "completed", "kind": "route", "start": "玉兰2门",
                "end": "樱顶"}
    assert score_target(expected, observed)["passed"] is True


def test_score_target_detects_constraint_that_user_removed():
    expected = {"kind": "route", "forbidden_constraints": ["avoid_steps"]}
    observed = {"status": "completed", "kind": "route",
                "hard_constraints": {"avoid_steps": True}}
    assert score_target(expected, observed) == {
        "passed": False, "mismatches": ["hard_constraints.avoid_steps"],
        "critical": True,
    }


def test_compare_cases_reports_paired_difference_and_critical_corruption():
    rows = [
        {"id": "a", "full": {"passed": True, "critical": False},
         "stateless": {"passed": False, "critical": False}},
        {"id": "b", "full": {"passed": False, "critical": True},
         "stateless": {"passed": False, "critical": False}},
        {"id": "c", "full": {"passed": True, "critical": False},
         "stateless": {"passed": True, "critical": False}},
    ]
    summary = compare_cases(rows)
    assert summary["n_cases"] == 3
    assert summary["full_success"] == 2
    assert summary["stateless_success"] == 1
    assert summary["full_critical_corruption"] == 1
    assert summary["paired_win"] == 1
    assert summary["paired_loss"] == 0
