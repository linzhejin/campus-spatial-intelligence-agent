"""The route comparison uses the same destination anchor policy as the API."""

from scripts.validate import compare_walk_reference as comparison
from spatial.coord_transform import gcj02_to_wgs84


def test_reference_comparison_prefers_exact_provider_entrance(monkeypatch):
    poi = {"name": "图书馆", "coordinates": {"lng": 114.36, "lat": 30.54}}
    monkeypatch.setattr(comparison, "lookup", lambda name, known, client: {
        "navigation_coordinates": {"lng": 114.3601, "lat": 30.5401}})

    wgs, gcj, source = comparison._navigation_anchor(poi, object())

    assert source == "amap_entr_location"
    assert gcj == {"lng": 114.3601, "lat": 30.5401}
    assert wgs == gcj02_to_wgs84(114.3601, 30.5401)


def test_reference_comparison_falls_back_to_poi_center(monkeypatch):
    poi = {"name": "图书馆", "coordinates": {"lng": 114.36, "lat": 30.54}}
    monkeypatch.setattr(comparison, "lookup", lambda name, known, client: None)

    wgs, gcj, source = comparison._navigation_anchor(poi, object())

    assert source == "poi_center"
    assert gcj == poi["coordinates"]
    assert wgs == gcj02_to_wgs84(114.36, 30.54)
