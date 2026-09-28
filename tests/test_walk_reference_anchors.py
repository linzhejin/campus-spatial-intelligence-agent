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


def test_reference_route_uses_identical_navigation_anchors(monkeypatch):
    captured = {}
    monkeypatch.setattr(comparison, "_reference_walk", lambda client, key, start, end: (
        captured.update(start=start, end=end) or (100.0, [(1.0, 1.0), (2.0, 2.0)])
    ))
    start = {"lng": 114.3601, "lat": 30.5401}
    end = {"lng": 114.3702, "lat": 30.5502}

    comparison._reference_walk_at_navigation_anchors(object(), "key", start, end)

    assert captured == {"start": (114.3601, 30.5401), "end": (114.3702, 30.5502)}


def test_reference_comparison_uses_shortest_distance_route_policy():
    assert comparison.COMPARISON_WEIGHTS == {
        "distance": 1.0, "slope": 0.0, "scenery": 0.0,
    }


def test_course_release_graph_uses_current_building_geometry_for_hazards():
    assert comparison._uses_candidate_geometry_assessment({
        "course_release_fingerprint": "release-123",
    }) is True


def test_legacy_graph_without_release_metadata_keeps_historical_issue_ids():
    assert comparison._uses_candidate_geometry_assessment({}) is False


def test_hazard_check_uses_edge_shape_instead_of_straight_node_chord():
    import networkx as nx

    graph = nx.MultiDiGraph()
    graph.add_node(1, x=114.0, y=30.0)
    graph.add_node(2, x=114.002, y=30.0)
    edge = {"geometry": "LINESTRING (114 30, 114.001 30.001, 114.002 30)"}

    line = comparison._candidate_edge_line(graph, 1, 2, edge)

    assert list(line.coords) == [
        (114.0, 30.0), (114.001, 30.001), (114.002, 30.0),
    ]


def test_building_crossing_report_includes_building_and_overlap_evidence(tmp_path):
    import json
    import networkx as nx

    graph = nx.MultiDiGraph()
    graph.add_node(1, x=114.0, y=30.0)
    graph.add_node(2, x=114.002, y=30.0)
    graph.add_edge(1, 2, key=0, highway="footway",
                   geometry="LINESTRING (114 30, 114.002 30)")
    footprints = tmp_path / "buildings.geojson"
    footprints.write_text(json.dumps({
        "metadata": {"source": "OpenStreetMap API 0.6 /map",
                     "license": "ODbL-1.0",
                     "created_at_utc": "2026-09-28T12:18:31Z"},
        "features": [{
        "id": "way/11",
        "properties": {"osm_id": "way/11", "name": "Test building"},
        "geometry": {"type": "Polygon", "coordinates": [[
            [114.0008, 29.9999], [114.0012, 29.9999],
            [114.0012, 30.0001], [114.0008, 30.0001],
            [114.0008, 29.9999],
        ]]},
    }]}), encoding="utf-8")

    result = comparison._candidate_hazards(graph, [(1, 2, 0)], footprints)

    assert result["status"] == "checked"
    assert result["building_footprint_source"]["source"] == "OpenStreetMap API 0.6 /map"
    assert len(result["building_footprint_source"]["sha256"]) == 64
    assert result["suspected_crossing_edge_ids"][0]["building_name"] == "Test building"
    assert result["suspected_crossing_edge_ids"][0]["intersection_length_m"] > 1.0
