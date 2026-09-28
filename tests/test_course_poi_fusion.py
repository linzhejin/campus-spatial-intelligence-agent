import copy
import json
from pathlib import Path

import pytest

from spatial.course_poi_fusion import fuse_course_pois


def _pois():
    return {
        "source": "existing",
        "count": 1,
        "provenance_defaults": {"source_refs": [], "verification_status": "legacy_unverified"},
        "pois": [{
            "id": "poi_140",
            "name": "珞珈门",
            "aliases": ["牌坊", "校门"],
            "coordinates": {"lng": 114.358238, "lat": 30.533340},
            "type": "gate",
            "category": "gate",
            "campus": "文理学部",
            "description": "main entrance",
            "season_tags": ["all"],
            "scenery_score": 1,
            "is_minor": False,
        }],
    }


def _feature(source_id="course:spot:0001", name="校门牌坊", coordinates=(114.35338845933923, 30.5361955203779)):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": list(coordinates)},
        "properties": {
            "name": name,
            "source_rows": [1],
            "source_ids": [source_id],
            "source_layer": "whu_spot",
            "source_crs": "EPSG:4547",
            "source_records": [{
                "source_row": 1,
                "source_id": source_id,
                "raw_source_properties": {"name": name, "spot_id": 9},
            }],
            "source_verification": "course_supplied_not_field_verified",
        },
    }


def _decisions(source_id="course:spot:0001"):
    return {
        "schema_version": 1,
        "source_id_prefix": "course",
        "coordinate_policy": "source_wgs84_to_gcj02_once",
        "decisions": [{
            "source_id": source_id,
            "source_ids": [source_id],
            "target_poi_id": "poi_140",
            "aliases": ["校门牌坊", "武汉大学校门牌坊"],
            "match_basis": "explicit reviewed correspondence",
            "coordinate_priority": "course",
        }],
    }


def test_course_point_replaces_gcj_coordinate_and_keeps_coordinate_lineage():
    before = _pois()
    merged, report = fuse_course_pois(before, [_feature()], _decisions())

    poi = merged["pois"][0]
    assert poi["coordinates"] == {"lng": pytest.approx(114.35890268918838),
                                  "lat": pytest.approx(30.53385381139409)}
    assert "校门牌坊" in poi["aliases"]
    assert "武汉大学校门牌坊" in poi["aliases"]
    assert poi["coordinate_history"][0]["coordinates"] == before["pois"][0]["coordinates"]
    assert poi["coordinate_system"] == "GCJ-02"
    assert poi["coordinate_verification_status"] == "course_source_only"
    assert poi["course_source_ids"] == ["course:spot:0001"]
    assert poi["course_source_records"][0]["raw_source_properties"]["spot_id"] == 9
    assert report["counts"] == {"updated": 1, "unchanged": 0, "unmatched": 0}


def test_course_poi_fusion_is_idempotent():
    first, _ = fuse_course_pois(_pois(), [_feature()], _decisions())
    second, report = fuse_course_pois(first, [_feature()], _decisions())

    assert second == first
    assert len(second["pois"][0]["coordinate_history"]) == 1
    assert report["counts"] == {"updated": 0, "unchanged": 1, "unmatched": 0}


def test_missing_or_stale_crosswalk_fails_closed():
    decisions = _decisions(source_id="stale:spot:0001")
    with pytest.raises(ValueError, match="crosswalk"):
        fuse_course_pois(_pois(), [_feature()], decisions)


def test_course_pois_do_not_create_duplicate_records_when_explicitly_matched():
    merged, report = fuse_course_pois(_pois(), [_feature()], _decisions())

    assert len(merged["pois"]) == 1
    assert report["poi_id_map"] == {"course:spot:0001": "poi_140"}


def test_reviewed_course_spots_are_in_the_live_poi_master_with_lineage():
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "data/pois.json").read_text(encoding="utf-8"))
    pois = {row["id"]: row for row in data["pois"]}

    assert len(pois) == 439
    expected = {
        "poi_247": {"遥感学院", "信息学部遥感学院"},
        "poi_140": {"武汉大学校门牌坊"},
        "poi_034": {"校史馆"},
        "poi_035": {"行政楼"},
        "poi_004": {"万林博物馆"},
    }
    for poi_id, aliases in expected.items():
        poi = pois[poi_id]
        assert aliases <= set(poi["aliases"])
        assert poi["coordinate_system"] == "GCJ-02"
        assert poi["coordinate_source"] == "WHU coursework"
        assert poi["coordinate_verification_status"] == "course_source_only"
        assert poi["coordinate_history"]
        assert poi["source_refs"]


def test_live_poi_lookup_resolves_course_names_and_aliases(monkeypatch):
    import spatial.poi as poi_module

    previous_cache = poi_module._POIS_CACHE
    previous_loaded = poi_module._LOADED
    monkeypatch.setattr(poi_module, "_record_search", lambda _poi_id: None)
    try:
        poi_module.reload_pois()
        queries = {
            "遥感学院": "poi_247",
            "信息学部遥感学院": "poi_247",
            "武汉大学校门牌坊": "poi_140",
            "校史馆": "poi_034",
            "行政楼": "poi_035",
            "万林博物馆": "poi_004",
        }
        for query, expected_id in queries.items():
            assert poi_module.get_poi(query)["id"] == expected_id
    finally:
        poi_module._POIS_CACHE = previous_cache
        poi_module._LOADED = previous_loaded
