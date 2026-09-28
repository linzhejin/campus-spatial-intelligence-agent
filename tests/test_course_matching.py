import sys
import json
from pathlib import Path

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from spatial.course_matching import classify_match, match_course_features


def test_nearby_bridge_is_not_automatically_the_same_road():
    assert classify_match({
        "coverage": 0.99,
        "median_distance_m": 1.0,
        "p95_distance_m": 2.0,
        "direction_difference_deg": 2.0,
        "candidate_count": 1,
        "grade_separation_conflict": True,
        "endpoint_correspondence": True,
        "name_relation": "same",
    }) == "ambiguous"


def test_high_confidence_unique_same_road_match_is_accepted():
    assert classify_match({
        "coverage": 0.98,
        "median_distance_m": 1.0,
        "p95_distance_m": 3.0,
        "direction_difference_deg": 5.0,
        "candidate_count": 1,
        "grade_separation_conflict": False,
        "endpoint_correspondence": True,
        "name_relation": "compatible",
    }) == "matched"


def test_local_curvature_does_not_reject_an_otherwise_unique_match():
    assert classify_match({
        "coverage": 0.98,
        "median_distance_m": 1.0,
        "p95_distance_m": 3.0,
        "direction_difference_deg": 32.0,
        "candidate_count": 1,
        "grade_separation_conflict": False,
        "endpoint_correspondence": True,
        "name_relation": "unknown",
    }) == "matched"


def test_no_nearby_osm_candidate_is_a_new_course_candidate():
    assert classify_match({
        "coverage": 0.0,
        "median_distance_m": 35.0,
        "p95_distance_m": 58.0,
        "direction_difference_deg": None,
        "candidate_count": 0,
        "grade_separation_conflict": False,
        "endpoint_correspondence": False,
        "name_relation": "unknown",
    }) == "new_candidate"


def test_parallel_or_conflicting_name_candidates_require_review():
    evidence = {
        "coverage": 0.99,
        "median_distance_m": 1.0,
        "p95_distance_m": 3.0,
        "direction_difference_deg": 2.0,
        "candidate_count": 2,
        "grade_separation_conflict": False,
        "endpoint_correspondence": True,
        "name_relation": "conflict",
    }
    assert classify_match(evidence) == "ambiguous"


def test_close_but_incomplete_geometry_is_not_forced_into_an_osm_match():
    assert classify_match({
        "coverage": 0.55,
        "median_distance_m": 5.0,
        "p95_distance_m": 14.0,
        "direction_difference_deg": 20.0,
        "candidate_count": 1,
        "grade_separation_conflict": False,
        "endpoint_correspondence": False,
        "name_relation": "unknown",
    }) == "ambiguous"


def test_missing_osm_nearest_distance_serializes_as_standard_json():
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=114.350, y=30.530)
    graph.add_node(2, x=114.351, y=30.530)
    graph.add_edge(1, 2, key=0, osmid=1,
                   geometry="LINESTRING (114.35 30.53, 114.351 30.53)")
    feature = {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [
            [114.365, 30.540], [114.366, 30.540],
        ]},
        "properties": {"source_id": "course:road:far", "source_row": 0, "name": None},
    }
    result = match_course_features([feature], graph)
    assert result["matches"][0]["action"] == "new_candidate"
    assert json.dumps(result["matches"][0], allow_nan=False)


def test_one_course_segment_can_match_a_chain_split_across_osm_ways():
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=114.360, y=30.530)
    graph.add_node(2, x=114.361, y=30.530)
    graph.add_node(3, x=114.362, y=30.530)
    graph.add_edge(1, 2, key=0, osmid=10,
                   geometry="LINESTRING (114.36 30.53, 114.361 30.53)")
    graph.add_edge(2, 1, key=0, osmid=10,
                   geometry="LINESTRING (114.361 30.53, 114.36 30.53)")
    graph.add_edge(2, 3, key=0, osmid=11,
                   geometry="LINESTRING (114.361 30.53, 114.362 30.53)")
    graph.add_edge(3, 2, key=0, osmid=11,
                   geometry="LINESTRING (114.362 30.53, 114.361 30.53)")
    feature = {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [
            [114.360, 30.530], [114.361, 30.530], [114.362, 30.530],
        ]},
        "properties": {"source_id": "course:road:chain", "source_row": 0, "name": None},
    }

    match = match_course_features([feature], graph)["matches"][0]

    assert match["action"] == "matched"
    assert match["evidence"]["candidate_count"] == 1
    assert len(match["candidates"][0]["edge_ids"]) == 4
