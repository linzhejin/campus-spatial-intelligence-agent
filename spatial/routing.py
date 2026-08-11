"""
多因素路径计算模块

基于硬约束过滤 + 软成本优化的两步路径计算算法（DEC-011）。

核心算法：
  1. 硬约束过滤：根据 constraints 过滤不可通行路段
  2. 软成本优化：Cost = w_d × D + w_s × S + w_v × (1 − V)
  3. 路径长度上限：recommended ≤ shortest × 3 或 ≤ 2000m
  4. 降级策略：约束过严时自动放宽

数据源：
  - G: OSMnx 路网（含 length 属性）
  - 路段坡度/景观标注：通过 edge 属性 slope_level / scenery_level
"""

import logging
import os
from typing import Optional

import networkx as nx

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS = {"distance": 0.5, "slope": 0.2, "scenery": 0.3}
WEIGHT_BOUNDS = {"min": 0.05, "max": 0.8}

_PATH_LENGTH_CAP_MULTIPLIER = 3.0
_PATH_LENGTH_CAP_MAX = 2000.0

_EDGE_ATTR_DEFAULTS = {
    "slope_level": 3,
    "scenery_level": 3,
}


def resolve_weights(llm_weights: Optional[dict]) -> dict:
    """
    解析 LLM 输出的 weights，校验后返回最终权重。

    - 入参为 None 时返回 DEFAULT_WEIGHTS
    - 每个权重强制限制在 [0.05, 0.8]
    - 归一化使总和 = 1

    Args:
        llm_weights: LLM 输出的 weights dict，可为 None

    Returns:
        校验并归一化后的权重 dict {"distance": float, "slope": float, "scenery": float}
    """
    if llm_weights is None:
        return dict(DEFAULT_WEIGHTS)

    bounds_min = WEIGHT_BOUNDS["min"]
    bounds_max = WEIGHT_BOUNDS["max"]

    w = {}
    for key in ("distance", "slope", "scenery"):
        raw = float(llm_weights.get(key, DEFAULT_WEIGHTS[key]))
        w[key] = max(bounds_min, min(bounds_max, raw))

    total = sum(w.values())
    if total == 0:
        return dict(DEFAULT_WEIGHTS)

    return {k: v / total for k, v in w.items()}


def _normalize_lengths(G: nx.MultiDiGraph) -> tuple:
    """
    计算所有边的归一化长度。

    Returns:
        (max_len, norm_map) 元组：
        - max_len: 全图最长边长度（米）
        - norm_map: {(u, v): norm_length} 归一化长度映射
    """
    max_len = 0.0
    for u, v, data in G.edges(data=True):
        length = data.get("length", 0)
        if length > max_len:
            max_len = length

    if max_len == 0:
        return 0.0, {}

    norm_map = {(u, v): data.get("length", 0) / max_len
                for u, v, data in G.edges(data=True)}
    return max_len, norm_map


def _get_edge_attr(data: dict, attr: str) -> tuple:
    """
    获取边属性值，返回 (值, 是否为降级默认值)。

    Args:
        data: edge data dict
        attr: 属性名

    Returns:
        (value, is_default) 元组
    """
    if attr in data and data[attr] is not None:
        return float(data[attr]), False
    return float(_EDGE_ATTR_DEFAULTS.get(attr, 3)), True


def _compute_edge_cost(
    data: dict,
    norm_length: float,
    weights: dict,
) -> tuple:
    """
    计算单条边的多因素成本。

    Cost = w_d × D + w_s × S + w_v × (1 − V)

    Args:
        data: edge data dict
        norm_length: 归一化距离（0-1）
        weights: 权重 dict

    Returns:
        (cost, is_degraded) 元组
    """
    slope, slope_degraded = _get_edge_attr(data, "slope_level")
    scenery, scenery_degraded = _get_edge_attr(data, "scenery_level")

    is_degraded = slope_degraded or scenery_degraded

    d_component = weights["distance"] * norm_length
    s_component = weights["slope"] * (slope / 5.0)
    v_component = weights["scenery"] * (1.0 - scenery / 5.0)

    return d_component + s_component + v_component, is_degraded


def _filter_by_constraints(G: nx.MultiDiGraph, constraints: dict) -> tuple:
    """
    基于硬约束过滤不可通行路段。

    DEC-011 规则：
      - slope=avoid: 过滤 slope_level=5 的路段
                     slope_level=4 的路段加 2× 距离惩罚
                     兜底：若无可行路径，放宽 slope_level=4 可通行（3× 惩罚）
      - 其他约束等级（normal/any）：不过滤

    Args:
        G: 路网图
        constraints: 约束 dict，如 {"slope": "avoid"}

    Returns:
        (G_filtered, filter_status, penalty_map) 元组
        - G_filtered: 过滤后的图（可能是原图副本）
        - filter_status: 状态标记 ("filtered" | "degraded_slope" | "no_filter")
        - penalty_map: {(u, v, k): penalty_multiplier} 用于成本调整
    """
    slope_constraint = constraints.get("slope", "normal")

    if slope_constraint not in ("avoid",):
        return G, "no_filter", {}

    edges_to_remove = []
    penalty_map = {}

    for u, v, k, data in G.edges(keys=True, data=True):
        slope_level = data.get("slope_level")

        if slope_level == 5:
            edges_to_remove.append((u, v, k))
        elif slope_level == 4:
            penalty_map[(u, v)] = 2.0

    if not edges_to_remove and not penalty_map:
        return G, "no_filter", {}

    G_filtered = G.copy()
    G_filtered.remove_edges_from(edges_to_remove)

    if nx.is_empty(G_filtered):
        G_filtered = G.copy()
        penalty_map = {}
        for u, v, data in G.edges(data=True):
            if data.get("slope_level") == 5:
                penalty_map[(u, v)] = 3.0
        logger.info("硬约束过滤后无可行路径，降级为 slope_level=4+5 均可通行")
        return G_filtered, "degraded_slope", penalty_map

    if not nx.is_connected(G_filtered.to_undirected()):
        logger.info("硬约束导致图不连通，降级为原图")
        G_filtered = G.copy()
        penalty_map = {}
        return G_filtered, "degraded_slope", penalty_map

    return G_filtered, "filtered", penalty_map


def _should_degrade_annotations() -> Optional[str]:
    try:
        from spatial.network import get_annotation_coverage_rate
        rate = get_annotation_coverage_rate()
    except Exception:
        rate = 0.0

    if rate <= 0.0:
        return "no_annotations"
    if rate < 0.8:
        return "degraded_annotations"
    return None


def _edge_cost_factory(
    G: nx.MultiDiGraph,
    norm_lengths: dict,
    weights: dict,
    penalty_map: dict,
    annotation_degraded_tag: Optional[str] = None,
):
    """
    创建边权函数（用于 Dijkstra 路径计算）。

    返回的函数签名: edge_weight(u, v, data) -> float
    """
    def edge_weight(u, v, data):
        key = (u, v)
        raw_length = data.get("length", 0)
        norm = norm_lengths.get(key, raw_length / 1000.0)

        if annotation_degraded_tag is not None:
            cost = weights["distance"] * norm
        else:
            cost, _ = _compute_edge_cost(data, norm, weights)

        edge_key = (u, v)
        penalty = penalty_map.get(edge_key, 1.0)
        cost *= penalty

        return cost

    return edge_weight


def _compute_overlap(route_a: list, route_b: list) -> float:
    """
    计算两条路径的节点重叠率。

    overlap_rate = 共同节点数 / max(len(route_a), len(route_b))

    Args:
        route_a: 节点路径 [node1, node2, ...]
        route_b: 节点路径 [node1, node2, ...]

    Returns:
        重叠率 0.0-1.0
    """
    if not route_a or not route_b:
        return 0.0

    set_a = set(route_a)
    set_b = set(route_b)
    overlap = len(set_a & set_b)
    max_len = max(len(route_a), len(route_b))

    return overlap / max_len if max_len > 0 else 0.0


def _path_length(G: nx.MultiDiGraph, path: list) -> float:
    """
    计算路径总长度（米）。

    Args:
        G: 路网图
        path: 节点路径

    Returns:
        路径总长度（米）
    """
    total = 0.0
    for i in range(len(path) - 1):
        u, v = path[i], path[i + 1]
        edge_data = G.get_edge_data(u, v)
        if edge_data:
            min_len = min(d.get("length", 0) for d in edge_data.values())
            total += min_len
    return total


def compute_route(
    G: nx.MultiDiGraph,
    start_node: int,
    end_node: int,
    constraints: Optional[dict] = None,
    weights: Optional[dict] = None,
) -> dict:
    """
    多因素路径计算主函数。

    流程：
      1. 解析权重（默认 → 校验 → 归一化）
      2. 硬约束过滤（过滤不可通行路段）
      3. 软成本优化（Dijkstra 计算推荐路线）
      4. 计算最短路径基线
      5. 路径长度上限裁剪
      6. 计算重叠率

    Args:
        G: OSMnx MultiDiGraph 路网
        start_node: 起点节点 ID
        end_node: 终点节点 ID
        constraints: 约束 dict，如 {"slope": "avoid"}
        weights: 权重 dict，如 {"distance": 0.2, "slope": 0.5, "scenery": 0.3}

    Returns:
        {
            "recommended": [node_id, ...], 推荐路径
            "shortest": [node_id, ...], 最短路径
            "filter_status": str, 过滤状态
            "overlap_rate": float, 重叠率
            "degraded": bool, 是否降级
            "degraded_count": int, 降级边数
            "recommended_length_m": float, 推荐路径长度
            "shortest_length_m": float, 最短路径长度
            "applied_weights": dict, 实际使用的权重
            "length_capped": bool, 是否触发了长度上限
        }

    Raises:
        ValueError: 起终点不可达时
    """
    constraints = constraints or {}
    resolved_weights = resolve_weights(weights)

    max_len, norm_lengths = _normalize_lengths(G)

    annotation_degraded = _should_degrade_annotations()

    G_filtered, filter_status, penalty_map = _filter_by_constraints(G, constraints)

    if annotation_degraded is not None:
        if filter_status == "no_filter":
            filter_status = annotation_degraded
        else:
            filter_status = f"{filter_status}+{annotation_degraded}"

    edge_weight = _edge_cost_factory(
        G_filtered, norm_lengths, resolved_weights, penalty_map, annotation_degraded
    )

    try:
        recommended = nx.dijkstra_path(
            G_filtered, start_node, end_node, weight=edge_weight
        )
    except nx.NetworkXNoPath:
        raise ValueError(
            f"起点 {start_node} 到终点 {end_node} 不可达。"
            f"可能原因：硬约束过严导致无可行路径，或路网数据不足。"
        )

    try:
        shortest = nx.dijkstra_path(G, start_node, end_node, weight="length")
    except nx.NetworkXNoPath:
        shortest = recommended

    recommended_len = _path_length(G, recommended)
    shortest_len = _path_length(G, shortest)

    cap_multplier = _PATH_LENGTH_CAP_MULTIPLIER
    cap_max = _PATH_LENGTH_CAP_MAX
    length_capped = False

    if shortest_len > 0 and recommended_len > shortest_len * cap_multplier:
        logger.info(
            "推荐路径 %.0fm 超过最短路径 %.0fm 的 %.0f 倍，裁剪为最短路径",
            recommended_len, shortest_len, cap_multplier
        )
        recommended = shortest
        recommended_len = shortest_len
        length_capped = True
    elif recommended_len > cap_max:
        logger.info(
            "推荐路径 %.0fm 超过上限 %.0fm，裁剪为最短路径",
            recommended_len, cap_max
        )
        recommended = shortest
        recommended_len = shortest_len
        length_capped = True

    overlap_rate = _compute_overlap(recommended, shortest)

    # 从实际结果路径计算 degraded_count（避免闭包中 Dijkstra 重复评估边导致计数虚高）
    degraded_count = 0
    for i in range(len(recommended) - 1):
        u, v = recommended[i], recommended[i + 1]
        edge_data = G.get_edge_data(u, v)
        if edge_data:
            data = min(edge_data.values(), key=lambda d: d.get("length", float("inf")))
            _, is_d = _compute_edge_cost(data, norm_lengths.get((u, v), 0.001), resolved_weights)
            if is_d:
                degraded_count += 1
    is_degraded = degraded_count > 0

    return {
        "recommended": recommended,
        "shortest": shortest,
        "filter_status": filter_status,
        "overlap_rate": round(overlap_rate, 4),
        "degraded": is_degraded,
        "degraded_count": degraded_count,
        "recommended_length_m": round(recommended_len, 1),
        "shortest_length_m": round(shortest_len, 1),
        "applied_weights": resolved_weights,
        "length_capped": length_capped,
        "max_len": max_len,
        "_annotation_degraded": annotation_degraded is not None,
    }


def compute_route_with_annotations(
    G: nx.MultiDiGraph,
    start_node: int,
    end_node: int,
    constraints: Optional[dict] = None,
    weights: Optional[dict] = None,
    annotations: Optional[list] = None,
) -> dict:
    """
    带路段标注的路径计算（slope_level / scenery_level 注入路网边属性）。

    Args:
        G: OSMnx MultiDiGraph 路网
        start_node: 起点节点 ID
        end_node: 终点节点 ID
        constraints: 约束 dict
        weights: 权重 dict
        annotations: 标注列表，每项含 edge_id 列表和属性值

    Returns:
        同 compute_route 返回结构
    """
    G_annotated = G.copy()

    if annotations:
        for ann in annotations:
            edge_ids = ann.get("edge_id", [])
            slope = ann.get("slope_level")
            scenery = ann.get("scenery_level")
            name = ann.get("name", "")

            for eid in edge_ids:
                for u, v, k, data in G_annotated.edges(keys=True, data=True):
                    if k == eid:
                        if slope is not None:
                            data["slope_level"] = slope
                        if scenery is not None:
                            data["scenery_level"] = scenery
                        if name:
                            data["name"] = name

    return compute_route(G_annotated, start_node, end_node, constraints, weights)