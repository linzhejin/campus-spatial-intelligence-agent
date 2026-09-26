"""A replacement network must retain access near existing destinations."""

import networkx as nx
from shapely.geometry import Polygon, mapping

from scripts.validate import audit_campus_master
from spatial.coord_transform import wgs84_to_gcj02


def test_candidate_snap_regressions_identify_lost_destination():
    lng, lat = wgs84_to_gcj02(114.36, 30.535)
    pois = [{"id": "poi_001", "name": "示例地点",
             "coordinates": {"lng": lng, "lat": lat}}]
    production = nx.MultiDiGraph()
    production.add_node(1, x=114.3601, y=30.535)
    candidate = nx.MultiDiGraph()
    candidate.add_node(2, x=114.362, y=30.535)

    regressions = audit_campus_master.candidate_snap_regressions(
        pois, production, candidate)

    assert len(regressions) == 1
    assert regressions[0]["poi_id"] == "poi_001"
    assert regressions[0]["production_snap_m"] < 50
    assert regressions[0]["candidate_snap_m"] > 50


def test_foreign_campus_conflicts_exempt_reviewed_shared_boundary_gate():
    lng, lat = wgs84_to_gcj02(114.36, 30.535)
    coord = {"lng": lng, "lat": lat}
    pois = [{"id": "wrong", "name": "误收外校楼", "type": "study",
             "coordinates": coord},
            {"id": "shared", "name": "共边界门", "type": "gate",
             "coordinates": coord,
             "campus_boundary_refs": ["relation/whu", "relation/foreign"]}]
    foreign = {"type": "Feature", "properties": {
        "osm_id": "relation/foreign", "name": "其他大学"},
        "geometry": mapping(Polygon([(114.359, 30.534), (114.361, 30.534),
                                     (114.361, 30.536), (114.359, 30.536)]))}
    whu = {"type": "Feature", "properties": {
        "osm_id": "relation/whu", "name": "武汉大学"},
        "geometry": mapping(Polygon([(114.37, 30.54), (114.38, 30.54),
                                     (114.38, 30.55), (114.37, 30.55)]))}

    conflicts = audit_campus_master.foreign_campus_conflicts(
        pois, [foreign, whu], {"relation/whu"})

    assert [row["poi_id"] for row in conflicts] == ["wrong"]
