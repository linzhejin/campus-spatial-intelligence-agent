"""
特殊路况管理模块（边绑定模型）

路况事件绑定到具体路网边，而不是"一个点 + 影响半径"的圆：
  - 管理员在地图上点出事发位置，snap_to_edge() 服务端吸附到最近路段
  - 事件持久化边标识（OSM 节点 u/v/key）+ 吸附点 + 道路名 + 边几何快照
  - 规划时按 (u, v, key) 精确命中（有向图双向同时生效）
  - 路网重新下载导致节点 id 失效时，用吸附点几何回退（12m 容差）
  - 更早的 radius_m 圆模型数据仍可读取，按旧半径几何回退

事件类型 × 出行方式影响矩阵（CONDITION_EFFECTS）：
                     walk        bike        drive
  closure 道路封闭   block       block       block   物理断行，人车均不可通行
  construction 施工  4.0 软惩罚   block       block   围挡断车道，行人可谨慎穿行
  flooding 积水      block       block       4.0     深积水人/骑行不可蹚，车可慢速通过
  accident 事故      3.0         3.0         block   人可侧穿绕行，车辆堵死
  event 活动         2.0         2.0         block   人流密集步行可穿，机动车管制

block = 硬移除边（不可通行）；数字 = 该边成本乘以的惩罚系数。

数据持久化到 data/road_conditions.json，坐标全部 GCJ-02（与 POI/前端一致）。
"""

import json
import logging
import os
import threading
import time
import uuid
from typing import Optional

import networkx as nx

logger = logging.getLogger(__name__)

_CONDITIONS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "road_conditions.json",
)

# "block" = 硬禁止；浮点数 = 边成本惩罚倍数
CONDITION_EFFECTS = {
    "closure":      {"walk": "block", "bike": "block", "drive": "block"},
    "construction": {"walk": 4.0,     "bike": "block", "drive": "block"},
    "flooding":     {"walk": "block", "bike": "block", "drive": 4.0},
    "accident":     {"walk": 3.0,     "bike": 3.0,     "drive": "block"},
    "event":        {"walk": 2.0,     "bike": 2.0,     "drive": "block"},
}

CONDITION_LABELS = {
    "closure": "道路封闭",
    "construction": "施工",
    "event": "活动",
    "flooding": "积水",
    "accident": "事故",
}

# 管理员点击点离最近路段超过该距离（米）则拒绝吸附
SNAP_MAX_DIST_M = 30.0
# 边绑定数据在节点 id 失效时的几何回退容差
_EDGE_FALLBACK_DIST_M = 12.0
# 旧圆模型数据缺省半径
_LEGACY_DEFAULT_RADIUS_M = 30.0

# 边链扩展：一次管制影响的是"两个路口之间的整段道路"，而非单个 OSM edge。
# 从吸附边沿拓扑向两端延伸，直连中间节点（无向度=2）直接穿过，直到路口/断头。
_CHAIN_MAX_EDGES = 40        # 单条链最多合并的边数
_CHAIN_MAX_LENGTH_M = 600.0  # 单条链最长（米），校园一个街区足够
_CHAIN_MAX_TURN_DEG = 100.0  # degree=2 处近乎折返（>100°）视为不同道路，停止延伸

_lock = threading.Lock()
_cache = None  # list of condition dicts
_cache_mtime = 0.0


# ===================== 持久化 =====================

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


def list_conditions(include_inactive: bool = False) -> list:
    """返回路况事件。

    生效判定：start_time <= now < end_time（end_time 为 0 表示长期有效）。
    include_inactive=True 时返回全部事件（含未开始/已过期），供管理端展示。
    """
    conditions = _load_conditions()
    if include_inactive:
        return list(conditions)
    now = time.time()
    active = []
    for c in conditions:
        start = c.get("start_time", 0) or 0
        end = c.get("end_time", 0) or 0
        if start and now < start:
            continue  # 尚未开始（如预录的樱花节管制）
        if end and now > end:
            continue  # 已过期
        active.append(c)
    return active


def add_condition(
    cond_type: str,
    name: str,
    edge: dict,
    click_point: Optional[dict] = None,
    description: str = "",
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
    created_by: str = "web",
) -> dict:
    """
    添加一个绑定到具体路段的路况事件。

    Args:
        cond_type: 事件类型（closure/construction/event/flooding/accident）
        name: 事件名称
        edge: snap_to_edge() 的返回结果（含 u/v/key/road_name/snap/geometry）
        click_point: 管理员原始点击坐标 {"lng","lat"}（GCJ-02），审计用
        description: 描述
        start_time/end_time: 生效时间戳（秒），None = 立即生效/长期有效
        created_by: 录入来源（web / token:保卫部）

    Returns:
        新创建的事件 dict
    """
    if cond_type not in CONDITION_EFFECTS:
        raise ValueError(f"未知路况类型: {cond_type}")
    if not edge or edge.get("u") is None or edge.get("v") is None:
        raise ValueError("缺少绑定路段信息 edge（u/v）")

    now = time.time()
    condition = {
        "id": str(uuid.uuid4())[:8],
        "type": cond_type,
        "name": name,
        "description": description or "",
        "edge": {
            "u": int(edge["u"]),
            "v": int(edge["v"]),
            "key": int(edge.get("key", 0)),
            # 完整边链（路口到路口）；旧数据无此字段时由 u/v 单条边兜底
            "edges": edge.get("edges") or [
                [int(edge["u"]), int(edge["v"]), int(edge.get("key", 0))]
            ],
            "chain_length_m": edge.get("chain_length_m", 0.0),
            "road_name": edge.get("road_name") or "",
            "snap": {
                "lng": float(edge["snap_lng_gcj"]),
                "lat": float(edge["snap_lat_gcj"]),
            },
            "geometry_gcj": edge.get("geometry_gcj") or [],
            "snap_dist_m": round(float(edge.get("dist_m", 0.0)), 1),
        },
        "click_point": click_point or {},
        "start_time": start_time if start_time is not None else now,
        "end_time": end_time if end_time is not None else 0,  # 0 = 长期有效
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
    }
    conditions = _load_conditions()
    conditions.append(condition)
    _save_conditions(conditions)
    logger.info(
        "新增路况: %s (%s) edge=(%s,%s,k%s) by=%s",
        name, cond_type, condition["edge"]["u"], condition["edge"]["v"],
        condition["edge"]["key"], created_by,
    )
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


def update_condition(cond_id: str, changes: dict) -> Optional[dict]:
    """更新事件字段（name/description/start_time/end_time），返回更新后的事件。

    特殊用法：changes={"end_time": 0} 由调用方把"立即结束"翻译成当前时间戳传入。
    """
    conditions = _load_conditions()
    target = None
    for c in conditions:
        if c["id"] == cond_id:
            target = c
            break
    if target is None:
        return None
    for field in ("name", "description", "start_time", "end_time"):
        if field in changes and changes[field] is not None:
            target[field] = changes[field]
    target["updated_at"] = time.time()
    _save_conditions(conditions)
    logger.info("更新路况事件 %s: %s", cond_id, list(changes.keys()))
    return target


# ===================== 路网吸附 =====================

def _edge_line(G, u, v, data):
    """取边的 shapely LineString（WGS-84）；geometry 可能是 LineString 或 WKT 字符串。"""
    from shapely.geometry import LineString
    from shapely import wkt
    geom = data.get("geometry")
    if geom is None:
        ud, vd = G.nodes[u], G.nodes[v]
        return LineString([
            (float(ud.get("x", 0)), float(ud.get("y", 0))),
            (float(vd.get("x", 0)), float(vd.get("y", 0))),
        ])
    if hasattr(geom, "coords"):
        return geom
    if isinstance(geom, str) and geom.strip():
        try:
            return wkt.loads(geom)
        except Exception:
            pass
    ud, vd = G.nodes[u], G.nodes[v]
    return LineString([
        (float(ud.get("x", 0)), float(ud.get("y", 0))),
        (float(vd.get("x", 0)), float(vd.get("y", 0))),
    ])


def _line_coords(line) -> list:
    """LineString → [[lng, lat], ...]（WGS-84）。"""
    try:
        return [[float(x), float(y)] for x, y in line.coords]
    except Exception:
        return []


def _road_name_of(edge_data: dict) -> str:
    """从边属性取规范道路名（无名路返回 ''）。"""
    raw = edge_data.get("name")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if isinstance(raw, list) and raw:
        return str(raw[0]).strip()
    return ""


def _undirected_degree(G, node) -> int:
    """节点的无向连接度：双向平行边只算一个邻居。==2 表示道路直连点，可穿过。"""
    return len(set(G.successors(node)) | set(G.predecessors(node)))


def _bearing_deg(p1, p2) -> float:
    import math
    dx = (p2[0] - p1[0]) * math.cos(math.radians((p1[1] + p2[1]) / 2.0))
    dy = p2[1] - p1[1]
    return math.degrees(math.atan2(dy, dx)) % 360.0


def _edge_end_bearing(coords: list, forward: bool) -> float:
    """边几何在起点(forward=True)/终点(forward=False)端的切线方位角。"""
    if len(coords) < 2:
        return 0.0
    if forward:
        return _bearing_deg(coords[0], coords[1])
    return _bearing_deg(coords[-2], coords[-1])


def _angle_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _walk_one_direction(G, cur, prev, heading, road_name, budget_edges, budget_m):
    """沿 cur 端单向延伸（道路为双向 MultiDiGraph，用出边即可）。

    停止条件：到达路口/断头（无向度≠2）、边数/长度预算耗尽、
    或直连点处道路名变化 / 近乎折返（防止穿到匝道、另一条路上）。

    Returns:
        keys: [(a,b,k), ...] 沿延伸方向的有向边
        pts:  [[lng,lat], ...] 从 cur 节点开始（含 cur）的拼接坐标
        length_m: 新增边总长度
    """
    keys, pts, total = [], [], 0.0
    while budget_edges > 0 and budget_m > 0:
        if _undirected_degree(G, cur) != 2:
            break  # 路口、丁字、断头：管制段到此为止
        candidates = []
        for _, nxt, k, d in G.out_edges(cur, keys=True, data=True):
            if nxt == prev:
                continue
            cs = _line_coords(_edge_line(G, cur, nxt, d))
            if len(cs) < 2:
                continue
            turn = _angle_diff(heading, _edge_end_bearing(cs, True))
            name = _road_name_of(d)
            length = float(d.get("length", 0) or 0)
            candidates.append((turn, nxt, k, cs, name, length))
        if not candidates:
            break
        candidates.sort(key=lambda c: c[0])
        turn, nxt, k, cs, name, length = candidates[0]
        if turn > _CHAIN_MAX_TURN_DEG:
            break
        if road_name and name and name != road_name:
            break
        if length <= 0 or length > budget_m:
            break
        keys.append((cur, nxt, k))
        if not pts:
            pts.append(cs[0])
        pts.extend(cs[1:])
        total += length
        budget_edges -= 1
        budget_m -= length
        prev, cur = cur, nxt
        heading = _edge_end_bearing(cs, False)
        if name:
            road_name = name
    return keys, pts, total


def _expand_edge_chain(G, u, v, k):
    """把吸附边 (u,v,k) 扩展为"路口到路口"的完整道路链。

    Returns:
        ordered_keys: [(a,b,k), ...] 沿几何方向（道路一端→另一端）的有向边
        coords_wgs:   [[lng,lat], ...] 合并去重后的整链几何（WGS-84）
        length_m:     整链长度
        edge_count:   合并边数
    """
    main_data = G.get_edge_data(u, v, k) or {}
    main_coords = _line_coords(_edge_line(G, u, v, main_data))
    main_len = float(main_data.get("length", 0) or 0)
    main_name = _road_name_of(main_data)
    budget_e = _CHAIN_MAX_EDGES - 1
    budget_m = _CHAIN_MAX_LENGTH_M - main_len

    # 前向：v 端继续；后向：u 端逆推（沿出边走向 v 的反方向）
    fwd_keys, fwd_pts, fwd_len = _walk_one_direction(
        G, v, u, _edge_end_bearing(main_coords, False), main_name,
        budget_e // 2, budget_m / 2,
    )
    bwd_keys, bwd_pts, bwd_len = _walk_one_direction(
        G, u, v, (_edge_end_bearing(main_coords, True) + 180.0) % 360.0, main_name,
        budget_e - budget_e // 2, budget_m - budget_m / 2,
    )

    # bwd_pts 方向为 u→道路远端，反转为 远端→…→u，再拼主边与前向
    coords = list(reversed(bwd_pts)) + main_coords
    if fwd_pts:
        coords.extend(fwd_pts[1:])
    # 相邻重复点去重
    deduped = []
    for p in coords:
        if not deduped or abs(deduped[-1][0] - p[0]) > 1e-12 or abs(deduped[-1][1] - p[1]) > 1e-12:
            deduped.append(p)

    ordered_keys = [(b, a, kk) for a, b, kk in reversed(bwd_keys)] + [(u, v, k)] + fwd_keys
    return ordered_keys, deduped, main_len + fwd_len + bwd_len, len(ordered_keys)


def snap_to_edge(
    G: nx.MultiDiGraph,
    lng_gcj: float,
    lat_gcj: float,
    max_dist_m: float = SNAP_MAX_DIST_M,
) -> Optional[dict]:
    """
    把管理员点击的 GCJ-02 坐标吸附到最近的路网边。

    Returns:
        {
          "u","v","key": 吸附主边标识,
          "edges": [[u,v,k],...] 主边沿道路扩展到两端路口后的完整边链,
          "chain_length_m": 整段道路长度,
          "road_name": 路名（可能为空）,
          "snap_lng_gcj","snap_lat_gcj": 吸附点（GCJ-02）,
          "snap_lng_wgs","snap_lat_wgs": 吸附点（WGS-84）,
          "dist_m": 点击点到边的距离（米）,
          "geometry_gcj": [[lng,lat],...] 整段道路几何（GCJ-02，前端直接画线）
        }
        最近边超过 max_dist_m 时返回 None。
    """
    from spatial.coord_transform import gcj02_to_wgs84, wgs84_to_gcj02
    from shapely.geometry import Point

    lng_wgs, lat_wgs = gcj02_to_wgs84(lng_gcj, lat_gcj)
    click = Point(lng_wgs, lat_wgs)
    cos_lat = __import__("math").cos(__import__("math").radians(lat_wgs))

    best = None  # (dist_deg_sq, u, v, k, line, proj_point)
    for u, v, k, data in G.edges(keys=True, data=True):
        line = _edge_line(G, u, v, data)
        proj = line.interpolate(line.project(click))
        dx = (proj.x - lng_wgs) * cos_lat
        dy = proj.y - lat_wgs
        dist_sq = dx * dx + dy * dy
        if best is None or dist_sq < best[0]:
            best = (dist_sq, u, v, k, line, proj)

    if best is None:
        return None

    dist_deg = best[0] ** 0.5
    dist_m = dist_deg * 111320.0
    if dist_m > max_dist_m:
        return None

    _, u, v, k, line, proj = best
    snap_lng_gcj, snap_lat_gcj = wgs84_to_gcj02(proj.x, proj.y)

    # 沿道路扩展到两端路口：管制影响的是一整段道路，不是单个 OSM edge
    try:
        chain_keys, chain_coords_wgs, chain_len_m, _ = _expand_edge_chain(G, u, v, k)
    except Exception:
        logger.warning("边链扩展失败，回退为单条边 (%s,%s,%s)", u, v, k, exc_info=True)
        chain_keys, chain_coords_wgs, chain_len_m = (
            [(u, v, k)], _line_coords(line), float((G.get_edge_data(u, v, k) or {}).get("length", 0) or 0),
        )
    geometry_gcj = [list(wgs84_to_gcj02(x, y)) for x, y in chain_coords_wgs]
    road_name = _road_name_of(G.get_edge_data(u, v, k) or {})

    return {
        "u": int(u),
        "v": int(v),
        "key": int(k),
        "edges": [[int(a), int(b), int(kk)] for a, b, kk in chain_keys],
        "chain_length_m": round(float(chain_len_m), 1),
        "road_name": road_name,
        "snap_lng_gcj": float(snap_lng_gcj),
        "snap_lat_gcj": float(snap_lat_gcj),
        "snap_lng_wgs": float(proj.x),
        "snap_lat_wgs": float(proj.y),
        "dist_m": round(float(dist_m), 1),
        "geometry_gcj": geometry_gcj,
    }


# ===================== 规划期应用 =====================

def _resolve_edge_keys(G: nx.MultiDiGraph, cond: dict) -> set:
    """
    解析事件影响的有向边 key 集合（道路双向通行，双向同时生效）。

    优先级：
      1. edge.edges 边链（路口到路口整段）/ 旧数据 edge.u/v 精确命中（当前路网）
      2. 节点 id 失效（路网重建）→ 整链几何 12m 回退，吸附点回退兜底
      3. 旧版 radius_m 圆模型 → 按原半径几何回退
    """
    edge_info = cond.get("edge")
    if edge_info:
        keys = set()
        chain = edge_info.get("edges")
        if isinstance(chain, list) and chain:
            for triple in chain:
                try:
                    a, b, kk = int(triple[0]), int(triple[1]), int(triple[2])
                except (TypeError, ValueError, IndexError):
                    continue
                if G.has_edge(a, b, kk):
                    keys.add((a, b, kk))
                if G.has_edge(b, a, kk):
                    keys.add((b, a, kk))
        elif edge_info.get("u") is not None and edge_info.get("v") is not None:
            a, b = int(edge_info["u"]), int(edge_info["v"])
            kk = int(edge_info.get("key", 0))
            if G.has_edge(a, b, kk):
                keys.add((a, b, kk))
            if G.has_edge(b, a, kk):
                keys.add((b, a, kk))
        if keys:
            return keys
        # id 全失效 → 整段道路几何回退（覆盖整条链），再退回吸附点回退
        geom = edge_info.get("geometry_gcj") or []
        if len(geom) >= 2:
            chain_keys = _edges_near_geometry(G, geom, _EDGE_FALLBACK_DIST_M)
            if chain_keys:
                return chain_keys
        snap = edge_info.get("snap") or {}
        if snap.get("lng") is not None:
            return _edges_near_point(
                G, float(snap["lng"]), float(snap["lat"]),
                gcj=True, radius_m=_EDGE_FALLBACK_DIST_M,
            )

    # 旧圆模型：coordinates + radius_m
    coords = cond.get("coordinates")
    if coords and coords.get("lng") is not None:
        return _edges_near_point(
            G, float(coords["lng"]), float(coords["lat"]),
            gcj=True, radius_m=float(cond.get("radius_m", _LEGACY_DEFAULT_RADIUS_M)),
        )
    return set()


def _edges_near_point(G, lng, lat, gcj, radius_m) -> set:
    """几何回退：找到点(GCJ-02)半径内的边，双向返回。"""
    from spatial.coord_transform import gcj02_to_wgs84
    from shapely.geometry import Point
    lng_wgs, lat_wgs = gcj02_to_wgs84(lng, lat)
    # 经纬度 → 米的保守换算（高纬方向 1°≈111.3km，经度乘 cos(lat)）
    radius_deg = radius_m / 111320.0
    keys = set()
    for u, v, k, data in G.edges(keys=True, data=True):
        line = _edge_line(G, u, v, data)
        if line.distance(Point(lng_wgs, lat_wgs)) <= radius_deg:
            keys.add((u, v, k))
            if G.has_edge(v, u, k):
                keys.add((v, u, k))
    return keys


def _edges_near_geometry(G, coords_gcj, radius_m=_EDGE_FALLBACK_DIST_M) -> set:
    """整链几何回退：与事件边链折线(GCJ-02)相距 radius_m 内的所有边，双向返回。

    路网重建后节点 id 全部变化时使用——一条链折线与候选边各做一次距离计算。
    """
    from spatial.coord_transform import gcj02_to_wgs84
    from shapely.geometry import LineString
    wgs = [gcj02_to_wgs84(float(p[0]), float(p[1])) for p in coords_gcj if p and len(p) >= 2]
    if len(wgs) < 2:
        return set()
    try:
        chain_line = LineString(wgs)
    except Exception:
        return set()
    radius_deg = radius_m / 111320.0
    keys = set()
    for u, v, k, data in G.edges(keys=True, data=True):
        if _edge_line(G, u, v, data).distance(chain_line) <= radius_deg:
            keys.add((u, v, k))
            if G.has_edge(v, u, k):
                keys.add((v, u, k))
    return keys


def apply_conditions_to_graph(
    G: nx.MultiDiGraph,
    conditions: Optional[list] = None,
    mode: str = "walk",
) -> tuple:
    """
    按出行模式把路况应用到路网。

    Returns:
        (G_modified, penalty_map, closed_edges, applied_count)
        - G_modified: 硬封边移除后的图（无封边时为原图）
        - penalty_map: {(u,v,k): 成本倍数} 软惩罚边
        - closed_edges: 被硬封的边集合（供不可达时软降级重试）
        - applied_count: 在该模式图上实际命中至少一条边的事件数
    """
    if conditions is None:
        conditions = list_conditions()
    if not conditions:
        return G, {}, set(), 0

    penalties = {}
    closed = set()
    applied = 0
    for cond in conditions:
        effect = CONDITION_EFFECTS.get(cond.get("type"), {}).get(mode)
        if effect is None:
            continue
        keys = _resolve_edge_keys(G, cond)
        if not keys:
            continue
        applied += 1
        if effect == "block":
            closed.update(keys)
        else:
            factor = float(effect)
            for key in keys:
                if factor > penalties.get(key, 1.0):
                    penalties[key] = factor

    G_modified = G
    if closed:
        G_modified = G.copy()
        G_modified.remove_edges_from(list(closed))
        isolated = [n for n, deg in G_modified.degree() if deg == 0]
        if isolated:
            G_modified.remove_nodes_from(isolated)

    return G_modified, penalties, closed, applied
