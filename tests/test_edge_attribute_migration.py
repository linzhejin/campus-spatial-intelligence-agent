import json

import networkx as nx

from scripts.network.build_edge_attribute_master import (
    geometry_hash,
    graph_fingerprint,
    migrate_legacy_record,
    segment_id,
)


def test_placeholder_levels_become_unknown():
    legacy = {
        "edge_id": [1, 2, 0],
        "slope_level": 3,
        "scenery_level": 3,
        "note": "坡度占位，待实测; 临近某景点 (80m)",
    }
    record = migrate_legacy_record(legacy, binding={"edge_id": [1, 2, 0]})
    assert record["terrain"]["slope_level"] is None
    assert record["terrain"]["confidence"] == "unknown"
    assert record["scenery"]["scenery_level"] is None
    assert record["scenery"]["confidence"] == "unknown"


def test_old_dem_text_is_low_confidence_candidate_not_field_measurement():
    legacy = {
        "edge_id": [1, 2, 0],
        "slope_level": 4,
        "note": "DEM实测 坡度10.0%（Δelev=5m）",
    }
    terrain = migrate_legacy_record(
        legacy, binding={"edge_id": [1, 2, 0]}
    )["terrain"]
    assert terrain["grade_abs_pct"] == 10.0
    assert terrain["slope_level"] == 4
    assert terrain["confidence"] == "low"
    assert terrain["verification_status"] == "derived_unverified"
    assert terrain["method_version"] == "legacy_endpoint_dem_unversioned"


def test_non_placeholder_scenery_level_is_still_unknown_without_components():
    legacy = {
        "edge_id": [1, 2, 0],
        "slope_level": 2,
        "scenery_level": 5,
        "note": "临近樱花大道",
    }
    scenery = migrate_legacy_record(
        legacy, binding={"edge_id": [1, 2, 0]}
    )["scenery"]
    assert scenery["scenery_level"] is None
    assert scenery["confidence"] == "unknown"


def _graph():
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=114.0, y=30.0)
    graph.add_node(2, x=114.001, y=30.001)
    graph.add_edge(
        1, 2, key=0, osmid=123, length=100.0,
        geometry="LINESTRING (114 30, 114.001 30.001)",
    )
    return graph


def test_graph_and_segment_fingerprints_are_stable_and_annotation_independent():
    graph = _graph()
    first_graph = graph_fingerprint(graph)
    first_geometry = geometry_hash(graph, 1, 2, 0)
    graph[1][2][0]["slope_level"] = 5
    graph[1][2][0]["scenery_level"] = 1
    assert graph_fingerprint(graph) == first_graph
    assert geometry_hash(graph, 1, 2, 0) == first_geometry
    refs = [{"source": "OpenStreetMap", "id": "way/123"}]
    assert segment_id(refs, first_geometry, "1>2:0") == segment_id(
        list(reversed(refs)), first_geometry, "1>2:0"
    )


def test_generated_master_binds_every_current_directed_edge_once():
    master = json.loads(
        open("data/edge_attribute_master.json", encoding="utf-8").read()
    )
    graph = nx.read_graphml("data/whu_road_network.graphml", node_type=int)
    bindings = [
        tuple(record["network_bindings"][0]["edge_id"])
        for record in master["records"]
    ]
    assert len(bindings) == graph.number_of_edges() == 12550
    assert len(set(bindings)) == len(bindings)
    assert sum(record["terrain"]["confidence"] == "unknown"
               for record in master["records"]) == 9520
    assert all(record["scenery"]["confidence"] in {"unknown", "low"}
               for record in master["records"])
    assert all(record["scenery"].get("greenery") is None
               and record["scenery"].get("shade") is None
               and record["scenery"].get("water") is None
               for record in master["records"])
