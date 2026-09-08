"""
特殊路况管理模块

管理校园内的特殊路况事件（道路封闭、施工、活动、积水等），
路径规划时根据路况对受影响路段施加惩罚或移除。

数据持久化到 data/road_conditions.json。

事件类型：
  - closure: 道路封闭（硬过滤，移除边）
  - construction: 施工（软惩罚 3x）
  - event: 活动（软惩罚 2x，如校庆、运动会）
  - flooding: 积水（软惩罚 2.5x）
  - accident: 事故（软惩罚 2x）
"""

import json
import logging
import os
import threading
import time
import uuid
import math
from typing import Optional

import networkx as nx

logger = logging.getLogger(__name__)

_CONDITIONS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "road_conditions.json",
)

# 事件类型 → 边成本惩罚系数（inf 表示硬删除边）
CONDITION_PENALTIES = {
    "closure": float("inf"),
    "construction": float("inf"),  # 施工也硬禁止通行
    "event": 2.0,
    "flooding": 2.5,
    "accident": 3.0,  # 事故 3× 惩罚
}

CONDITION_LABELS = {
    "closure": "道路封闭",
    "construction": "施工",
    "event": "活动",
    "flooding": "积水",
    "accident": "事故",
}

_lock = threading.Lock()
_cache = None  # list of condition dicts
_cache_mtime = 0.0


def _load_conditions() -> list:
    """从 JSON 文件加载路况事件，带 mtime 缓存。"""
    global _cache, _cache_mtime
    with _lock:
        try:
            mtime = os.path.getmtime(_CONDITIONS_FILE)
        except OSError:
            mtime = 0.0
        if _cache is not None and mtime == _cache_mtime:
            return list(_cache)
        try:
            with open(_CONDITIONS_FILE, "r", encoding="utf-8") as f:
                _cache = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("路况文件加载失败，使用空列表: %s", e)
            _cache = []
        _cache_mtime = mtime
        return list(_cache)


def _save_conditions(conditions: list) -> None:
    """持久化路况事件到 JSON 文件。"""
    global _cache, _cache_mtime
    with _lock:
        os.makedirs(os.path.dirname(_CONDITIONS_FILE), exist_ok=True)
        with open(_CONDITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(conditions, f, ensure_ascii=False, indent=2)
        _cache = conditions
        _cache_mtime = os.path.getmtime(_CONDITIONS_FILE)


def list_conditions() -> list:
    """返回所有路况事件（含已过期的，由调用方过滤）。"""
    conditions = _load_conditions()
    now = time.time()
    active = []
    for c in conditions:
        start = c.get("start_time", 0)
        end = c.get("end_time", 0)
        if end > 0 and now > end:
            continue  # 已过期
        active.append(c)
    return active


def add_condition(
    cond_type: str,
    name: str,
    lng: float,
    lat: float,
    radius_m: float = 30.0,
    description: str = "",
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
) -> dict:
    """
    添加一个路况事件。

    Args:
        cond_type: 事件类型（closure/construction/event/flooding/accident）
        name: 事件名称
        lng, lat: 事件中心坐标（GCJ-02，与 POI 一致）
        radius_m: 影响半径（米）
        description: 描述
        start_time, end_time: 生效时间戳（秒），None 表示立即生效/长期有效

    Returns:
        新创建的事件 dict
    """
    if cond_type not in CONDITION_PENALTIES:
        raise ValueError(f"未知路况类型: {cond_type}")

    now = time.time()
    condition = {
        "id": str(uuid.uuid4())[:8],
        "type": cond_type,
        "name": name,
        "coordinates": {"lng": lng, "lat": lat},
        "radius_m": radius_m,
        "description": description,
        "start_time": start_time if start_time is not None else now,
        "end_time": end_time if end_time is not None else 0,  # 0 = 长期有效
        "created_at": now,
    }
    conditions = _load_conditions()
    conditions.append(condition)
    _save_conditions(conditions)
    logger.info("新增路况事件: %s (%s) @ (%.6f, %.6f)", name, cond_type, lng, lat)
    return condition


def remove_condition(cond_id: str) -> bool:
    """删除一个路况事件，返回是否成功。"""
    conditions = _load_conditions()
    new_conditions = [c for c in conditions if c["id"] != cond_id]
    if len(new_conditions) == len(conditions):
        return False
    _save_conditions(new_conditions)
    logger.info("删除路况事件: %s", cond_id)
    return True


def _edge_in_radius(G, u, v, k, data, center_lng, center_lat, radius_m):
    """
    判断一条边是否落在以 (center_lng, center_lat) 为中心、radius_m 为半径的圆内。
    坐标均为 WGS-84。

    shapely 可用时：计算事件点到边几何（LineString）的精确距离。
    shapely 缺失时：降级为事件点到边中点/端点的 haversine 距离（略保守）。
    """
    try:
        from shapely.geometry import Point, LineString
        from shapely import wkt
        center = Point(center_lng, center_lat)
        geom = data.get("geometry")
        if isinstance(geom, LineString):
            line = geom
        elif isinstance(geom, str) and geom.strip():
            # OSM 原始 geometry 可能是 WKT 字符串，解析为 shapely 对象
            try:
                line = wkt.loads(geom)
            except Exception:
                line = None
        else:
            line = None
        if line is None:
            ud = G.nodes[u]
            vd = G.nodes[v]
            line = LineString([
                (float(ud.get("x", 0)), float(ud.get("y", 0))),
                (float(vd.get("x", 0)), float(vd.get("y", 0))),
            ])
        radius_deg = radius_m / 111320.0
        return line.distance(center) <= radius_deg
    except ImportError:
        # 降级：用事件点到边端点和中点的最小 haversine 距离
        def _dist_m(lng, lat):
            rlat = math.radians(center_lat)
            dlat = math.radians(lat - center_lat)
            dlng = math.radians(lng - center_lng)
            a = math.sin(dlat / 2) ** 2 + math.cos(rlat) * math.cos(math.radians(lat)) * math.sin(dlng / 2) ** 2
            return 6371000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        ud = G.nodes[u]
        vd = G.nodes[v]
        ux, uy = float(ud.get("x", 0)), float(ud.get("y", 0))
        vx, vy = float(vd.get("x", 0)), float(vd.get("y", 0))
        mx, my = (ux + vx) / 2.0, (uy + vy) / 2.0
        min_dist = min(
            _dist_m(ux, uy),
            _dist_m(vx, vy),
            _dist_m(mx, my),
        )
        return min_dist <= radius_m


def _iter_affected_edges(G, conditions, types=None):
    """遍历所有路况事件，产出 (edge_key, condition) 对。types 限定事件类型集合。"""
    from spatial.coord_transform import gcj02_to_wgs84
    for cond in conditions:
        ctype = cond["type"]
        if types is not None and ctype not in types:
            continue
        if CONDITION_PENALTIES.get(ctype) is None:
            continue
        lng_wgs, lat_wgs = gcj02_to_wgs84(
            cond["coordinates"]["lng"], cond["coordinates"]["lat"]
        )
        radius_m = cond.get("radius_m", 30.0)
        for u, v, k, data in G.edges(keys=True, data=True):
            if _edge_in_radius(G, u, v, k, data, lng_wgs, lat_wgs, radius_m):
                yield (u, v, k), cond


def get_condition_penalties(
    G: nx.MultiDiGraph,
    conditions: Optional[list] = None,
) -> dict:
    """
    计算受路况影响的边的惩罚系数。

    Args:
        G: 路网图（WGS-84 坐标）
        conditions: 路况事件列表，None 时自动加载

    Returns:
        {(u, v, k): penalty_factor} 受影响边的惩罚系数
        closure 类型的边不在返回中（由调用方决定是否移除）
    """
    if conditions is None:
        conditions = list_conditions()
    if not conditions:
        return {}

    penalties = {}
    non_closure = {t for t in CONDITION_PENALTIES if t != "closure"}
    for (u, v, k), cond in _iter_affected_edges(G, conditions, types=non_closure):
        penalty = CONDITION_PENALTIES[cond["type"]]
        key = (u, v, k)
        # 多条路况叠加取最大惩罚
        if key not in penalties or penalty > penalties[key]:
            penalties[key] = penalty

    return penalties


def get_closed_edges(
    G: nx.MultiDiGraph,
    conditions: Optional[list] = None,
) -> set:
    """
    返回因道路封闭/施工而不可通行的边集合 {(u, v, k)}。
    所有惩罚系数为 inf 的事件类型（closure、construction）都会硬删除边。
    """
    if conditions is None:
        conditions = list_conditions()
    # 动态收集所有硬禁止类型（惩罚为 inf）
    hard_block_types = {
        t for t, p in CONDITION_PENALTIES.items() if p == float("inf")
    }
    closed = set()
    for (u, v, k), _cond in _iter_affected_edges(G, conditions, types=hard_block_types):
        closed.add((u, v, k))
    return closed


def apply_conditions_to_graph(
    G: nx.MultiDiGraph,
    conditions: Optional[list] = None,
) -> tuple:
    """
    将路况应用到路网，返回 (G_modified, penalty_map, closed_edges)。

    策略：
    - closure: 硬删除边（真的不能走）
    - 其他类型: 施加惩罚系数（由调用方在 cost 函数中使用）

    返回 closed_edges 供调用方在不可达时做软降级重试。
    """
    if conditions is None:
        conditions = list_conditions()
    if not conditions:
        return G, {}, set()

    closed = get_closed_edges(G, conditions)
    penalties = get_condition_penalties(G, conditions)

    if closed:
        G = G.copy()
        G.remove_edges_from(list(closed))
        # 清理孤立节点
        isolated = [n for n, deg in G.degree() if deg == 0]
        if isolated:
            G.remove_nodes_from(isolated)

    return G, penalties, closed
