import agents.tools as tools


def _route_result(distance=1200, duration=15):
    return {
        "start_name": "星湖园",
        "end_name": "宿舍",
        "recommended_length_m": distance,
        "distance_m": distance,
        "duration_min": duration,
        "legs": [{"duration_min": duration}],
        "route_state": {"route_kind": "via"},
    }


def test_plan_itinerary_counts_travel_and_stop_time_and_returns_route(monkeypatch):
    calls = []

    def plan_via(args, ctx):
        calls.append((args, ctx))
        return _route_result(), {"route": _route_result(), "route_kind": "via"}

    monkeypatch.setattr(tools, "_tool_plan_via_route", plan_via)
    result, artifact = tools.execute_tool("plan_itinerary", {
        "start": {"name": "星湖园", "type": "poi"},
        "stops": [{"name": "星湖园食堂", "type": "poi"}, {"name": "图书馆", "type": "poi"}],
        "end": {"name": "宿舍", "type": "poi"},
        "time_budget_min": 30,
        "stop_duration_min": 5,
    }, {"query": "半小时内逛两个地方"})

    assert len(calls) == 1
    assert calls[0][0]["via_points"] == [
        {"name": "星湖园食堂", "type": "poi"}, {"name": "图书馆", "type": "poi"},
    ]
    assert result["status"] == "feasible"
    assert result["travel_duration_min"] == 15
    assert result["stop_duration_min"] == 10
    assert result["total_duration_min"] == 25
    assert artifact["route"]["itinerary"]["time_budget_min"] == 30


def test_plan_itinerary_does_not_publish_route_when_budget_is_exceeded(monkeypatch):
    monkeypatch.setattr(tools, "_tool_plan_via_route",
                        lambda *_args: (_route_result(duration=25), {"route": _route_result(duration=25)}))

    result, artifact = tools.execute_tool("plan_itinerary", {
        "start": {"name": "星湖园", "type": "poi"},
        "stops": [{"name": "食堂", "type": "poi"}, {"name": "图书馆", "type": "poi"}],
        "time_budget_min": 30,
        "stop_duration_min": 5,
    })

    assert result["status"] == "over_budget"
    assert result["total_duration_min"] == 35
    assert result["over_by_min"] == 5
    assert artifact is None


def test_plan_itinerary_requires_budget_and_at_least_one_stop():
    result, artifact = tools.execute_tool("plan_itinerary", {
        "start": {"name": "星湖园", "type": "poi"},
    })

    assert result["error"] == "invalid_itinerary"
    assert artifact is None


def test_plan_itinerary_schema_and_executor_are_registered():
    schema = next((item for item in tools.TOOL_SCHEMAS
                   if item["function"]["name"] == "plan_itinerary"), None)

    assert schema is not None
    assert schema["function"]["parameters"]["required"] == ["start", "stops", "time_budget_min"]
    assert "plan_itinerary" in tools._EXECUTORS
