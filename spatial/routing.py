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

import ast
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

# ====== 校园边界多边形（精确剔除"真校外"路段） ======
# 仅两端都落在校园多边形外的边才算"真校外"并删除；
# 一端在内一端在外的跨边界边（如东湖南路沿湖段、珞瑜路校门段）保留以维持学部连通。
_campus_polygon = None
_outside_edge_cache = {}  # key: id(G) → set of (u,v,k)

def get_campus_polygon():
    """懒加载校园边界多边形（WGS-84，不加缓冲）。"""
    global _campus_polygon
    if _campus_polygon is None:
        try:
            from config import CAMPUS_POLYS_GCJ
            from spatial.coord_transform import gcj02_to_wgs84
            from shapely.geometry import Polygon
            from shapely.ops import unary_union
            polys = [Polygon([gcj02_to_wgs84(lng, lat) for lng, lat in poly])
                     for poly in CAMPUS_POLYS_GCJ.values()]
            _campus_polygon = unary_union(polys)
        except Exception as e:
            logger.warning("校园多边形加载失败，校外边检测降级为路名匹配: %s", e)
            _campus_polygon = False
    return _campus_polygon


def _get_outside_edges(G: nx.MultiDiGraph) -> set:
    """
    返回"两端均在校外"的边 key 集合。
    用校园多边形精确判定（非路名），避免误删穿越校园的市政路段。
    结果按 id(G) 缓存，路网不变时只算一次。
    """
    poly = get_campus_polygon()
    if not poly:
        # 多边形不可用 → 退路名匹配（保守）
        return set()
    cache_key = id(G)
    cached = _outside_edge_cache.get(cache_key)
    if cached is not None:
        return cached
    from shapely.geometry import Point
    outside = set()
    for u, v, k, d in G.edges(keys=True, data=True):
        ud = G.nodes[u]
        vd = G.nodes[v]
        pu = Point(float(ud.get("x", 0)), float(ud.get("y", 0)))
        pv = Point(float(vd.get("x", 0)), float(vd.get("y", 0)))
        if not poly.covers(pu) and not poly.covers(pv):
            outside.add((u, v, k))
    _outside_edge_cache[cache_key] = outside
    return outside


# 校外市政道路（武大校园周边的马路）：推荐路径应尽量避开，
# 只在起终点本身就在这些路边时（凌波门/牌坊/珞瑜门等）才必要地经过。
# 这些路的 scenery 标注可能很高（如东湖南路沿湖），若不惩罚会导致推荐路线绕出校园。
# 与 scripts/clip_to_campus.py 的 OUTSIDE_ROAD_NAMES 保持同步。
_OUTSIDE_ROAD_NAMES = {
    "八一路", "东湖南路", "卓刀泉北路", "卓刀泉南路", "卓刀泉路",
    "广八路", "茶港路", "广卓路", "珞狮路", "珞狮北路", "珞狮路辅路",
    "珞瑜路", "珞喻路", "珞喻路辅路", "珞瑜路辅路",
    "武珞路", "武珞路辅路", "群光南路", "洪福巷",
    "武工路", "机电路", "科技小路", "明志路", "汇志大道", "神龙园路",
}
_OUTSIDE_ROAD_PENALTY = 10.0  # 校外道路的距离成本放大倍数（从3.0调至10.0，强避免穿城）


def _road_name_list(raw) -> list:
    """路名可能是 str / list / 被str()序列化的list（"['珞喻路辅路', ...]"），统一拆成字符串列表。"""
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw]
    s = str(raw)
    if s.startswith("["):
        try:
            parsed = ast.literal_eval(s)
            if isinstance(parsed, (list, tuple)):
                return [str(x) for x in parsed]
        except (ValueError, SyntaxError):
            pass
    return [s]


def _is_outside_road(raw_name) -> bool:
    for n in _road_name_list(raw_name):
        if any(bad in n for bad in _OUTSIDE_ROAD_NAMES):
            return True
    return False


# ---------------------------------------------------------------------------
# 出行方式（walk 步行 / bike 骑行 / drive 驾车）
# ---------------------------------------------------------------------------

TRAVEL_MODES = ("walk", "bike", "drive")

MODE_SPEEDS_KMH = {"walk": 4.5, "bike": 14.0, "drive": 25.0}

# 各模式默认多因素权重（walk 与 DEFAULT_WEIGHTS 完全一致，保持步行行为不变）
MODE_DEFAULT_WEIGHTS = {
    "walk": {"distance": 0.5, "slope": 0.2, "scenery": 0.3},
    "bike": {"distance": 0.35, "slope": 0.45, "scenery": 0.2},
    "drive": {"distance": 0.8, "slope": 0.05, "scenery": 0.15},
}

# 校外市政道路惩罚倍数：步行强避免（10×，与 _OUTSIDE_ROAD_PENALTY 保持一致）；
# 骑行/驾车本身就常走市政路，惩罚逐档降低。
MODE_OUTSIDE_ROAD_PENALTY = {"walk": _OUTSIDE_ROAD_PENALTY, "bike": 3.0, "drive": 1.2}

# 骑行不可通行：纯台阶/垂直交通标签（边只要还含 footway/path 等可骑行标签即保留）
_BIKE_BLOCKED_HIGHWAY = {"steps", "elevator", "escalator"}

# 驾车白名单：边的 highway 标签中任意一个命中即视为车行道
_DRIVE_ALLOWED_HIGHWAY = {
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential",
    "service", "living_street", "road",
}


def normalize_mode(mode) -> str:
    """归一化出行方式：None / 非法值统一回退 "walk"，合法值原样返回。"""
    if isinstance(mode, str) and mode in TRAVEL_MODES:
        return mode
    return "walk"


def _edge_highway_tags(edge_data: dict) -> list:
    """解析边的 highway 属性（str / list / 被 str() 序列化的 list），返回标签字符串列表。"""
    if not edge_data:
        return []
    return _road_name_list(edge_data.get("highway"))


def _merge_penalty_maps(*maps: dict) -> dict:
    """合并多个 penalty_map：同一 (u, v, k) 上的惩罚倍数相乘。"""
    merged = {}
    for m in maps:
        if not m:
            continue
        for key, multiplier in m.items():
            if key in merged:
                merged[key] *= multiplier
            else:
                merged[key] = multiplier
    return merged


def filter_graph_for_mode(G: nx.MultiDiGraph, mode) -> tuple:
    """
    按出行方式过滤路网。

    第一步（所有模式）：剔除"真校外"边——两端均在校园多边形外的边。
        跨边界边（一端在内）保留以维持学部连通。
    第二步（按模式）：
    - walk:  仅剔校外边后返回
    - bike:  再移除纯台阶/垂直交通边；陡坡软惩罚
    - drive: 仅保留车行道边

    过滤后图为空时回退，status 追加 "_degraded"。

    Returns:
        (G_filtered, status_str, penalty_map)
    """
    mode = normalize_mode(mode)

    # —— 第一步：所有模式都剔除真校外边 ——
    outside_edges = _get_outside_edges(G)
    if outside_edges:
        G_mode = G.copy()
        G_mode.remove_edges_from(list(outside_edges))
        isolated = [n for n, deg in G_mode.degree() if deg == 0]
        if isolated:
            G_mode.remove_nodes_from(isolated)
    else:
        G_mode = G
    outside_status = "no_outside" if outside_edges else "no_filter"

    if mode == "walk":
        return G_mode, outside_status, {}

    # bike/drive：在已剔校外边的图副本上继续删边（绝不能改原图）
    G_mode = G_mode.copy()
    edges_to_remove = []

    if mode == "bike":
        for u, v, k, data in list(G_mode.edges(keys=True, data=True)):
            tags = _edge_highway_tags(data)
            if tags and all(tag in _BIKE_BLOCKED_HIGHWAY for tag in tags):
                edges_to_remove.append((u, v, k))
        base_status = "mode_bike"
    else:  # drive
        for u, v, k, data in list(G_mode.edges(keys=True, data=True)):
            tags = _edge_highway_tags(data)
            if not any(tag in _DRIVE_ALLOWED_HIGHWAY for tag in tags):
                edges_to_remove.append((u, v, k))
        base_status = "mode_drive"

    G_mode.remove_edges_from(edges_to_remove)

    if G_mode.number_of_edges() == 0:
        # 极端情况：方式过滤删掉了所有边（数据异常），回退原图保证可达
        logger.warning(
            "出行方式 %s 过滤后图为空（移除 %d 条边），回退原图",
            mode, len(edges_to_remove),
        )
        G_mode = G
        status = f"{base_status}_degraded"
    else:
        isolated = [n for n, deg in G_mode.degree() if deg == 0]
        if isolated:
            G_mode.remove_nodes_from(isolated)
            logger.info("出行方式 %s：移除 %d 条不可通行边、%d 个孤立节点",
                        mode, len(edges_to_remove), len(isolated))
        status = base_status

    penalty_map = {}
    if mode == "bike":
        for u, v, k, data in G_mode.edges(keys=True, data=True):
            lvl = data.get("slope_level")
            if lvl == 5:
                penalty_map[(u, v, k)] = 3.0
            elif lvl == 4:
                penalty_map[(u, v, k)] = 1.5

    return G_mode, status, penalty_map


def estimate_duration_min(length_m, mode) -> float:
    """
    按模式平均速度估算通行时长（分钟），round 到 1 位小数。

    length 为 0 / None 时返回 0.0。
    """
    if not length_m:
        return 0.0
    mode = normalize_mode(mode)
    meters_per_min = MODE_SPEEDS_KMH[mode] * 1000.0 / 60.0
    return round(length_m / meters_per_min, 1)


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

    norm_map = {}
    for u, v, k, data in G.edges(keys=True, data=True):
        norm_map[(u, v, k)] = data.get("length", 0) / max_len
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
            penalty_map[(u, v, k)] = 2.0

    if not edges_to_remove and not penalty_map:
        return G, "no_filter", {}

    G_filtered = G.copy()
    G_filtered.remove_edges_from(edges_to_remove)

    if nx.is_empty(G_filtered):
        G_filtered = G.copy()
        penalty_map = {}
        for u, v, k, data in G.edges(keys=True, data=True):
            if data.get("slope_level") == 5:
                penalty_map[(u, v, k)] = 3.0
        logger.info("硬约束过滤后无可行路径，降级为 slope_level=4+5 均可通行")
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
    outside_road_penalty: float = _OUTSIDE_ROAD_PENALTY,
):
    """
    创建边权函数（用于 Dijkstra 路径计算）。

    Args:
        outside_road_penalty: 校外市政道路成本放大倍数（按出行模式取，
            walk=10.0 与历史行为一致）。

    返回的函数签名: edge_weight(u, v, data) -> float
    """
    def edge_weight(u, v, data):
        # networkx 3.x 对 MultiDiGraph 传给 weight 函数的 data 是 {edge_key: edge_attr_dict}，
        # 必须先取出真正的边属性 dict，否则 name/slope_level/scenery_level/length 都读不到
        if isinstance(data, dict) and data:
            edge_k = next(iter(data))
            edge_data = data[edge_k]
        else:
            edge_k = 0
            edge_data = data or {}

        key = (u, v, edge_k)
        raw_length = edge_data.get("length", 0)
        max_len_for_fallback = max(norm_lengths.values()) if norm_lengths else 1000.0
        fallback_norm = raw_length / max_len_for_fallback if max_len_for_fallback > 0 else 0.0
        norm = norm_lengths.get(key, fallback_norm)

        if annotation_degraded_tag is not None:
            cost = weights["distance"] * norm
        else:
            cost, _ = _compute_edge_cost(edge_data, norm, weights)

        # 校外道路惩罚：尽量避免推荐路线绕出校园（东湖南路/八一路等市政路）
        if _is_outside_road(edge_data.get("name")):
            cost *= outside_road_penalty

        penalty = penalty_map.get(key, 1.0)
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


def _weighted_avg_attr(G: nx.MultiDiGraph, path: list, attr: str) -> float:
    """计算路径的长度加权平均属性值（如坡度/景观等级）。

    用于实验评估：Σ(attr(e) × L(e)) / Σ L(e)
    """
    total_weighted = 0.0
    total_length = 0.0
    for i in range(len(path) - 1):
        u, v = path[i], path[i + 1]
        edge_data = G.get_edge_data(u, v)
        if not edge_data:
            continue
        data = min(edge_data.values(), key=lambda d: d.get("length", float("inf")))
        length = data.get("length", 0)
        attr_val = data.get(attr, 3)  # 默认值 3（中等）
        total_weighted += float(attr_val) * length
        total_length += length
    return total_weighted / total_length if total_length > 0 else 0.0


def _raise_no_path(G, start_node, end_node, filter_status, G_filtered, mode="walk"):
    """不可达诊断：记录日志并抛出带友好提示的 ValueError。"""
    mode = normalize_mode(mode)
    from spatial.network import get_node_coords
    try:
        sn_coords = get_node_coords(G, start_node)
        en_coords = get_node_coords(G, end_node)
    except Exception:
        sn_coords, en_coords = None, None
    logger.warning(
        "路径不可达: start=%s (%s) end=%s (%s) mode=%s filter=%s edges=%d/%d",
        start_node, sn_coords, end_node, en_coords, mode,
        filter_status, G_filtered.number_of_edges(), G.number_of_edges(),
    )
    # 路况导致不可达时，给出针对性提示
    is_road_blocked = "road_closure" in filter_status or "road_blocked" in filter_status
    if is_road_blocked:
        if mode == "drive":
            raise ValueError(
                "驾车路线因道路封闭/施工无法通行，建议切换骑行或步行，或选择其他路线～"
            )
        if mode == "bike":
            raise ValueError(
                "骑行路线因道路封闭/施工无法通行，建议切换步行，或选择其他路线～"
            )
        raise ValueError(
            "该路线因道路封闭/施工暂时无法通行，建议选择附近的其他地点作为起终点，或稍后再试～"
        )
    if mode == "drive":
        raise ValueError(
            "驾车无法到达该地点（附近可能只有步行道/台阶），建议切换骑行或步行～"
        )
    if mode == "bike":
        raise ValueError(
            "骑行无法到达该地点（附近可能只有台阶/陡坡），建议切换步行～"
        )
    raise ValueError(
        f"该两点之间没有完全在校内的步行路线，"
        f"可能需要经过校外市政道路。"
        f"可试试选择附近的其他校门或地点作为起终点。"
    )


def compute_route(
    G: nx.MultiDiGraph,
    start_node: int,
    end_node: int,
    constraints: Optional[dict] = None,
    weights: Optional[dict] = None,
    mode: str = "walk",
    road_conditions: Optional[list] = None,
    weather_info: Optional[dict] = None,
    disable_hard_filter: bool = False,
) -> dict:
    """
    多因素路径计算主函数。

    流程：
      1. 解析出行方式与权重（默认 → 校验 → 归一化）
      2. 出行方式过滤（walk 不过滤；bike 删纯台阶；drive 仅留车行道）
      3. 特殊路况处理（封闭边移除，施工/积水等施加惩罚）
      4. 硬约束过滤（坡度等）
      5. 软成本优化（Dijkstra 计算推荐路线）
      6. 计算最短路径基线
      7. 路径长度上限裁剪
      8. 计算重叠率

    Args:
        G: OSMnx MultiDiGraph 路网
        start_node: 起点节点 ID
        end_node: 终点节点 ID
        constraints: 约束 dict，如 {"slope": "avoid"}
        weights: 权重 dict，如 {"distance": 0.2, "slope": 0.5, "scenery": 0.3}；
                 None 时取 MODE_DEFAULT_WEIGHTS[mode]
        mode: 出行方式 "walk" / "bike" / "drive"（默认 "walk"）
        road_conditions: 路况事件列表，None 时自动加载 active 事件
        disable_hard_filter: 设为 True 时跳过硬约束过滤（消融实验用）

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
            "max_len": float, 全图最长边长度
            "_annotation_degraded": bool, 是否处于标注降级
            "mode": str, 出行方式
            "duration_min": float, 推荐路径预估时长（分钟）
            "shortest_duration_min": float, 最短路径预估时长（分钟）
            "speed_kmh": float, 模式平均速度
            "road_conditions_applied": int, 实际生效的路况事件数
        }

    Raises:
        ValueError: 起终点不可达时
    """
    constraints = constraints or {}
    mode = normalize_mode(mode)

    if weights is None:
        resolved_weights = dict(MODE_DEFAULT_WEIGHTS[mode])
    else:
        resolved_weights = resolve_weights(weights)

    # 天气影响权重：高温时倾向树荫景观路（在归一化/校验后的权重上微调再归一化）
    weather_penalty = {}
    weather_applied = False
    if weather_info:
        try:
            from spatial.weather import weather_slope_penalty, adjust_weights_for_weather, classify_weather
            slope_pen = weather_slope_penalty(weather_info)
            weather_applied = bool(classify_weather(weather_info).get("label"))
            if slope_pen:
                # 湿滑：对陡坡/台阶边按 slope_level 加惩罚（先收集 level→multiplier）
                # 实际边惩罚在 G_filtered 构建后按 slope_level 注入
                pass
            hot_adj = adjust_weights_for_weather(resolved_weights, weather_info)
            # 高温且 weights 非用户显式锁定时采用；这里仅在发生变化时覆盖
            if hot_adj != resolved_weights:
                resolved_weights = resolve_weights(hot_adj)
        except Exception as e:
            logger.warning("天气权重调整失败，跳过: %s", e)

    max_len, norm_lengths = _normalize_lengths(G)

    annotation_degraded = _should_degrade_annotations()

    # 1) 出行方式过滤（所有模式先剔真校外边；bike/drive 再按方式删边+删孤立节点）
    G_mode, mode_status, mode_penalty = filter_graph_for_mode(G, mode)

    # 起终点在过滤后被作为孤立节点移除（如驾车时起终点只连台阶/步行道，
    # 或步行时起终点只连校外路段）→ 该方式不可达，给友好提示
    if start_node not in G_mode or end_node not in G_mode:
        _raise_no_path(G, start_node, end_node, mode_status, G_mode, mode=mode)

    # 1.5) 特殊路况处理：封闭/施工硬删除，事故3×惩罚，活动/积水软惩罚
    # 步行模式不受路况影响（行人可绕行施工区域）
    road_penalty = {}
    road_conditions_applied = 0
    closed_edges = set()
    G_mode_before_road = G_mode  # 保存路况处理前的图，用于不可达时软降级重试
    if mode != "walk" and road_conditions is None:
        try:
            from spatial.road_conditions import list_conditions
            road_conditions = list_conditions()
        except Exception as e:
            logger.warning("路况加载失败，跳过: %s", e)
            road_conditions = []
    if mode != "walk" and road_conditions:
        from spatial.road_conditions import apply_conditions_to_graph
        G_mode, road_penalty, closed_edges = apply_conditions_to_graph(G_mode, road_conditions)
        road_conditions_applied = len(road_conditions)
        if closed_edges:
            mode_status = f"{mode_status}+road_closure"
        if road_penalty:
            mode_status = f"{mode_status}+road_penalty"
        # 路况封闭边可能导致起终点变成孤立节点
        if start_node not in G_mode or end_node not in G_mode:
            _raise_no_path(G, start_node, end_node, f"{mode_status}+road_blocked", G_mode, mode=mode)

    # 2) 硬约束过滤（坡度等），在方式过滤图上进行
    if disable_hard_filter:
        G_filtered, filter_status, constraint_penalty = G_mode, "no_filter", {}
    else:
        G_filtered, filter_status, constraint_penalty = _filter_by_constraints(G_mode, constraints)

    # 天气湿滑惩罚：雨雪天对陡坡/台阶边（slope_level 4/5）施加额外成本，智能避坡
    if weather_info:
        try:
            from spatial.weather import weather_slope_penalty
            slope_pen = weather_slope_penalty(weather_info)
            if slope_pen:
                for u, v, k, data in G_filtered.edges(keys=True, data=True):
                    lvl = data.get("slope_level", 3)
                    mult = slope_pen.get(lvl)
                    if mult:
                        weather_penalty[(u, v, k)] = mult
        except Exception as e:
            logger.warning("天气湿滑惩罚应用失败，跳过: %s", e)

    # 方式惩罚 + 路况惩罚 + 天气惩罚 + 约束惩罚合并（同一 key 相乘）
    penalty_map = _merge_penalty_maps(mode_penalty, road_penalty)
    penalty_map = _merge_penalty_maps(penalty_map, weather_penalty)
    penalty_map = _merge_penalty_maps(penalty_map, constraint_penalty)

    # 将 edge key 注入边数据，使 edge_weight 能按 (u, v, k) 查找 norm 和 penalty
    for u, v, k, data in G_filtered.edges(keys=True, data=True):
        data["_key"] = k

    if mode == "walk":
        status_parts = [mode_status] if mode_status != "no_filter" else []
        if filter_status != "no_filter":
            status_parts.append(filter_status)
        if annotation_degraded is not None:
            status_parts.append(annotation_degraded)
        filter_status = "+".join(status_parts) if status_parts else "no_filter"
    else:
        status_parts = [mode_status]
        if filter_status != "no_filter":
            status_parts.append(filter_status)
        if annotation_degraded is not None:
            status_parts.append(annotation_degraded)
        filter_status = "+".join(status_parts)

    outside_penalty = MODE_OUTSIDE_ROAD_PENALTY[mode]

    edge_weight = _edge_cost_factory(
        G_filtered, norm_lengths, resolved_weights, penalty_map,
        annotation_degraded, outside_road_penalty=outside_penalty,
    )

    try:
        recommended = nx.dijkstra_path(
            G_filtered, start_node, end_node, weight=edge_weight
        )
    except nx.NetworkXNoPath:
        # 路况封闭边导致不可达时，软降级重试：不删边，封闭边施加 1000× 惩罚
        if closed_edges:
            closure_penalty = {(u, v, k): 1000.0 for u, v, k in closed_edges}
            retry_penalty = _merge_penalty_maps(mode_penalty, road_penalty, closure_penalty)
            edge_weight = _edge_cost_factory(
                G_mode_before_road, norm_lengths, resolved_weights, retry_penalty,
                annotation_degraded, outside_road_penalty=outside_penalty,
            )
            try:
                recommended = nx.dijkstra_path(
                    G_mode_before_road, start_node, end_node, weight=edge_weight
                )
                filter_status = "degraded_road_closure"
                if annotation_degraded is not None:
                    filter_status = f"degraded_road_closure+{annotation_degraded}"
                logger.info(
                    "路况封闭导致 start=%s end=%s mode=%s 不可达，软降级（封闭边1000×成本）后重算成功",
                    start_node, end_node, mode,
                )
            except nx.NetworkXNoPath:
                _raise_no_path(G, start_node, end_node, filter_status, G_filtered, mode=mode)
        # 避坡硬过滤（删除 slope_level=5 边）可能割裂路网（湖滨/凌波门等台阶密集区域）。
        # 软降级重试：不删边，level5 边 3× 成本、level4 边 2× 成本，保证可达、仍尽量少走陡坡。
        elif constraints.get("slope") == "avoid":
            retry_penalty = {}
            for u, v, k, data in G_mode.edges(keys=True, data=True):
                lvl = data.get("slope_level")
                if lvl == 5:
                    retry_penalty[(u, v, k)] = 3.0
                elif lvl == 4:
                    retry_penalty[(u, v, k)] = 2.0
            retry_penalty = _merge_penalty_maps(mode_penalty, retry_penalty)
            edge_weight = _edge_cost_factory(
                G_mode, norm_lengths, resolved_weights, retry_penalty,
                annotation_degraded, outside_road_penalty=outside_penalty,
            )
            try:
                recommended = nx.dijkstra_path(
                    G_mode, start_node, end_node, weight=edge_weight
                )
                if mode == "walk":
                    filter_status = "degraded_slope"
                    if annotation_degraded is not None:
                        filter_status = f"degraded_slope+{annotation_degraded}"
                else:
                    filter_status = f"{mode_status}+degraded_slope"
                    if annotation_degraded is not None:
                        filter_status = f"{mode_status}+degraded_slope+{annotation_degraded}"
                logger.info(
                    "避坡硬过滤导致 start=%s end=%s mode=%s 不可达，软降级（陡坡3×成本）后重算成功",
                    start_node, end_node, mode,
                )
            except nx.NetworkXNoPath:
                _raise_no_path(G, start_node, end_node, filter_status, G_filtered, mode=mode)
        else:
            _raise_no_path(G, start_node, end_node, filter_status, G_filtered, mode=mode)

    # 最短路径基线在方式过滤图上计算（避免驾车最短路线穿台阶/步行道）
    try:
        shortest = nx.dijkstra_path(G_mode, start_node, end_node, weight="length")
    except nx.NetworkXNoPath:
        shortest = recommended

    recommended_len = _path_length(G, recommended)
    shortest_len = _path_length(G, shortest)

    cap_multplier = _PATH_LENGTH_CAP_MULTIPLIER
    cap_max = _PATH_LENGTH_CAP_MAX
    length_capped = False

    if shortest_len > 0 and recommended_len > shortest_len * cap_multplier:
        logger.info(
            "推荐路径 %.0fm 超过最短路径 %.0fm 的 %.0f 倍，标记为超长",
            recommended_len, shortest_len, cap_multplier
        )
        length_capped = True
    elif recommended_len > cap_max:
        logger.info(
            "推荐路径 %.0fm 超过上限 %.0fm，标记为超长",
            recommended_len, cap_max
        )
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

    # 计算路径加权平均坡度和景观（用于实验评估）
    slope_avg_rec = _weighted_avg_attr(G, recommended, "slope_level")
    scenery_avg_rec = _weighted_avg_attr(G, recommended, "scenery_level")
    slope_avg_short = _weighted_avg_attr(G, shortest, "slope_level")
    scenery_avg_short = _weighted_avg_attr(G, shortest, "scenery_level")

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
        "mode": mode,
        "duration_min": estimate_duration_min(recommended_len, mode),
        "shortest_duration_min": estimate_duration_min(shortest_len, mode),
        "speed_kmh": MODE_SPEEDS_KMH[mode],
        "road_conditions_applied": road_conditions_applied,
        "weather_applied": weather_applied,
        "slope_avg_recommended": round(slope_avg_rec, 3),
        "scenery_avg_recommended": round(scenery_avg_rec, 3),
        "slope_avg_shortest": round(slope_avg_short, 3),
        "scenery_avg_shortest": round(scenery_avg_short, 3),
    }


def compute_route_with_annotations(
    G: nx.MultiDiGraph,
    start_node: int,
    end_node: int,
    constraints: Optional[dict] = None,
    weights: Optional[dict] = None,
    annotations: Optional[list] = None,
    mode: str = "walk",
    road_conditions: Optional[list] = None,
    weather_info: Optional[dict] = None,
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
        mode: 出行方式 "walk" / "bike" / "drive"（默认 "walk"）
        road_conditions: 路况事件列表

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

            # edge_ids = [u_node, v_node, key] — use as triple, don't iterate
            if len(edge_ids) >= 3:
                u_target, v_target, k_target = int(edge_ids[0]), int(edge_ids[1]), int(edge_ids[2])
                if G_annotated.has_edge(u_target, v_target, k_target):
                    data = G_annotated[u_target][v_target][k_target]
                    if slope is not None:
                        data["slope_level"] = int(slope)
                    if scenery is not None:
                        data["scenery_level"] = int(scenery)
                    if name:
                        data["name"] = str(name)

    return compute_route(
        G_annotated, start_node, end_node, constraints, weights, mode=mode,
        road_conditions=road_conditions, weather_info=weather_info,
    )