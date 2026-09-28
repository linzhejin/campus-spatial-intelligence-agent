import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from spatial.course_data import normalize_course_road, normalize_course_spot


@pytest.mark.parametrize(
    ("properties", "expected"),
    [
        (
            {"道路名": "无名路", "道路类": None, "路面材": "水泥路面", "车道数": 0},
            {"name": None, "road_class": None, "surface": "concrete", "lane_count": 0},
        ),
        (
            {"道路名": "  玉兰路 ", "道路类": "人行道", "路面材": "沥青", "车道数": 1},
            {"name": "玉兰路", "road_class": "pedestrian", "surface": "asphalt", "lane_count": 1},
        ),
        (
            {"道路名": None, "道路类": "电动车道", "路面材": None, "车道数": None},
            {"name": None, "road_class": "electric_vehicle", "surface": None, "lane_count": None},
        ),
    ],
)
def test_course_attributes_normalize_without_inventing_values(properties, expected):
    normalized = normalize_course_road(properties)
    for key, value in expected.items():
        assert normalized[key] == value


def test_course_unknowns_do_not_become_false_facts():
    road = normalize_course_road({
        "道路名": "无名路", "道路类": None,
        "路面材": "水泥路面", "车道数": 0,
    })
    assert road["name"] is None
    assert road["road_class"] is None
    assert road["surface"] == "concrete"
    assert road["elevation_available"] is False


def test_course_static_access_category_does_not_claim_current_closure():
    road = normalize_course_road({"道路名": "施工路", "道路类": "施工"})
    assert road["road_class"] == "construction"
    assert road["blocked_modes"] == []
    assert road["current_status"] == "unknown"


def test_unknown_or_unrecognized_values_are_retained_as_raw_evidence():
    road = normalize_course_road({"道路名": "某路", "道路类": "未分类甲", "路面材": "新材质"})
    assert road["road_class"] is None
    assert road["road_class_raw"] == "未分类甲"
    assert road["surface"] is None
    assert road["surface_raw"] == "新材质"


def test_geographic_code_uses_the_source_field_name_and_retains_raw_fields():
    source = {"道路名": "玉兰路", "道路类": "人行道", "地理编": 620,
              "Shape_Leng": 33.5}
    road = normalize_course_road(source)
    assert road["geographic_code"] == 620
    assert road["raw_source_properties"] == source


def test_course_spot_normalization_retains_every_raw_attribute():
    source = {"name": "校门牌坊", "spot_id": 7, "类别": "入口"}
    spot = normalize_course_spot(source, "course:spot:0007", 7)
    assert spot["name"] == "校门牌坊"
    assert spot["source_rows"] == [7]
    assert spot["source_ids"] == ["course:spot:0007"]
    assert spot["source_records"] == [{
        "source_row": 7,
        "source_id": "course:spot:0007",
        "raw_source_properties": source,
    }]
