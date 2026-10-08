from unittest.mock import patch

from agents import planner, tools
from spatial.poi import _flatten_poi


def test_activity_filter_is_applied_to_the_full_category_result():
    records = [
        {"id": "basketball", "name": "篮球场", "type": "sports", "subcategory": "field",
         "activities": ["basketball"]},
        {"id": "badminton", "name": "羽毛球场", "type": "sports", "subcategory": "field",
         "activities": ["badminton"]},
        {"id": "running", "name": "操场", "type": "sports", "subcategory": "field",
         "activities": ["running"]},
    ]
    calls = []

    def category_search(subcategory=None, limit=None, **_kwargs):
        calls.append((subcategory, limit))
        return records if subcategory == "field" else []

    with patch.object(tools, "search_by_category", side_effect=category_search):
        results = tools._search_poi_categories(
            ["field", "court"], "sports", "", None, True, activities=["basketball"],
        )

    assert calls == [("field", 2000), ("court", 2000)]
    assert [poi["id"] for poi in results] == ["basketball"]


def test_live_master_category_search_returns_basketball_and_canteens():
    basketball = tools._search_poi_categories(
        ["field", "sports_field", "court"], "sports", "", None, True,
        activities=["basketball"],
    )
    canteens = tools._search_poi_categories(
        ["canteen"], "dining", "", None, True, activities=["canteen_meal"],
    )

    assert {poi["id"] for poi in basketball} >= {"poi_083", "poi_206", "poi_262"}
    assert canteens
    assert all("canteen_meal" in poi["activities"] for poi in canteens)
    assert all(poi["subcategory"] == "canteen" for poi in canteens)


def test_generic_meal_matches_all_meal_venues_but_canteen_stays_specific():
    meal = tools._search_poi_categories(
        ["canteen", "restaurant", "fastfood"], "dining", "", None, True,
        activities=["meal"],
    )
    canteens = tools._search_poi_categories(
        ["canteen", "restaurant", "fastfood"], "dining", "", None, True,
        activities=["canteen_meal"],
    )

    assert meal
    assert {poi["subcategory"] for poi in meal} == {"canteen", "restaurant", "fastfood"}
    assert canteens
    assert all(poi["subcategory"] == "canteen" for poi in canteens)


def test_flattened_poi_preserves_audited_activity_tags():
    source = {
        "id": "poi_262", "name": "信息学部篮球场", "type": "sports",
        "subcategory": "field", "coordinates": {"lng": 114.4, "lat": 30.5},
        "activities": ["basketball"], "activity_basis": "name_supported: basketball court",
        "activity_review_status": "source_supported", "activity_taxonomy_version": 1,
    }

    flattened = _flatten_poi(source)

    assert flattened["activities"] == ["basketball"]
    assert flattened["activity_basis"] == "name_supported: basketball court"
    assert flattened["activity_review_status"] == "source_supported"
    assert flattened["activity_taxonomy_version"] == 1


def test_ambiguous_ball_request_asks_before_calling_the_model():
    with patch.object(planner, "_make_client", side_effect=AssertionError("LLM should not choose a sport")):
        result = planner.run_agent("我想去打球")

    assert result["response_kind"] == "clarify"
    assert result["clarify"]["options"] == ["篮球", "羽毛球", "乒乓球", "足球", "其他球类"]
    assert result["place_request"]["pending_slot"] == "activity"


def test_sport_answer_that_matches_a_poi_does_not_replace_the_saved_origin():
    original_query = "从信息学部图书馆出发，想去打球"
    pending = planner.run_agent(original_query)
    candidate = {
        "poi_id": "poi_450", "name": "乒乓球馆", "subcategory": "court",
        "activities": ["table_tennis"], "distance_m": 120,
    }

    with patch.object(planner.agent_tools, "execute_tool", return_value=(
        {"candidates": [candidate], "count": 1,
         "start_name": "武汉大学信息学部图书馆"},
        {"candidates": [candidate]},
    )) as execute:
        result = planner.run_agent("乒乓球", context={
            "active_task_request": original_query,
            "place_request": pending["place_request"],
        })

    assert result["response_kind"] == "candidates"
    tool_name, args = execute.call_args.args[:2]
    assert tool_name == "search_poi_candidates"
    assert args["start"] == {
        "type": "poi", "name": "武汉大学信息学部图书馆",
    }


def test_basketball_catalog_uses_complete_activity_filtered_master_without_origin():
    basketball = [
        {"poi_id": "poi_083", "name": "文理学部篮球场", "activities": ["basketball"]},
        {"poi_id": "poi_206", "name": "工学部篮球场", "activities": ["basketball"]},
        {"poi_id": "poi_262", "name": "信息学部篮球场", "activities": ["basketball"]},
    ]
    with patch.object(planner.agent_tools, "execute_tool", return_value=(
        {"candidates": basketball, "count": 3}, {"candidates": basketball},
    )) as execute:
        result = planner.run_agent("武大有哪些篮球场")

    assert result["response_kind"] == "candidates"
    assert [row["poi_id"] for row in result["candidates"]] == ["poi_083", "poi_206", "poi_262"]
    name, args = execute.call_args.args[:2]
    assert name == "search_poi_candidates"
    assert args["activities"] == ["basketball"]
    assert "start" not in args


def test_composite_sport_then_meal_keeps_the_second_request_through_route_selection():
    initial = planner.run_agent("我在工学部，想先打球再吃饭")
    assert initial["response_kind"] == "clarify"
    assert initial["place_request"]["follow_up_requests"] == [{
        "query": "吃饭", "activities": ["meal"],
        "subcategories": ["canteen", "restaurant", "fastfood"],
    }]

    candidate = {
        "poi_id": "poi_206", "name": "工学部篮球场", "subcategory": "field",
        "activities": ["basketball"], "distance_m": 120,
    }
    with patch.object(planner.agent_tools, "execute_tool", return_value=(
        {"candidates": [candidate], "count": 1, "start_name": "工学部"},
        {"candidates": [candidate]},
    )):
        selected_sport = planner.run_agent(
            "篮球", context={"place_request": initial["place_request"]},
        )
    assert selected_sport["response_kind"] == "candidates"
    assert selected_sport["place_request"]["follow_up_requests"] == initial["place_request"]["follow_up_requests"]

    route = {"recommended": [[114.36, 30.54]], "start_name": "工学部",
             "end_name": "工学部篮球场", "mode": "walk"}
    with patch.object(planner.agent_tools, "load_pois", return_value=[{
        "id": "poi_206", "name": "工学部篮球场",
    }]), patch.object(planner.agent_tools, "execute_tool", return_value=(
        route, {"route": route},
    )) as execute:
        completed = planner.run_agent("去这个地点", context={
            "place_request": selected_sport["place_request"],
            "selected_poi_id": "poi_206",
        })

    assert completed["response_kind"] == "route"
    assert completed["suggestions"] == [{
        "label": "下一步：找餐饮", "query": "从工学部篮球场出发找吃饭",
    }]
    route_args = execute.call_args.args[1]
    assert route_args["end"]["poi_id"] == "poi_206"
