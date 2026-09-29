import re

import networkx as nx
import pytest

from scripts.network import build_course_data_release
from scripts.network.build_course_data_release import _stabilize_graphml_key_ids


_KEY_RE = re.compile(
    r'<key\s+id="(?P<id>[^"]+)"\s+for="(?P<scope>[^"]+)"\s+'
    r'attr\.name="(?P<name>[^"]+)"\s+attr\.type="(?P<type>[^"]+)"\s*/>'
)


def _key_map(path):
    return {
        (row["scope"], row["name"], row["type"]): row["id"]
        for row in (match.groupdict() for match in _KEY_RE.finditer(
            path.read_bytes().decode("utf-8")
        ))
    }


def test_release_graphml_keeps_base_key_ids_and_remains_readable(tmp_path):
    base = nx.MultiDiGraph()
    base.add_node(1, x=114.0, y=30.0)
    base.add_node(2, x=114.001, y=30.0)
    base.add_edge(1, 2, key=0, length=95.0, osmid=11)
    base_path = tmp_path / "base.graphml"
    nx.write_graphml(base, base_path)
    base_keys = _key_map(base_path)

    release = base.copy()
    release.graph["course_release_fingerprint"] = "abc123"
    release[1][2][0]["course_source_id"] = "course:row:1"
    release_path = tmp_path / "release.graphml"
    nx.write_graphml(release, release_path)

    _stabilize_graphml_key_ids(release_path, base_path)

    release_keys = _key_map(release_path)
    assert all(release_keys[key] == key_id for key, key_id in base_keys.items())
    assert len(set(release_keys.values())) == len(release_keys)
    reloaded = nx.read_graphml(release_path, node_type=int)
    assert reloaded.graph["course_release_fingerprint"] == "abc123"
    reloaded_edge = (next(iter(reloaded[1][2].values()))
                     if reloaded.is_multigraph() else reloaded[1][2])
    assert reloaded_edge["course_source_id"] == "course:row:1"


def test_release_graphml_preserves_keys_from_previous_release(tmp_path):
    base = nx.MultiDiGraph()
    base.add_node(1, x=114.0, y=30.0)
    base.add_node(2, x=114.001, y=30.0)
    base.add_edge(1, 2, key=0, length=95.0, osmid=11)
    base_path = tmp_path / "base.graphml"
    nx.write_graphml(base, base_path)

    previous = base.copy()
    previous.graph["course_release_fingerprint"] = "previous"
    previous[1][2][0]["course_source_id"] = "course:row:1"
    previous_path = tmp_path / "previous.graphml"
    nx.write_graphml(previous, previous_path)
    _stabilize_graphml_key_ids(previous_path, base_path)
    previous_keys = _key_map(previous_path)

    current = previous.copy()
    current[1][2][0]["course_routeability_override_id"] = "walkable-row-1"
    current[1][2][0]["course_routeability_override_reason"] = "user-confirmed path"
    current_path = tmp_path / "current.graphml"
    nx.write_graphml(current, current_path)

    _stabilize_graphml_key_ids(
        current_path, base_path, previous_graph_path=previous_path
    )

    current_keys = _key_map(current_path)
    assert all(current_keys[key] == key_id for key, key_id in previous_keys.items())
    assert len(set(current_keys.values())) == len(current_keys)


def test_release_spatial_validation_rejects_geometry_outside_campus_extent():
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=114.35, y=30.53)
    graph.add_node(2, x=114.36, y=30.54)
    graph.add_edge(
        1, 2, key=0, length=60.0,
        geometry="LINESTRING (109.5 0.0002, 114.35 30.53, 114.36 30.54)",
    )

    with pytest.raises(ValueError, match="outside campus extent"):
        build_course_data_release._validate_spatial_geometry(graph)


def test_component_route_audit_uses_one_reverse_multi_source_search(monkeypatch):
    graph = nx.MultiDiGraph()
    for node in (1, 2, 3, 4):
        graph.add_node(node, x=114.35 + node / 10000, y=30.53)
    for u, v, length in ((2, 1, 10.0), (3, 4, 20.0),
                         (2, 3, 100.0), (3, 2, 100.0)):
        graph.add_edge(u, v, key=0, length=length, highway="footway",
                       allowed_modes='["walk"]',
                       course_source_id="course:component" if {u, v} == {2, 3} else "")
    migration = {"course_feature_dispositions": [{
        "active_release": True,
        "component_source_ids": ["course:component"],
        "component_anchors": [
            {"release_node_ids": ["1"]}, {"release_node_ids": ["4"]},
        ],
    }]}
    calls = 0
    original = nx.multi_source_dijkstra

    def tracked(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(nx, "multi_source_dijkstra", tracked)

    result = build_course_data_release._validate_component_routes(graph, migration)

    assert calls == 1
    assert result[0]["maximum_terminal_to_anchor_distance_m"] == 20.0


def test_authoritative_dead_end_course_component_needs_one_existing_anchor():
    graph = nx.MultiDiGraph()
    for node in (1, 2, 3):
        graph.add_node(node, x=114.35 + node / 10000, y=30.53)
    graph.add_edge(2, 1, key=0, length=10.0, highway="residential",
                   allowed_modes='["walk"]')
    for u, v in ((2, 3), (3, 2)):
        graph.add_edge(u, v, key=0, length=100.0, highway="service",
                       allowed_modes='["walk"]', course_source_id="course:320")
    migration = {
        "trusted_walk_source_rows": [320],
        "course_feature_dispositions": [{
            "source_id": "course:320",
            "source_row": 320,
            "active_release": True,
            "component_source_ids": ["course:320"],
            "component_anchors": [{"release_node_ids": ["1"]}],
        }],
    }

    result = build_course_data_release._validate_component_routes(graph, migration)

    assert result[0]["anchor_nodes"] == ["1"]
    assert result[0]["maximum_terminal_to_anchor_distance_m"] == 110.0
