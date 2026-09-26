"""Mode permissions must survive dense OSM road reconstruction."""
import networkx as nx
import pytest

from spatial import routing


def graph_with_edge(**attrs):
    g = nx.MultiDiGraph()
    g.add_node(1, x=114.36, y=30.535)
    g.add_node(2, x=114.3601, y=30.535)
    g.add_edge(1, 2, length=10.0, **attrs)
    return g


def test_empty_drive_filter_does_not_restore_steps():
    g = graph_with_edge(highway="steps")
    mode, status, _ = routing.filter_graph_for_mode(g, "drive")
    assert mode.number_of_edges() == 0
    assert "empty" in status


def test_explicit_foot_permission_keeps_corridor():
    g = graph_with_edge(highway="corridor", indoor="yes", foot="yes")
    mode, _, _ = routing.filter_graph_for_mode(g, "walk")
    assert mode.has_edge(1, 2)


def test_unpermitted_indoor_corridor_is_not_an_outdoor_walk_connection():
    g = graph_with_edge(highway="corridor", indoor="yes")
    mode, _, _ = routing.filter_graph_for_mode(g, "walk")
    assert not mode.has_edge(1, 2)


def test_unpermitted_indoor_footway_is_not_an_outdoor_walk_connection():
    g = graph_with_edge(highway="footway", indoor="room")
    mode, _, _ = routing.filter_graph_for_mode(g, "walk")
    assert not mode.has_edge(1, 2)


def test_access_specificity_and_no_tag_not_blocked():
    from spatial.osm_access import edge_access
    assert edge_access({"highway": "service"}, "walk")[0]
    assert not edge_access({"highway": "service", "access": "private"}, "walk")[0]
    assert edge_access({"highway": "service", "access": "private", "foot": "yes"}, "walk")[0]
    assert not edge_access({"highway": "service", "access": "permit"}, "walk")[0]
    assert not edge_access({"highway": "service", "foot": "permit"}, "walk")[0]
    assert edge_access({"highway": "service", "access": "permit", "foot": "yes"}, "walk")[0]
    assert not edge_access({"highway": "service", "vehicle": "no", "bicycle": "yes"}, "drive")[0]
    assert edge_access({"highway": "service", "vehicle": "no", "bicycle": "yes"}, "bike")[0]


def test_same_way_walk_two_way_drive_one_way():
    from spatial.osm_access import edge_access
    back = {"highway": "residential", "oneway": "yes", "osm_way_forward": False}
    assert edge_access(back, "walk")[0]
    assert not edge_access(back, "drive")[0]
    assert not edge_access(back, "bike")[0]
    assert edge_access({**back, "oneway:bicycle": "no"}, "bike")[0]
    assert not edge_access({**back, "oneway:foot": "yes"}, "walk")[0]


def test_explicit_reverse_oneway_and_roundabout():
    from spatial.osm_access import edge_access
    assert not edge_access({"highway": "service", "oneway": "-1", "osm_way_forward": True}, "drive")[0]
    assert edge_access({"highway": "service", "oneway": "-1", "osm_way_forward": False}, "drive")[0]
    assert not edge_access({"highway": "service", "junction": "roundabout", "osm_way_forward": False}, "drive")[0]


def test_barrier_rules_and_unknown_gate():
    from spatial.osm_access import node_access
    assert node_access({"barrier": "gate"}, "walk")[0]
    assert node_access({"barrier": "bollard"}, "bike")[0]
    assert not node_access({"barrier": "bollard"}, "drive")[0]
    assert not node_access({"barrier": "gate", "motor_vehicle": "no"}, "drive")[0]
    allowed, advisory = node_access({"barrier": "gate", "foot": "permit"}, "walk")
    assert not allowed and "permit" in advisory


def test_emergency_entrance_is_not_a_public_walk_connection():
    from spatial.osm_access import node_access
    allowed, reason = node_access({"entrance": "emergency"}, "walk")
    assert not allowed
    assert "emergency" in reason


def test_mode_filter_uses_node_and_way_access():
    g = graph_with_edge(highway="service", foot="no", bicycle="yes")
    assert routing.filter_graph_for_mode(g, "walk")[0].number_of_edges() == 0
    g.nodes[2]["barrier"] = "bollard"
    assert routing.filter_graph_for_mode(g, "drive")[0].number_of_edges() == 0
    assert routing.filter_graph_for_mode(g, "bike")[0].number_of_edges() == 1


def test_polygon_filter_preserves_edge_that_crosses_campus(monkeypatch):
    from shapely.geometry import Polygon
    g = nx.MultiDiGraph()
    g.add_node(1, x=-1, y=.5)
    g.add_node(2, x=2, y=.5)
    g.add_edge(1, 2, geometry="LINESTRING (-1 0.5, 2 0.5)")
    monkeypatch.setattr(routing, "_outside_edge_cache", {})
    monkeypatch.setattr(routing, "get_campus_polygon", lambda: Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]))
    assert not routing._get_outside_edges(g)
