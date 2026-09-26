"""AMap candidates must be assigned by real campus geometry, not a wide circle."""

import json
from pathlib import Path

from scripts.fetch import campus_scope
from scripts.fetch.fetch_pois_amap import CAMPUSES, classify, keep_campus_candidate

ROOT = Path(__file__).resolve().parents[1]


def test_fetch_anchors_are_inside_their_named_campus():
    polygons = campus_scope.load_whu_polygons()
    for campus in CAMPUSES:
        assert campus_scope.campus_for_gcj(*campus["anchor"], polygons) == campus["name"]


def test_other_university_pois_fail_campus_scope():
    polygons = campus_scope.load_whu_polygons()
    pois = {poi["id"]: poi for poi in json.loads(
        (ROOT / "data/pois_excluded_legacy.json").read_text(encoding="utf-8"))["excluded"]
        for poi in [poi["poi"]]}
    for poi_id in ("poi_232", "poi_233"):
        coord = pois[poi_id]["coordinates"]
        assert campus_scope.campus_for_gcj(coord["lng"], coord["lat"], polygons) is None


def test_named_second_gate_survives_candidate_classification():
    assert keep_campus_candidate("玉兰2门", "")
    assert classify("玉兰2门", "190300") == "gate"
