import json
import sys
from pathlib import Path

import networkx as nx
import pytest
from shapely import wkt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from spatial.course_fusion import apply_course_junction_overrides, fuse_network


def _feature(source_id, coordinates, *, row=0, name=None, road_class="pedestrian"):
    return {
        "type": "Feature",
        "id": source_id,
        "geometry": {"type": "LineString", "coordinates": coordinates},
        "properties": {
            "source_id": source_id,
            "source_row": row,
            "name": name,
            "road_class": road_class,
            "road_class_raw": road_class,
            "surface": "concrete",
            "surface_raw": "水泥路面",
            "lane_count": 0,
            "geographic_code": 620,
            "raw_source_properties": {"Shape_Leng": 10.0},
        },
    }


def _matched_report(source_id, edge_ids):
    return {"matches": [{
        "source_id": source_id,
        "action": "matched",
        "candidates": [{"candidate_id": "track-1", "edge_ids": edge_ids}],
    }]}


def _new_report(features):
    return {"matches": [{
        "source_id": feature["properties"]["source_id"],
        "action": "new_candidate",
        "candidates": [],
    } for feature in features]}


def _base_graph():
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=114.360, y=30.530)
    graph.add_node(2, x=114.361, y=30.530)
    graph.add_edge(1, 2, key=0, osmid=10, highway="residential", length=95.0,
                   geometry="LINESTRING (114.36 30.53, 114.361 30.53)")
    graph.add_edge(2, 1, key=0, osmid=10, highway="residential", length=95.0,
                   geometry="LINESTRING (114.361 30.53, 114.36 30.53)")
    return graph


def test_matched_course_geometry_replaces_old_shape_without_dropping_osm_lineage():
    graph = _base_graph()
    feature = _feature("course:matched", [
        [114.360, 30.530005], [114.3605, 30.530003], [114.361, 30.530005],
    ], name="玉兰路")

    fused, migration = fuse_network(
        graph, [feature], _matched_report("course:matched", [["1", "2", 0], ["2", "1", 0]])
    )

    forward = wkt.loads(fused[1][2][0]["geometry"])
    reverse = wkt.loads(fused[2][1][0]["geometry"])
    assert len(forward.coords) == 3
    assert abs(forward.coords[0][0] - 114.360) < 1e-9
    assert abs(forward.coords[0][1] - 30.530) < 1e-9
    assert forward.coords[1][1] < 30.530005
    assert list(reverse.coords) == list(reversed(forward.coords))
    assert fused[1][2][0]["name"] == "玉兰路"
    assert fused[1][2][0]["osmid"] == 10
    assert fused[1][2][0]["course_road_class"] == "pedestrian"
    assert migration["course_feature_dispositions"][0]["geometry_replaced_edges"]
    disposition = next(row for row in migration["old_edge_dispositions"]
                       if row["edge_id"] == ["1", "2", 0])
    assert disposition["disposition"] == "course_geometry_replaced"


def test_unique_match_applies_course_static_attributes_when_geometry_tolerance_fails():
    graph = _base_graph()
    old_geometry = graph[1][2][0]["geometry"]
    feature = _feature("course:static-only", [
        [114.360, 30.53003], [114.361, 30.53003],
    ], name="课程道路", road_class="motor_vehicle")
    report = _matched_report("course:static-only", [["1", "2", 0], ["2", "1", 0]])
    report["matches"][0]["evidence"] = {
        "candidate_count": 1,
        "coverage": 1.0,
        "median_distance_m": 3.2,
        "p95_distance_m": 3.3,
        "direction_difference_deg": 0.0,
        "grade_separation_conflict": False,
    }

    fused, migration = fuse_network(graph, [feature], report)

    assert fused[1][2][0]["geometry"] == old_geometry
    assert fused[1][2][0]["name"] == "课程道路"
    assert fused[1][2][0]["course_road_class"] == "motor_vehicle"
    assert "course_geometry_replaced" not in fused[1][2][0]
    disposition = migration["course_feature_dispositions"][0]
    assert disposition["geometry_replaced_edges"] == []
    assert disposition["attributes_applied_edges"]
    assert disposition["active_release"] is True
    assert disposition["reason"] == "course_static_attributes_applied_geometry_retained"


def test_conflicting_course_static_values_do_not_overwrite_one_another_by_order():
    graph = _base_graph()
    original_geometry = graph[1][2][0]["geometry"]
    features = [
        _feature("course:conflict-a", [[114.360, 30.53003], [114.361, 30.53003]],
                 row=7, name="课程名甲"),
        _feature("course:conflict-b", [[114.360, 30.53003], [114.361, 30.53003]],
                 row=8, name="课程名乙"),
    ]
    report = {"matches": []}
    for feature in features:
        report["matches"].append({
            "source_id": feature["properties"]["source_id"],
            "action": "matched",
            "candidates": [{"candidate_id": "same-edge", "edge_ids": [["1", "2", 0]]}],
            "evidence": {"candidate_count": 1, "coverage": 1.0,
                         "median_distance_m": 3.2, "p95_distance_m": 3.3,
                         "direction_difference_deg": 0.0},
        })

    fused, migration = fuse_network(graph, features, report)

    assert fused[1][2][0]["geometry"] == original_geometry
    assert "course_source_ids" not in fused[1][2][0]
    rows = {row["source_id"]: row for row in migration["course_feature_dispositions"]}
    assert all(row.get("attribute_conflict_edges") for row in rows.values())
    assert all(row["active_release"] is False for row in rows.values())


def test_geometry_replacement_keeps_course_provenance_when_static_values_conflict():
    graph = _base_graph()
    features = [
        _feature("course:geometry", [
            [114.360, 30.530005], [114.361, 30.530005],
        ], row=9, name="课程名甲"),
        _feature("course:static-conflict", [
            [114.360, 30.53003], [114.361, 30.53003],
        ], row=10, name="课程名乙"),
    ]
    report = {"matches": []}
    for feature in features:
        report["matches"].append({
            "source_id": feature["properties"]["source_id"],
            "action": "matched",
            "candidates": [{"candidate_id": "same-edge", "edge_ids": [["1", "2", 0]]}],
            "evidence": {"candidate_count": 1, "coverage": 1.0,
                         "median_distance_m": 3.2, "p95_distance_m": 3.3,
                         "direction_difference_deg": 0.0},
        })

    fused, migration = fuse_network(graph, features, report)

    edge = fused[1][2][0]
    assert edge["course_geometry_replaced"] is True
    assert edge["course_source_ids"] == ["course:geometry"]
    assert edge["course_geometry_source"] == "WHU coursework"
    assert "name" not in edge
    assert migration["course_feature_dispositions"][0]["geometry_replaced_edges"]
    assert migration["course_feature_dispositions"][1].get("attribute_conflict_edges")


def test_new_course_t_junction_splits_old_edges_and_keeps_routes_connected():
    graph = _base_graph()
    graph.add_node(3, x=114.3605, y=30.5305)
    feature = _feature("course:t-connector", [
        [114.3605, 30.530], [114.3605, 30.5305],
    ], row=1)

    fused, migration = fuse_network(graph, [feature], _new_report([feature]))

    assert fused.number_of_edges() == 6
    assert nx.has_path(fused, 1, 3)
    assert nx.has_path(fused, 3, 2)
    course_edges = [data for _, _, data in fused.edges(data=True)
                    if data.get("course_source_id") == "course:t-connector"]
    assert len(course_edges) == 2
    assert all(json.loads(data["allowed_modes"]) == ["walk"] for data in course_edges)
    assert migration["course_feature_dispositions"][0]["active_release"] is True
    assert any(row["disposition"] == "split" for row in migration["old_edge_dispositions"])
    assert len(migration["old_edge_dispositions"]) == graph.number_of_edges()
    split_edges = [(u, v, data) for u, v, data in fused.edges(data=True)
                   if data.get("edge_split_from")]
    assert split_edges
    for u, v, data in split_edges:
        geometry = wkt.loads(data["geometry"])
        assert all(114.0 < lng < 115.0 and 30.0 < lat < 31.0
                   for lng, lat in geometry.coords)
        assert abs(geometry.coords[0][0] - fused.nodes[u]["x"]) < 1e-6
        assert abs(geometry.coords[0][1] - fused.nodes[u]["y"]) < 1e-6
        assert abs(geometry.coords[-1][0] - fused.nodes[v]["x"]) < 1e-6
        assert abs(geometry.coords[-1][1] - fused.nodes[v]["y"]) < 1e-6


def test_course_endpoint_does_not_split_a_grade_separated_osm_edge():
    graph = _base_graph()
    graph[1][2][0]["bridge"] = "yes"
    graph[1][2][0]["layer"] = "1"
    graph[2][1][0]["bridge"] = "yes"
    graph[2][1][0]["layer"] = "1"
    graph.add_node(3, x=114.3605, y=30.5305)
    feature = _feature("course:bridge-crossing", [
        [114.3605, 30.530], [114.3605, 30.5305],
    ], row=2)

    fused, migration = fuse_network(graph, [feature], _new_report([feature]))

    assert fused.number_of_edges() == graph.number_of_edges()
    assert all(data.get("course_source_id") != "course:bridge-crossing"
               for _, _, data in fused.edges(data=True))
    assert migration["course_feature_dispositions"][0]["active_release"] is False
    assert migration["course_feature_dispositions"][0]["reason"] == "insufficient_safe_anchors"


def test_ambiguous_course_match_leaves_existing_graph_unchanged():
    graph = _base_graph()
    feature = _feature("course:ambiguous", [
        [114.360, 30.530], [114.361, 30.530],
    ], row=3)
    report = {"matches": [{"source_id": "course:ambiguous", "action": "ambiguous",
                           "candidates": []}]}

    fused, migration = fuse_network(graph, [feature], report)

    assert fused.number_of_edges() == graph.number_of_edges()
    assert migration["course_feature_dispositions"][0]["reason"] == "ambiguous_correspondence"


def test_connected_course_chain_audits_component_external_anchors():
    graph = _base_graph()
    first = _feature("course:chain-a", [[114.360, 30.530], [114.3605, 30.530]], row=4)
    second = _feature("course:chain-b", [[114.3605, 30.530], [114.361, 30.530]], row=5)
    pending = _feature("course:pending", [[114.365, 30.530], [114.365, 30.531]], row=6)

    features = [first, second, pending]
    fused, migration = fuse_network(graph, features, _new_report(features))

    assert nx.has_path(fused, 1, 2)
    rows = {row["source_id"]: row for row in migration["course_feature_dispositions"]}
    for source_id in ("course:chain-a", "course:chain-b"):
        assert rows[source_id]["active_release"] is True
        assert rows[source_id]["component_source_ids"] == ["course:chain-a", "course:chain-b"]
        assert rows[source_id]["component_anchor_count"] >= 2
        assert all(anchor["attachment"]["kind"] in {"node", "edge"}
                   for anchor in rows[source_id]["component_anchors"])
        assert all(anchor["release_node_ids"] for anchor in rows[source_id]["component_anchors"])
    assert rows["course:pending"]["active_release"] is False
    assert rows["course:pending"]["component_source_ids"] == ["course:pending"]


def _course_junction_fixture():
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=114.3545774, y=30.533959)
    graph.add_node(10, x=114.3550061, y=30.5338619, course_source_node="true")
    graph.add_node(20, x=114.3545850851, y=30.5340030304, course_source_node="true")
    graph.add_node(30, x=114.3542931, y=30.5341160, course_source_node="true")
    graph.add_edge(10, 20, key=0, length=43.3, highway="footway",
                   course_source_id="course:210", course_source_ids='["course:210"]',
                   allowed_modes='["walk"]')
    graph.add_edge(20, 10, key=0, length=43.3, highway="footway",
                   course_source_id="course:210", course_source_ids='["course:210"]',
                   allowed_modes='["walk"]')
    graph.add_edge(20, 30, key=0, length=34.5, highway="footway",
                   course_source_id="course:270", course_source_ids='["course:270"]',
                   allowed_modes='["walk"]')
    graph.add_edge(30, 20, key=0, length=34.5, highway="footway",
                   course_source_id="course:270", course_source_ids='["course:270"]',
                   allowed_modes='["walk"]')
    existing_anchors = [
        {"attachment": {"kind": "node", "node_id": "100"},
         "release_node_ids": ["100"]},
        {"attachment": {"kind": "node", "node_id": "101"},
         "release_node_ids": ["101"]},
    ]
    migration = {
        "course_feature_dispositions": [
            {"source_id": "course:210", "source_row": 210,
             "active_release": True, "match_action": "new_candidate",
             "component_source_ids": ["course:210", "course:270"],
             "component_anchor_count": 2, "component_anchors": existing_anchors.copy(),
             "added_edge_ids": [["10", "20", 0], ["20", "10", 0]]},
            {"source_id": "course:270", "source_row": 270,
             "active_release": True, "match_action": "new_candidate",
             "component_source_ids": ["course:210", "course:270"],
             "component_anchor_count": 2, "component_anchors": existing_anchors.copy(),
             "added_edge_ids": [["20", "30", 0], ["30", "20", 0]]},
        ],
        "new_graph_counts": {"nodes": graph.number_of_nodes(),
                             "edges": graph.number_of_edges()},
    }
    policy = {
        "schema_version": 1,
        "overrides": [{
            "id": "course-path-to-gate",
            "course_sources": [
                {"source_id": "course:210", "source_row": 210},
                {"source_id": "course:270", "source_row": 270},
            ],
            "target_osm_node_id": "1",
            "target_osm_access": {"barrier": "gate", "foot": "yes"},
            "max_gap_m": 5.5,
            "allowed_modes": ["walk"],
            "verification_status": "source_only",
            "evidence_status": "user_confirmed_source_correlated",
            "reason": "Connect the school road source to the mapped campus gate.",
            "building_clearance_audit": {
                "snapshot_sha256": "a" * 64,
                "features_checked": 1,
                "intersections": 0,
                "nearest_building_clearance_m": 5.0,
            },
        }],
    }
    return graph, migration, policy


def test_course_junction_override_connects_verified_source_path_to_osm_gate_walk_only():
    graph, migration, policy = _course_junction_fixture()

    applied = apply_course_junction_overrides(graph, migration, policy)

    assert len(applied) == 1
    assert applied[0]["distance_m"] == pytest.approx(4.94, abs=0.02)
    assert graph.has_edge(20, 1)
    assert graph.has_edge(1, 20)
    forward = graph[20][1][0]
    reverse = graph[1][20][0]
    assert json.loads(forward["allowed_modes"]) == ["walk"]
    assert json.loads(reverse["allowed_modes"]) == ["walk"]
    assert forward["course_junction_override_id"] == "course-path-to-gate"
    assert len(migration["course_junction_overrides"]) == 1
    assert migration["new_graph_counts"] == {
        "nodes": graph.number_of_nodes(), "edges": graph.number_of_edges()
    }
    for row in migration["course_feature_dispositions"]:
        assert row["component_anchor_count"] == 3
        assert any("1" in anchor["release_node_ids"]
                   for anchor in row["component_anchors"])


def test_course_junction_override_rejects_gap_above_declared_limit_without_mutation():
    graph, migration, policy = _course_junction_fixture()
    policy["overrides"][0]["max_gap_m"] = 4.0
    before_edges = graph.number_of_edges()

    with pytest.raises(ValueError, match="exceeds the declared maximum"):
        apply_course_junction_overrides(graph, migration, policy)

    assert graph.number_of_edges() == before_edges


def test_live_yulan_gate_connects_course_path_only_in_walking_graph():
    root = Path(__file__).resolve().parents[1]
    graph = nx.read_graphml(root / "data/whu_road_network.graphml", node_type=int)
    gate_node = next(node for node in graph.nodes if str(node) == "1204194363")
    course_node = next(node for node in graph.nodes if str(node) == "13732671009")
    forward = [edge for edge in graph[gate_node][course_node].values()
               if edge.get("course_junction_override_id")
               == "yulan-2-gate-to-information-liberal-arts-course-path"]
    reverse = [edge for edge in graph[course_node][gate_node].values()
               if edge.get("course_junction_override_id")
               == "yulan-2-gate-to-information-liberal-arts-course-path"]

    assert len(forward) == len(reverse) == 1
    assert 4.9 <= float(forward[0]["length"]) <= 5.0
    assert json.loads(forward[0]["allowed_modes"]) == ["walk"]
    assert json.loads(reverse[0]["allowed_modes"]) == ["walk"]

    from spatial.routing import filter_graph_for_mode
    walk_graph, _, _ = filter_graph_for_mode(graph, "walk")
    bike_graph, _, _ = filter_graph_for_mode(graph, "bike")
    drive_graph, _, _ = filter_graph_for_mode(graph, "drive")
    component_anchor_a = next(node for node in graph.nodes if str(node) == "13732671011")
    component_anchor_b = next(node for node in graph.nodes if str(node) == "13732671013")
    assert nx.has_path(walk_graph, gate_node, component_anchor_a)
    assert nx.has_path(walk_graph, gate_node, component_anchor_b)
    assert not bike_graph.has_edge(gate_node, course_node)
    assert not drive_graph.has_edge(gate_node, course_node)
