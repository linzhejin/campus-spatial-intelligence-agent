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
