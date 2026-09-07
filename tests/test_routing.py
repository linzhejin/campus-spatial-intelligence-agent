import pytest
import networkx as nx

import spatial.routing as routing_module
from spatial.routing import (
    resolve_weights,
    _normalize_lengths,
    _compute_edge_cost,
    _filter_by_constraints,
    compute_route,
)


def _build_mock_graph():
    G = nx.MultiDiGraph()
    G.add_nodes_from(range(6))
    edges = [
        (0, 1, {"length": 100.0, "slope_level": 2, "scenery_level": 3}),
        (1, 2, {"length": 200.0, "slope_level": 4, "scenery_level": 5}),
        (2, 3, {"length": 150.0, "slope_level": 5, "scenery_level": 4}),
        (3, 4, {"length": 180.0, "slope_level": 1, "scenery_level": 5}),
        (4, 5, {"length": 120.0, "slope_level": 3, "scenery_level": 2}),
        (0, 2, {"length": 350.0, "slope_level": 2, "scenery_level": 3}),
        (1, 4, {"length": 400.0, "slope_level": 3, "scenery_level": 4}),
    ]
    for u, v, data in edges:
        G.add_edge(u, v, **data)
        G.add_edge(v, u, **data)
    return G


@pytest.fixture
def mock_graph():
    return _build_mock_graph()


class TestNormalizeWeights:
    def test_resolve_weights_default(self):
        result = resolve_weights(None)
        assert result == {"distance": 0.5, "slope": 0.2, "scenery": 0.3}
        assert abs(sum(result.values()) - 1.0) < 1e-9

    @pytest.mark.parametrize("raw,expected_min", [
        ({"distance": 0.001, "slope": 0.001, "scenery": 0.001}, 0.05),
        ({"distance": 0.99, "slope": 0.5, "scenery": 0.5}, 0.8),
    ])
    def test_resolve_weights_bounds(self, raw, expected_min):
        result = resolve_weights(raw)
        for v in result.values():
            assert v >= 0.05 - 1e-9
            assert v <= 0.8 + 1e-9
        assert abs(sum(result.values()) - 1.0) < 1e-9

    def test_resolve_weights_boundary_normalized(self):
        raw = {"distance": 0.8, "slope": 0.8, "scenery": 0.8}
        result = resolve_weights(raw)
        for k in ("distance", "slope", "scenery"):
            assert abs(result[k] - 1 / 3) < 1e-9
        assert abs(sum(result.values()) - 1.0) < 1e-9


class TestThreeFactorNormalization:
    def test_normalize_lengths(self, mock_graph):
        max_len, norm_map = _normalize_lengths(mock_graph)
        assert max_len == 400.0
        assert len(norm_map) > 0
        for (u, v, k), val in norm_map.items():
            assert 0.0 <= val <= 1.0

    def test_compute_edge_cost_three_components(self, mock_graph):
        weights = {"distance": 0.5, "slope": 0.2, "scenery": 0.3}
        data = {"length": 200.0, "slope_level": 2, "scenery_level": 4}
        norm_length = 200.0 / 400.0
        cost, degraded = _compute_edge_cost(data, norm_length, weights)
        expected = 0.5 * 0.5 + 0.2 * (2 / 5) + 0.3 * (1 - 4 / 5)
        assert abs(cost - expected) < 1e-9
        assert degraded is False


class TestFilterConstraints:
    def test_filter_slope_avoid_removes_level5(self, mock_graph):
        G_filtered, status, penalty = _filter_by_constraints(
            mock_graph, {"slope": "avoid"}
        )
        assert status in ("filtered", "degraded_slope")
        for u, v, k, data in G_filtered.edges(keys=True, data=True):
            if data.get("slope_level") == 5 and status == "filtered":
                pytest.fail("slope_level=5 路段未被过滤")

    def test_filter_no_filter_when_slope_normal(self, mock_graph):
        G_filtered, status, penalty = _filter_by_constraints(
            mock_graph, {"slope": "normal"}
        )
        assert status == "no_filter"
        assert penalty == {}


class TestDegradeMechanism:
    def test_degraded_flag_on_missing_attrs(self):
        G = nx.MultiDiGraph()
        G.add_edge(0, 1, length=100.0)
        weights = {"distance": 0.5, "slope": 0.2, "scenery": 0.3}
        data = {"length": 100.0}
        cost, degraded = _compute_edge_cost(data, 0.1, weights)
        assert degraded is True
        assert cost > 0


class TestComputeRouteErrors:
    def test_no_path_raises_value_error(self):
        G = nx.MultiDiGraph()
        G.add_node(0)
        G.add_node(1)
        G.add_edge(0, 0, length=10.0)
        with pytest.raises(ValueError, match="不可达"):
            compute_route(G, 0, 1)

    def test_compute_route_success(self, mock_graph):
        result = compute_route(mock_graph, 0, 5)
        assert "recommended" in result
        assert "shortest" in result
        assert result["recommended"][0] == 0
        assert result["recommended"][-1] == 5
        assert result["recommended_length_m"] > 0
        assert result["shortest_length_m"] > 0
        assert "applied_weights" in result
        assert 0.0 <= result["overlap_rate"] <= 1.0


# ---------------------------------------------------------------------------
# 出行方式（walk / bike / drive）测试
# ---------------------------------------------------------------------------

from spatial.routing import (  # noqa: E402
    DEFAULT_WEIGHTS,
    TRAVEL_MODES,
    MODE_SPEEDS_KMH,
    MODE_DEFAULT_WEIGHTS,
    MODE_OUTSIDE_ROAD_PENALTY,
    normalize_mode,
    _edge_highway_tags,
    filter_graph_for_mode,
    estimate_duration_min,
    compute_route_with_annotations,
)


def _build_mode_mock_graph():
    """含 footway/steps/service/residential/corridor/path/混合标签 的小图（双向边）。"""
    G = nx.MultiDiGraph()
    G.add_nodes_from(range(8))
    edges = [
        # (u, v, highway, length, slope_level)
        (0, 1, "footway", 100.0, 2),
        (1, 2, "steps", 100.0, 3),                     # 纯台阶：bike/drive 均移除
        (2, 3, "['steps', 'footway']", 100.0, 3),      # 混合：bike 保留、drive 移除
        (3, 4, "service", 100.0, 2),
        (4, 5, "residential", 100.0, 5),               # slope5：bike penalty 3.0
        (5, 6, "corridor", 100.0, 3),                  # 纯走廊：drive 移除
        (6, 7, "path", 100.0, 3),                      # 步行小径：drive 移除
        (0, 7, "['service', 'footway']", 500.0, 2),   # 混合：drive 保留
        (1, 6, "primary", 600.0, 2),
        (2, 6, "service", 250.0, 4),                   # slope4：bike penalty 1.5
    ]
    for u, v, highway, length, slope in edges:
        data = {
            "length": length,
            "slope_level": slope,
            "scenery_level": 3,
            "highway": highway,
        }
        G.add_edge(u, v, **data)
        G.add_edge(v, u, **data)
    return G


@pytest.fixture
def mode_graph():
    return _build_mode_mock_graph()


class TestTravelModeConstants:
    def test_travel_modes_tuple(self):
        assert TRAVEL_MODES == ("walk", "bike", "drive")

    def test_walk_default_weights_unchanged(self):
        assert MODE_DEFAULT_WEIGHTS["walk"] == DEFAULT_WEIGHTS
        assert MODE_DEFAULT_WEIGHTS["walk"] == {
            "distance": 0.5, "slope": 0.2, "scenery": 0.3
        }

    def test_walk_outside_penalty_unchanged(self):
        assert MODE_OUTSIDE_ROAD_PENALTY["walk"] == 10.0

    def test_mode_speeds(self):
        assert MODE_SPEEDS_KMH == {"walk": 4.5, "bike": 14.0, "drive": 25.0}


class TestNormalizeMode:
    @pytest.mark.parametrize("raw,expected", [
        ("walk", "walk"),
        ("bike", "bike"),
        ("drive", "drive"),
        (None, "walk"),
        ("", "walk"),
        ("fly", "walk"),
        ("WALK", "walk"),
        (123, "walk"),
    ])
    def test_normalize_mode(self, raw, expected):
        assert normalize_mode(raw) == expected


class TestEstimateDuration:
    def test_durations_by_mode(self):
        # 1000m / (14km/h) = 4.286min → 4.3
        assert estimate_duration_min(1000.0, "bike") == 4.3
        # 1000m / (4.5km/h) = 13.333min → 13.3
        assert estimate_duration_min(1000.0, "walk") == 13.3
        # 1000m / (25km/h) = 2.4min
        assert estimate_duration_min(1000.0, "drive") == 2.4

    def test_zero_and_none_length(self):
        assert estimate_duration_min(0, "bike") == 0.0
        assert estimate_duration_min(None, "drive") == 0.0

    def test_invalid_mode_falls_back_to_walk(self):
        # 4500m / 4.5km/h = 60min
        assert estimate_duration_min(4500.0, None) == 60.0


class TestEdgeHighwayTags:
    def test_parse_various_forms(self):
        assert _edge_highway_tags({"highway": "steps"}) == ["steps"]
        assert _edge_highway_tags({"highway": "['steps', 'footway']"}) == ["steps", "footway"]
        assert _edge_highway_tags({"highway": ["service", "footway"]}) == ["service", "footway"]
        assert _edge_highway_tags({}) == []


class TestFilterGraphForMode:
    def test_walk_no_filter_returns_original(self, mode_graph):
        original_edges = mode_graph.number_of_edges()
        Gf, status, penalty = filter_graph_for_mode(mode_graph, "walk")
        assert Gf is mode_graph
        assert status == "no_filter"
        assert penalty == {}
        assert Gf.number_of_edges() == original_edges

    def test_bike_removes_pure_steps_keeps_mixed(self, mode_graph):
        Gf, status, penalty = filter_graph_for_mode(mode_graph, "bike")
        assert status == "mode_bike"

        # 纯台阶边双向移除
        assert not Gf.has_edge(1, 2)
        assert not Gf.has_edge(2, 1)
        # 含 footway 的混合台阶边保留
        assert Gf.has_edge(2, 3)
        assert Gf.has_edge(3, 2)
        # 普通边保留
        assert Gf.has_edge(0, 1)   # footway
        assert Gf.has_edge(3, 4)   # service

        # 陡坡软惩罚（双向）
        assert penalty[(4, 5, 0)] == 3.0
        assert penalty[(5, 4, 0)] == 3.0
        assert penalty[(2, 6, 0)] == 1.5
        assert penalty[(6, 2, 0)] == 1.5

        # 原图不被修改
        assert mode_graph.has_edge(1, 2)

    def test_drive_keeps_only_road_edges(self, mode_graph):
        Gf, status, penalty = filter_graph_for_mode(mode_graph, "drive")
        assert status == "mode_drive"
        assert penalty == {}

        # 保留：service / residential / primary / ['service','footway']
        assert Gf.has_edge(3, 4)
        assert Gf.has_edge(4, 5)
        assert Gf.has_edge(1, 6)
        assert Gf.has_edge(0, 7)
        # 移除：footway / 纯steps / ['steps','footway'] / corridor / path
        assert not Gf.has_edge(0, 1)
        assert not Gf.has_edge(1, 2)
        assert not Gf.has_edge(2, 3)
        assert not Gf.has_edge(5, 6)
        assert not Gf.has_edge(6, 7)

        # 原图不被修改
        assert mode_graph.has_edge(0, 1)
        assert mode_graph.has_edge(5, 6)


class TestComputeRouteModes:
    _EXISTING_KEYS = (
        "recommended", "shortest", "filter_status", "overlap_rate",
        "degraded", "degraded_count", "recommended_length_m",
        "shortest_length_m", "applied_weights", "length_capped",
        "max_len", "_annotation_degraded",
    )

    def test_bike_route(self, mode_graph):
        result = compute_route(mode_graph, 0, 7, mode="bike")
        assert result["mode"] == "bike"
        assert result["speed_kmh"] == 14.0
        assert result["duration_min"] > 0
        assert result["shortest_duration_min"] > 0
        for key in self._EXISTING_KEYS:
            assert key in result
        assert result["filter_status"].startswith("mode_bike")
        # 推荐路径不得经过纯台阶边 (1,2) / (2,1)
        path = result["recommended"]
        for i in range(len(path) - 1):
            assert (path[i], path[i + 1]) not in ((1, 2), (2, 1))

    def test_drive_route(self, mode_graph):
        result = compute_route(mode_graph, 0, 7, mode="drive")
        assert result["mode"] == "drive"
        assert result["speed_kmh"] == 25.0
        assert result["duration_min"] > 0
        assert result["shortest_duration_min"] > 0
        assert result["filter_status"].startswith("mode_drive")

    def test_drive_no_path_steps_only_raises(self):
        G = nx.MultiDiGraph()
        G.add_nodes_from(range(4))
        # 0-1 仅台阶连通（驾车不可达）
        for u, v in ((0, 1), (1, 0)):
            G.add_edge(
                u, v, length=100.0, slope_level=3, scenery_level=3,
                highway="steps",
            )
        # 另有车行道边，保证方式过滤后图非空（不触发全局 _degraded 回退）
        for u, v in ((2, 3), (3, 2)):
            G.add_edge(
                u, v, length=100.0, slope_level=2, scenery_level=3,
                highway="service",
            )
        with pytest.raises(ValueError, match="驾车"):
            compute_route(G, 0, 1, mode="drive")

    def test_walk_default_unchanged(self, mode_graph):
        result = compute_route(mode_graph, 0, 7)
        assert result["mode"] == "walk"
        assert result["speed_kmh"] == 4.5
        # walk 不过滤，filter_status 不以 mode_ 开头
        assert not result["filter_status"].startswith("mode_")
        assert result["applied_weights"] == DEFAULT_WEIGHTS

    def test_annotations_passthrough_mode(self, mode_graph):
        result = compute_route_with_annotations(mode_graph, 0, 7, mode="bike")
        assert result["mode"] == "bike"
        assert result["speed_kmh"] == 14.0
