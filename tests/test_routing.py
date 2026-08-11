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
        for (u, v), val in norm_map.items():
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
