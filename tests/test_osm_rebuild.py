"""Dense graph building keeps source identity and isolates old annotations."""
import json

import networkx as nx

from spatial import network


def test_unscoped_old_annotation_and_override_are_not_applied(tmp_path):
    g = nx.MultiDiGraph(annotation_policy="source_scoped_only", source_sha256="current")
    g.add_edge(1, 2, key=0, highway="footway", length=10.)
    annotations = tmp_path / "annotations.json"
    annotations.write_text(json.dumps({"edges": [{"edge_id": [1, 2, 0], "slope_level": 5, "scenery_level": 5}]}))
    overrides = tmp_path / "overrides.json"
    overrides.write_text(json.dumps({"edges": [{"u": 1, "v": 2, "k": 0, "blocked_modes": ["all"]}]}))
    assert network._merge_annotations(g, str(annotations)) == 0
    assert network._merge_overrides(g, str(overrides)) == 0
    assert "slope_level" not in g[1][2][0]
    assert "blocked_modes" not in g[1][2][0]


def test_dense_builder_keeps_shape_nodes_and_original_direction():
    from scripts.network.rebuild_network import build_from_candidates
    roads = {"features": [{"properties": {"osm_id": 42, "osm_version": 3,
              "osm_timestamp": "2026-09-24", "node_refs": [1, 2, 3],
              "tags": {"highway": "service", "oneway": "yes", "covered": "yes"}},
              "geometry": {"type": "LineString", "coordinates": [[114.36, 30.535], [114.3601, 30.5352], [114.3603, 30.535]]}}]}
    nodes = {"features": [{"properties": {"osm_id": 2, "tags": {"barrier": "bollard"}},
                            "geometry": {"type": "Point", "coordinates": [114.3601, 30.5352]}}]}
    g, stats = build_from_candidates(roads, nodes, source_sha256="sha")
    assert set(g) == {1, 2, 3}
    assert g[1][2][42]["covered"] == "yes"
    assert g[1][2][42]["osm_way_forward"] is True
    assert g[2][1][42]["osm_way_forward"] is False
    assert g.nodes[2]["barrier"] == "bollard"
    assert "drive" in g[1][2][42]["allowed_modes"]
    assert "drive" not in g[2][1][42]["allowed_modes"]
    assert "slope_level" not in g[1][2][42]
    assert "scenery_level" not in g[1][2][42]
    assert g.graph["annotation_policy"] == "source_scoped_only"


def test_builder_does_not_treat_pedestrian_area_outline_as_road():
    from scripts.network.rebuild_network import build_from_candidates
    roads = {"features": [{"properties": {"osm_id": 42, "node_refs": [1, 2, 1],
              "tags": {"highway": "pedestrian", "area": "yes"}},
              "geometry": {"type": "LineString", "coordinates": [[114.36, 30.535], [114.36, 30.536], [114.36, 30.535]]}}]}
    g, stats = build_from_candidates(roads, {"features": []}, source_sha256="sha")
    assert g.number_of_edges() == 0
    assert stats["excluded_highway_areas"] == 1


def test_builder_marks_emergency_entrance_as_not_publicly_walkable():
    from scripts.network.rebuild_network import build_from_candidates
    roads = {"features": [{"properties": {"osm_id": 42, "node_refs": [1, 2],
              "tags": {"highway": "footway"}},
              "geometry": {"type": "LineString", "coordinates": [
                  [114.36, 30.535], [114.3601, 30.535]]}}]}
    nodes = {"features": [{"properties": {"osm_id": 2,
              "tags": {"entrance": "emergency"}},
              "geometry": {"type": "Point", "coordinates": [114.3601, 30.535]}}]}

    graph, _ = build_from_candidates(roads, nodes, source_sha256="sha")

    assert graph.nodes[2]["entrance"] == "emergency"
    assert "walk" in graph.nodes[2]["blocked_modes"]


def test_building_footprint_candidate_requires_complete_ring_and_retains_provenance():
    from shapely.geometry import box
    from scripts.validate.extract_open_osm_routes import building_candidate

    selection = box(114.35, 30.52, 114.38, 30.55)
    ring = [(114.36, 30.53), (114.361, 30.53),
            (114.361, 30.531), (114.36, 30.531), (114.36, 30.53)]
    feature = building_candidate(11, {"building": "yes", "name": "Test"}, ring,
                                 selection, version=2, timestamp="2026-09-24")
    assert feature["id"] == "way/11"
    assert feature["properties"]["source_ref"] == "way/11"
    assert feature["properties"]["license"] == "ODbL-1.0"
    assert feature["properties"]["verification_status"] == "source_only"
    assert building_candidate(12, {"building": "yes"}, ring[:-1], selection) is None
    assert building_candidate(13, {"building": "yes"}, ring, box(0, 0, 1, 1)) is None
