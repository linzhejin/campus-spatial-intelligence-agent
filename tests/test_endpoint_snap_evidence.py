from types import SimpleNamespace

import networkx as nx
import pytest

import agents.tools as tools
import api.routes as routes


def _graph():
    graph = nx.MultiDiGraph()
    graph.graph["travel_mode"] = "walk"
    graph.add_node(1, x=114.36, y=30.53)
    graph.add_node(2, x=114.361, y=30.53)
    graph.add_edge(1, 2, 0, length=100.0)
    return graph


def test_endpoint_evidence_discloses_unverified_connector_distance():
    graph = _graph()
    ref = {"type": "coord", "name": "地图起点", "coordinates": {"lng": 114.3601, "lat": 30.53}}
    node, _, error = tools._resolve_endpoint(ref, graph)

    assert error is None
    evidence = tools._endpoint_access_evidence(ref, node, graph, "walk")
    assert evidence["snap_distance_m"] > 0
    assert evidence["access_link_verified"] is False
    assert evidence["status"] == "unverified_nearby_network_node"


def test_endpoint_resolution_rejects_excessive_distance_from_network():
    graph = _graph()
    ref = {"type": "coord", "name": "偏远起点", "coordinates": {"lng": 114.365, "lat": 30.53}}

    node, _, error = tools._resolve_endpoint(ref, graph)

    assert node is None
    assert error["error"] == "endpoint_too_far_from_network"
    assert error["snap_distance_m"] > error["max_access_snap_m"]


def test_multi_via_coordinate_uses_endpoint_snap_cap(monkeypatch):
    graph = _graph()
    monkeypatch.setattr(tools, "_plan_common", lambda args, ctx: (graph, graph, "walk", None))
    context = {
        "_route_graph_snapshot": graph,
        "_route_conditions_snapshot": [],
        "_route_weather_snapshot": None,
    }

    result, artifact = tools._tool_plan_via_route({
        "start": {"type": "coord", "coordinates": {"lng": 114.36, "lat": 30.53}},
        "end": {"type": "coord", "coordinates": {"lng": 114.361, "lat": 30.53}},
        "via_points": [{"type": "coord", "coordinates": {"lng": 114.365, "lat": 30.53}}],
    }, context)

    assert result["error"] == "endpoint_too_far_from_network"
    assert artifact is None


def test_multimodal_transfer_uses_snap_cap_when_node_is_not_in_next_mode(monkeypatch):
    graph = nx.MultiDiGraph()
    graph.add_nodes_from([
        (1, {"x": 114.36, "y": 30.53}),
        (2, {"x": 114.361, "y": 30.53}),
        (3, {"x": 114.37, "y": 30.53}),
        (4, {"x": 114.371, "y": 30.53}),
    ])
    graph.add_edge(1, 2, key=0, length=100)
    walk_graph = nx.MultiDiGraph(graph.subgraph([1, 2]).copy())
    walk_graph.graph["travel_mode"] = "walk"
    bike_graph = nx.MultiDiGraph(graph.subgraph([3, 4]).copy())
    bike_graph.graph["travel_mode"] = "bike"
    index = type("Index", (), {
        "for_mode": lambda self, mode: type("Mode", (), {
            "graph": walk_graph if mode == "walk" else bike_graph,
        })(),
    })()
    monkeypatch.setattr(tools, "get_routing_index", lambda _graph: index)
    monkeypatch.setattr(tools, "_strategy_for_args", lambda *args, **kwargs: type("Decision", (), {
        "weights": {"distance": 1, "slope": 0, "scenery": 0},
        "name": "shortest", "detour_cap": 1,
        "as_dict": lambda self: {"name": "shortest"},
    })())
    monkeypatch.setattr(tools, "compute_route", lambda **kwargs: {})
    monkeypatch.setattr(tools, "_route_payload", lambda _graph, _result, start, end, mode: {
        "start_name": start, "end_name": end, "mode": mode,
        "recommended": [{"lng": 114.36, "lat": 30.53}, {"lng": 114.361, "lat": 30.53}],
        "recommended_edge_ids": [(1, 2, 0)], "steps": [], "pois": [],
        "recommended_length_m": 100, "distance_m": 100, "duration_min": 2,
    })
    context = {
        "_route_graph_snapshot": graph,
        "_route_conditions_snapshot": [],
        "_route_weather_snapshot": None,
    }

    result, artifact = tools._tool_plan_multimodal_route({
        "start": {"type": "coord", "coordinates": {"lng": 114.36, "lat": 30.53}},
        "legs": [
            {"mode": "walk", "end": {"type": "coord", "coordinates": {"lng": 114.361, "lat": 30.53}}},
            {"mode": "bike", "end": {"type": "coord", "coordinates": {"lng": 114.37, "lat": 30.53}}},
        ],
    }, context)

    assert result["error"] == "endpoint_too_far_from_network"
    assert "换乘点" in result["message"]
    assert artifact is None


def test_tour_excludes_pois_far_from_the_routable_network(monkeypatch):
    graph = _graph()
    far_pois = [
        {"name": "远处景点A", "type": "scenery", "subcategory": "landmark", "lat": 30.53, "lon": 114.37},
        {"name": "远处景点B", "type": "scenery", "subcategory": "landmark", "lat": 30.53, "lon": 114.371},
    ]
    monkeypatch.setattr(tools, "_plan_common", lambda args, ctx: (graph, graph, "walk", None))
    monkeypatch.setattr(tools, "search_by_category", lambda **kwargs: far_pois)
    monkeypatch.setattr(tools, "gcj02_to_wgs84", lambda lng, lat: (lng, lat))
    seen = {}

    def compute_tour(_graph, poi_nodes, **kwargs):
        seen["poi_nodes"] = poi_nodes
        return {"legs": [], "ordered_pois": [], "dropped": far_pois, "loop": True}

    monkeypatch.setattr(tools, "compute_tour_route", compute_tour)
    context = {
        "_route_graph_snapshot": graph,
        "_route_conditions_snapshot": [],
        "_route_weather_snapshot": None,
    }

    result, artifact = tools._tool_plan_tour({"theme": "scenery"}, context)

    assert seen["poi_nodes"] == []
    assert result["error"] == "tour_failed"
    assert artifact is None


def test_tour_does_not_replace_an_unresolved_named_stop_with_generic_scenery(monkeypatch):
    graph = _graph()
    known = {"id": "poi_luojia", "name": "武汉大学珞珈山", "type": "scenery",
             "subcategory": "hill", "lat": 30.53, "lon": 114.36}
    monkeypatch.setattr(tools, "_plan_common", lambda args, ctx: (graph, graph, "walk", None))
    monkeypatch.setattr(tools, "find_poi_ambiguous",
                        lambda name: (known, []) if name == "珞珈山" else (None, []))
    monkeypatch.setattr(tools, "search_by_category",
                        lambda **kwargs: pytest.fail("named stops must not fall back to generic POIs"))
    context = {
        "_route_graph_snapshot": graph,
        "_route_conditions_snapshot": [],
        "_route_weather_snapshot": None,
    }

    result, artifact = tools._tool_plan_tour(
        {"theme": "scenery", "poi_names": ["珞珈山", "东湖"]}, context,
    )

    assert result["error"] == "tour_poi_not_found"
    assert result["missing_pois"] == ["东湖"]
    assert artifact is None


@pytest.mark.parametrize("query", [
    "我要游览珞珈山和东湖",
    "规划一条经过珞珈山和东湖的游览路线",
    "规划珞珈山和东湖的游览路线",
    "珞珈山和东湖游览路线",
    "去珞珈山和东湖逛逛",
])
def test_tour_infers_named_destinations_from_query_when_model_omits_poi_names(query):
    names, missing = tools._infer_explicit_tour_poi_names(query)

    assert names == ["武汉大学珞珈山", "凌波门东湖观景点"]
    assert missing == []


@pytest.mark.parametrize("query", [
    "我不想逛珞珈山和东湖，推荐一条赏樱路线",
    "先别逛珞珈山和东湖，推荐其他景点",
])
def test_tour_exclusion_query_resolves_places_to_exclude(query):
    excluded = tools._infer_excluded_tour_poi_names(query)

    assert excluded == {"武汉大学珞珈山", "凌波门东湖观景点"}


def test_tour_inference_keeps_positive_stops_after_a_negated_clause():
    names, missing = tools._infer_explicit_tour_poi_names(
        "我不想逛珞珈山和东湖，但想游览老斋舍和樱花大道",
    )

    assert names == ["武汉大学老斋舍", "武汉大学樱花大道"]
    assert missing == []


@pytest.mark.parametrize("query", [
    "我不想逛珞珈山和东湖，推荐一条赏樱路线",
    "先别逛珞珈山和东湖，推荐其他景点",
])
def test_tour_does_not_route_explicitly_excluded_stops_when_model_passes_them(monkeypatch, query):
    graph = _graph()
    luojia = {"id": "poi_luojia", "name": "武汉大学珞珈山", "type": "scenery",
              "subcategory": "hill", "lat": 30.53, "lon": 114.36}
    donghu = {"id": "poi_donghu", "name": "凌波门东湖观景点", "type": "scenery",
              "subcategory": "lake", "lat": 30.53, "lon": 114.36}
    other = [
        {"id": "poi_other_1", "name": "老斋舍", "type": "scenery",
         "subcategory": "landmark", "lat": 30.53, "lon": 114.36},
        {"id": "poi_other_2", "name": "樱花大道", "type": "scenery",
         "subcategory": "sakura", "lat": 30.53, "lon": 114.36},
    ]
    lookup = {
        "珞珈山": luojia, "武汉大学珞珈山": luojia,
        "东湖": donghu, "凌波门东湖观景点": donghu,
    }
    monkeypatch.setattr(tools, "_plan_common", lambda args, ctx: (graph, graph, "walk", None))
    monkeypatch.setattr(tools, "find_poi_ambiguous",
                        lambda name: (lookup.get(name), []))
    monkeypatch.setattr(tools, "search_by_category", lambda **kwargs: [luojia, donghu, *other])
    monkeypatch.setattr(tools, "_snap_wgs_to_network",
                        lambda lon, lat, graph, name: (1, {"name": name}, None))
    monkeypatch.setattr(tools, "importance_score", lambda poi: 0.5)
    monkeypatch.setattr(tools, "_strategy_for_args", lambda args, ctx: SimpleNamespace(
        name="recommended", weights={}, detour_cap=1.5,
        as_dict=lambda: {"name": "recommended"},
    ))
    seen = {}

    def capture_stops(_graph, poi_nodes, **kwargs):
        seen["names"] = [poi["name"] for poi, _ in poi_nodes]
        return {"legs": [], "ordered_pois": [], "total_length_m": 0,
                "duration_min": 0, "dropped": [], "loop": True}

    monkeypatch.setattr(tools, "compute_tour_route", capture_stops)
    result, _ = tools._tool_plan_tour({
        "theme": "scenery", "poi_names": ["珞珈山", "东湖"], "loop": True,
    }, {
        "query": query,
        "_route_graph_snapshot": graph,
        "_route_conditions_snapshot": [],
        "_route_weather_snapshot": None,
    })

    assert seen["names"] == ["老斋舍", "樱花大道"]
    assert result["error"] == "tour_failed"


def test_agent_chat_adapter_preserves_endpoint_access_evidence():
    evidence = {"start": {"snap_distance_m": 24.0, "access_link_verified": False}}

    adapted = routes._agent_response_to_legacy({
        "response_kind": "route",
        "route": {"start_name": "起点", "end_name": "终点", "endpoint_access": evidence},
    })

    assert adapted["endpoint_access"] == evidence
