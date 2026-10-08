import json
from pathlib import Path

from spatial.poi_taxonomy import resolve_activity_query, resolve_activity_sequence
from spatial.poi import _exact_master_poi_matches, load_pois


def _by_id():
    return {poi["id"]: poi for poi in load_pois()}


def test_explicit_ball_sports_map_to_their_own_facilities():
    assert resolve_activity_query("我想打篮球") == {
        "status": "resolved", "activities": ["basketball"],
        "subcategories": ["field", "sports_field", "court"],
    }
    assert resolve_activity_query("去打羽毛球") == {
        "status": "resolved", "activities": ["badminton"],
        "subcategories": ["field", "sports_field", "court"],
    }
    assert resolve_activity_query("想打乒乓球") == {
        "status": "resolved", "activities": ["table_tennis"],
        "subcategories": ["court", "sports_centre"],
    }
    assert resolve_activity_query("去踢足球") == {
        "status": "resolved", "activities": ["football"],
        "subcategories": ["field", "sports_field"],
    }


def test_unspecified_ball_sport_requires_the_compact_choices():
    assert resolve_activity_query("我想去打球") == {
        "status": "needs_clarification",
        "question": "想打什么球？",
        "options": ["篮球", "羽毛球", "乒乓球", "足球", "其他球类"],
    }


def test_general_exercise_requires_an_activity_but_running_is_specific():
    assert resolve_activity_query("我想去运动")["status"] == "needs_clarification"
    assert resolve_activity_query("找个地方跑步") == {
        "status": "resolved", "activities": ["running"],
        "subcategories": ["field", "sports_field"],
    }


def test_composite_activity_requests_keep_order_and_unresolved_details():
    sequence = resolve_activity_sequence("我想先打球再吃饭")

    assert [item["query"] for item in sequence] == ["我想先打球", "吃饭"]
    assert sequence[0]["intent"]["status"] == "needs_clarification"
    assert sequence[1]["intent"]["activities"] == ["meal"]

    explicit = resolve_activity_sequence("先打篮球，然后去吃饭")
    assert [item["intent"]["activities"] for item in explicit] == [
        ["basketball"], ["meal"],
    ]
    assert resolve_activity_query("我想去健身") == {
        "status": "resolved", "activities": ["fitness"],
        "subcategories": ["gym", "sports_centre", "sports_other"],
    }


def test_curated_pois_carry_audited_activity_provenance():
    pois = _by_id()
    # Keep the established coarse category field stable; use the new activity
    # field for the specific supported sport.
    assert {pois[poi_id]["subcategory"] for poi_id in ("poi_083", "poi_206", "poi_262")} == {"field"}
    assert all("basketball" in pois[poi_id]["activities"]
               for poi_id in ("poi_083", "poi_206", "poi_262"))
    assert all(poi.get("activity_review_status") in {
        "source_supported", "category_supported", "reviewed_no_supported_activity",
    } for poi in pois.values())
    assert len(pois) == len(json.loads(Path("data/pois.json").read_text(encoding="utf-8"))["pois"])


def test_common_sports_nicknames_resolve_to_the_correct_grounds_only():
    pois = load_pois()
    assert [poi["id"] for poi in _exact_master_poi_matches("桂操", pois)] == ["poi_085"]
    assert [poi["id"] for poi in _exact_master_poi_matches("梅操", pois)] == ["poi_082"]
    assert [poi["id"] for poi in _exact_master_poi_matches("信操", pois)] == ["poi_260"]


def test_osm_cafe_is_not_an_alias_of_the_museum_and_is_searchable_as_coffee():
    pois = _by_id()
    museum = pois["poi_004"]
    cafe = pois["poi_452"]

    assert "珞珈咖啡" not in museum["aliases"]
    assert [poi["id"] for poi in _exact_master_poi_matches(
        "珞珈咖啡", list(pois.values()))] == ["poi_452"]
    assert cafe["subcategory"] == "coffee"
    assert "coffee" in cafe["activities"]
    assert any(source.get("id") == "node/13246222238"
               for source in cafe["source_refs"])


def test_named_table_tennis_and_badminton_osm_candidates_have_audited_decisions():
    pois = _by_id()
    by_source = {
        source.get("id"): poi
        for poi in pois.values()
        for source in poi.get("source_refs", [])
        if source.get("source") == "OpenStreetMap"
    }
    assert by_source["node/2177869899"]["activities"] == ["table_tennis"]
    assert by_source["node/10742578168"]["activities"] == ["badminton"]
    review = json.loads(Path("data/campus_review_decisions.json").read_text(encoding="utf-8"))
    decisions = {row.get("source_id"): row for row in review["poi_decisions"]}
    assert decisions["node/2177869899"]["decision"] != "pending_review"
    assert decisions["node/10742578168"]["decision"] != "pending_review"
