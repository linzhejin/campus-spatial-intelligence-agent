import sys
from pathlib import Path

import networkx as nx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from spatial.course_topology_policy import validate_topology_exceptions


def _feature(source_id, row, coordinates, *, blocked_modes=None):
    return {
        "type": "Feature",
        "id": source_id,
        "geometry": {"type": "LineString", "coordinates": coordinates},
        "properties": {
            "source_id": source_id,
            "source_row": row,
            "source_layer": "whu_road",
            "name": "自强大道",
            "blocked_modes": blocked_modes or [],
        },
    }


def _connected_graph():
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=114.3605, y=30.53)
    graph.add_node(2, x=114.3610, y=30.53)
    graph.add_node(3, x=114.3615, y=30.53)
    graph.add_edge(1, 2, key=0, length=48.0)
    graph.add_edge(2, 3, key=0, length=48.0)
    graph.add_edge(2, 1, key=0, length=48.0)
    graph.add_edge(3, 2, key=0, length=48.0)
    return graph


def _exception():
    return {
        "id": "ziqiang-test-gap",
        "source_layer": "whu_road",
        "source_rows": [1, 2],
        "expected_name": "自强大道",
        "from_endpoint": "end",
        "to_endpoint": "start",
        "gap_distance_m": {"min": 80.0, "max": 120.0},
        "max_endpoint_snap_m": 10.0,
        "walk_must_remain_connected": True,
        "active_new_candidate_forbidden": True,
    }


def _features():
    return [
        _feature("course:ziqiang:1", 1,
                 [[114.3600, 30.53], [114.3605, 30.53]]),
        _feature("course:ziqiang:2", 2,
                 [[114.3615, 30.53], [114.3620, 30.53]]),
    ]


def _migration(action="matched", active=False):
    return {"course_feature_dispositions": [
        {"source_id": "course:ziqiang:1", "match_action": action,
         "active_release": active},
        {"source_id": "course:ziqiang:2", "match_action": "matched",
         "active_release": False},
    ]}


def test_intentional_gap_keeps_existing_walk_connectivity_and_never_blocks():
    graph = _connected_graph()

    results = validate_topology_exceptions(
        graph, graph.copy(), _features(), _migration(), [_exception()]
    )

    row = results[0]
    assert row["intentional_gap_distance_m"] == pytest.approx(95.8, abs=2)
    assert row["base_walk_route_exists"] is True
    assert row["release_walk_route_exists"] is True
    assert row["policy"] == "preserve_existing_walk_topology_no_closure_or_gap_connector"


def test_intentional_gap_rejects_course_data_that_blocks_walking():
    features = _features()
    features[0]["properties"]["blocked_modes"] = ["walk"]

    with pytest.raises(ValueError, match="must not block walking"):
        validate_topology_exceptions(
            _connected_graph(), _connected_graph(), features, _migration(), [_exception()]
        )


def test_intentional_gap_requires_walk_route_in_base_and_release_graphs():
    base = _connected_graph()
    release = _connected_graph()
    release.remove_edge(2, 3, 0)
    release.remove_edge(3, 2, 0)

    with pytest.raises(ValueError, match="walking connectivity was lost"):
        validate_topology_exceptions(
            base, release, _features(), _migration(), [_exception()]
        )


def test_intentional_gap_must_not_activate_as_an_unmatched_new_connector():
    with pytest.raises(ValueError, match="must remain pending"):
        validate_topology_exceptions(
            _connected_graph(), _connected_graph(), _features(),
            _migration(action="new_candidate", active=True), [_exception()]
        )
