"""Cross campus conflicts are quarantined only after an explicit review."""

import pytest
from shapely.geometry import Polygon, mapping

from scripts.validate.quarantine_foreign_campus_pois import review_exclusions
from spatial.coord_transform import wgs84_to_gcj02


def _feature(ref, polygon):
    return {"type": "Feature", "geometry": mapping(polygon),
            "properties": {"osm_id": ref, "name": ref}}


def test_reviewed_foreign_poi_is_archived_and_unreviewed_poi_remains():
    lng, lat = wgs84_to_gcj02(114.36, 30.535)
    pois = [{"id": "wrong", "name": "外校楼", "coordinates": {"lng": lng, "lat": lat}},
            {"id": "keep", "name": "待核实", "coordinates": {"lng": lng, "lat": lat}}]
    features = [
        _feature("relation/foreign", Polygon([(114.359, 30.534), (114.361, 30.534),
                                               (114.361, 30.536), (114.359, 30.536)])),
        _feature("relation/whu", Polygon([(114.37, 30.54), (114.38, 30.54),
                                           (114.38, 30.55), (114.37, 30.55)])),
    ]
    decisions = [{"poi_id": "wrong", "name": "外校楼",
                  "foreign_boundary_ref": "relation/foreign"}]

    kept, archived = review_exclusions(pois, decisions, features, {"relation/whu"})

    assert [p["id"] for p in kept] == ["keep"]
    assert archived[0]["poi"]["id"] == "wrong"


def test_review_rejects_poi_outside_claimed_foreign_boundary():
    lng, lat = wgs84_to_gcj02(114.36, 30.535)
    pois = [{"id": "wrong", "name": "外校楼", "coordinates": {"lng": lng, "lat": lat}}]
    features = [_feature("relation/foreign", Polygon([
        (114.37, 30.54), (114.38, 30.54), (114.38, 30.55), (114.37, 30.55)]))]
    decisions = [{"poi_id": "wrong", "name": "外校楼",
                  "foreign_boundary_ref": "relation/foreign"}]

    with pytest.raises(ValueError, match="outside reviewed boundary"):
        review_exclusions(pois, decisions, features, set())
