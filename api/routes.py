"""
珞珈智行 (WHU-Walker) — RESTful API 路由

端点:
  POST /api/parse   — 自然语言 → 结构化任务意图
  POST /api/route   — 任务意图 → 多因素路径规划
  POST /api/chat    — 一站式 NL → 解析 + 路径 + 解释
  GET  /api/pois   — POI 列表（支持 type/season 筛选）
  GET  /api/pois/<name> — 单 POI 查询
  POST /api/network/init — 触发路网加载
  GET  /api/road-conditions — 路况事件列表
  POST /api/road-conditions — 新增路况事件
  DELETE /api/road-conditions/<id> — 删除路况事件
"""
import json
import logging
import math
import os
import re
import time
from pathlib import Path

import networkx as nx
from flask import Blueprint, request, jsonify, session

import config
from agents.parser import parse_query, detect_travel_mode
from agents.explainer import generate_explanation, generate_chat_response, generate_suggestions, generate_poi_guidance
from spatial.poi import get_poi, search_pois, list_all_pois, load_pois, find_poi_ambiguous, importance_score
from spatial.network import get_network, load_or_download_network, get_nearest_node, get_node_coords
from spatial.routing import (
    compute_route,
    resolve_weights,
    _path_length,
    TRAVEL_MODES,
    normalize_mode,
    filter_graph_for_mode,
    MODE_DEFAULT_WEIGHTS,
)
from spatial.coord_transform import gcj02_to_wgs84, wgs84_to_gcj02
from spatial.road_conditions import (
    list_conditions, add_condition, remove_condition, update_condition,
    snap_to_edge, CONDITION_LABELS, CONDITION_EFFECTS, SNAP_MAX_DIST_M,
)
from spatial import weather as weather_mod

logger = logging.getLogger(__name__)

# ===== 管理员鉴权 =====
def _is_admin() -> bool:
    return _admin_identity() is not None


def _admin_identity():
    """返回管理员来源标识：'web'（网页 session）/ 'token'（保卫部系统 Token）/ None。"""
    if session.get("is_admin"):
        return "web"
    token = request.headers.get("X-Admin-Token", "").strip()
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
    if token and config.ROAD_CONDITION_ADMIN_TOKEN:
        import hmac as _hmac
        if _hmac.compare_digest(token, config.ROAD_CONDITION_ADMIN_TOKEN):
            return "token"
    return None


def _admin_login_enabled() -> bool:
    return bool(config.ROAD_CONDITION_ADMIN_PASSWORD)


def _require_admin():
    """写操作鉴权（函数式），未登录返回 (err_response, None)；成功返回 (None, identity)。"""
    identity = _admin_identity()
    if identity is None:
        return _err("unauthorized", "需要管理员权限，请先登录", 401)
    return None

api_bp = Blueprint("api", __name__, url_prefix="/api")

_network_initialized = False

# 快捷模式预设（与前端 quick-chip 的 data-mode 一一对应）：
#   distance_first 最短路径 / scenery_first 风景优先 / slope_avoid 平坦优先（避开陡坡）
SHORTCUT_MODE_PRESETS = {
    "distance_first": {
        "weights": {"distance": 0.8, "slope": 0.1, "scenery": 0.1},
        "constraints": {"distance": "short", "slope": "normal", "scenery": "normal"},
    },
    "scenery_first": {
        "weights": {"distance": 0.1, "slope": 0.1, "scenery": 0.8},
        "constraints": {"distance": "medium", "slope": "normal", "scenery": "high"},
    },
    "slope_avoid": {
        "weights": {"distance": 0.1, "slope": 0.8, "scenery": 0.1},
        "constraints": {"distance": "medium", "slope": "avoid", "scenery": "normal"},
    },
}


def _ok(data, status=200):
    resp = {"data": data}
    return jsonify(resp), status


def _err(code, message, status):
    return jsonify({"error": code, "message": message}), status


# ====== WGS-84 → GCJ-02 转换（前端高德底图用 GCJ-02）======
# 后端路网/路径坐标来自 OSM(WGS-84)，直接画在高德(GCJ-02)上会偏 50-100m
def _coords_wgs_to_gcj(coords_list):
    """路径坐标点列表 [{lng, lat}, ...] 整体转换"""
    if not coords_list:
        return coords_list
    return [{"lng": round(gcj_lng, 6), "lat": round(gcj_lat, 6)}
            for c in coords_list
            for gcj_lng, gcj_lat in [wgs84_to_gcj02(c["lng"], c["lat"])]]


def _pois_wgs_to_gcj(pois_list):
    """沿途 POI 列表 [{"lng", "lat", ...}, ...] 整体转换"""
    if not pois_list:
        return pois_list
    result = []
    for p in pois_list:
        gcj_lng, gcj_lat = wgs84_to_gcj02(p.get("lng", 0), p.get("lat", 0))
        new_p = dict(p)
        new_p["lng"] = round(gcj_lng, 6)
        new_p["lat"] = round(gcj_lat, 6)
        result.append(new_p)
    return result


def _resolve_travel_mode(query=None, body_mode=None, intent_mode=None):
    """确定最终出行方式（walk/bike/drive）。

    优先级：
      1. NL 显式关键词（detect_travel_mode explicit=True）
      2. body.travel_mode（经 normalize_mode，非法值兜底为 walk）
      3. intent.mode（LLM 输出 / 多轮上下文继承）
      4. 默认 "walk"
    """
    if query:
        nl_mode, explicit = detect_travel_mode(query)
        if explicit:
            return normalize_mode(nl_mode)
    if body_mode is not None:
        return normalize_mode(body_mode)
    if intent_mode is not None:
        return normalize_mode(intent_mode)
    return "walk"


# —— 非景点目的地默认纯最短 ——
# 景点类 POI（type=scenery 或 scenery_score>=3）用平衡权重；
# 功能性目的地（study/dining/dorm/sports/gate/service 等）和 GPS 坐标目的地
# 一律用纯距离权重，slope/scenery 弱偏好只在用户显式表达或 LLM 输出时生效。
_SCENERY_TYPES = {"scenery"}  # 只明确标注为 scenery 的
_SCENERY_MIN_SCORE = 3       # 片区中心点等也可能被打 3 分

_WEIGHTS_DISTANCE_ONLY = {
    "distance": 0.8, "slope": 0.05, "scenery": 0.05,
}


def _is_scenery_destination(poi_or_ref) -> bool:
    """判断目的地 POI 是否属于景点类（应使用风景偏好权重）。

    入参可能是完整 POI dict（从 get_poi 返回），也可能是 PoiRef 兜底
    （type="coord" 或找不到 POI 时只有 name/coordinates）。兜底情况一律算
    "非景点" → 纯最短。
    """
    if not isinstance(poi_or_ref, dict):
        return False
    t = poi_or_ref.get("type")
    if t == "coord":
        return False
    if t in _SCENERY_TYPES:
        return True
    score = poi_or_ref.get("scenery_score") or 0
    return score >= _SCENERY_MIN_SCORE


def _weights_for_destination(end_poi, mode, explicit_weights=None):
    """根据目的地类型和用户显式偏好决定路由权重。

    规则：
      - explicit_weights 非 None → 用户/LLM 已给出偏好，直接用
      - 终点是景点类 → 用 MODE_DEFAULT_WEIGHTS[mode]（平衡权重）
      - 终点是功能性目的地/GPS 坐标 → 用 _WEIGHTS_DISTANCE_ONLY（纯最短）

    返回 dict 或 None（调用 compute_route 时传 None 会用默认）。
    """
    if explicit_weights is not None:
        return explicit_weights
    mode = normalize_mode(mode)
    if _is_scenery_destination(end_poi):
        # 景点：按出行方式给平衡权重
        return dict(MODE_DEFAULT_WEIGHTS[mode])
    # 非景点：纯距离主导
    return dict(_WEIGHTS_DISTANCE_ONLY)


def _mode_filtered_graph(G, mode):
    """按出行方式过滤路网，返回 (G_mode, status, penalty_map)。

    起终点 snap 用 G_mode（驾车时吸附到最近车行节点）；路径坐标展开/POI 沿途
    检索仍用原图 G（副本节点 id 与 geometry 与原图一致）。
    """
    return filter_graph_for_mode(G, mode)


def _apply_coord_override(intent, coord_start, coord_end):
    """把前端 GPS 定位坐标（WGS-84）覆盖到解析意图的起/终点。

    触发场景：用户说「从我这到樱顶」「到我这来」等，浏览器定位在前端完成，
    以 coord_start/coord_end 随请求上送，坐标即 WGS-84，与路网同源、无需转换。
    仅接受数值合法的坐标；静默忽略非法值（不影响其余解析结果）。
    """
    from agents.parser import PoiRef

    for role, coord in (("start", coord_start), ("end", coord_end)):
        if not isinstance(coord, dict):
            continue
        try:
            lng = float(coord.get("lng"))
            lat = float(coord.get("lat"))
        except (TypeError, ValueError):
            continue
        if not (-180.0 <= lng <= 180.0 and -90.0 <= lat <= 90.0):
            continue
        label = str(coord.get("name") or "我的位置")[:20]
        setattr(intent, role, PoiRef(
            type="coord",
            name=label,
            coordinates={"lng": round(lng, 6), "lat": round(lat, 6)},
        ))


def _endpoint_to_wgs(ref, fallback_poi):
    """把 PoiRef 解析为 (lon_wgs, lat_wgs) 供 nearest_node 使用。

    - type="coord"：坐标本身即 WGS-84（GPS 定位），直接使用；
    - type="poi"（默认）：POI 存 GCJ-02，需转 WGS-84。
    """
    if isinstance(ref, dict) and ref.get("type") == "coord" and ref.get("coordinates"):
        c = ref["coordinates"]
        return float(c["lng"]), float(c["lat"])
    return gcj02_to_wgs84(fallback_poi["lon"], fallback_poi["lat"])


def _haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def _node_to_coord(G, node_id):
    lng, lat = get_node_coords(G, node_id)
    return {"node_id": int(node_id), "lng": round(lng, 6), "lat": round(lat, 6)}


def _parse_linestring(wkt):
    """解析 WKT LINESTRING 字符串为 [(lng, lat), ...] 坐标点列表。

    路网边的 geometry 以 WKT 字符串形式存储（如 "LINESTRING (114.36 30.53, ...)"），
    坐标顺序为 lng lat（经度 纬度）。解析失败返回 None。
    """
    if not wkt or not isinstance(wkt, str):
        return None
    m = re.match(r"LINESTRING\s*\((.*)\)", wkt.strip(), re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    pts = []
    for pair in m.group(1).split(","):
        parts = pair.strip().split()
        if len(parts) >= 2:
            try:
                pts.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
    return pts if pts else None


def _path_to_coords(G, route_nodes):
    """把路径节点序列展开为密集坐标序列（含边的 geometry 中间点）。

    路网下载时用了 simplify=True，节点只保留交叉路口，弯曲道路的中间点
    保存在边的 geometry 属性里。若只返回节点坐标，前端用直线连接会丢失弯道
    细节（"曲线变直线"）。这里沿路径逐边展开 geometry 中间点，让渲染贴合道路。

    Args:
        G: 路网图
        route_nodes: [node_id, ...] 路径节点序列

    Returns:
        [{"lng": float, "lat": float}, ...] 密集坐标序列（不含 node_id，前端只画线）
    """
    if not route_nodes:
        return []

    coords = []
    for i in range(len(route_nodes) - 1):
        u, v = route_nodes[i], route_nodes[i + 1]
        edge_data = G.get_edge_data(u, v)
        if not edge_data:
            continue

        data = min(edge_data.values(), key=lambda d: d.get("length", float("inf")))

        # 添加当前边起点（首段才加；后续段的起点已由上一段终点覆盖）
        if not coords:
            ulng, ulat = get_node_coords(G, u)
            coords.append({"lng": round(ulng, 6), "lat": round(ulat, 6)})

        geom = data.get("geometry")
        pts = _parse_linestring(geom) if geom else None
        if pts and len(pts) >= 2:
            # geometry 首尾点即 u/v，跳过首点，追加中间点与终点
            for lng, lat in pts[1:]:
                coords.append({"lng": round(lng, 6), "lat": round(lat, 6)})
        else:
            # 无 geometry（直线边）→ 直接用终点 v
            vlng, vlat = get_node_coords(G, v)
            coords.append({"lng": round(vlng, 6), "lat": round(vlat, 6)})

    return coords


def _compute_route_costs(G, route_nodes, weights, max_len=0.0):
    if len(route_nodes) < 2:
        return {"distance": 0.0, "slope": 0.0, "scenery": 0.0}

    if max_len == 0:
        for u, v, data in G.edges(data=True):
            length = data.get("length", 0)
            if length > max_len:
                max_len = length

    if max_len == 0:
        max_len = 1.0

    d_cost = 0.0
    s_cost = 0.0
    v_cost = 0.0

    for i in range(len(route_nodes) - 1):
        u, v = route_nodes[i], route_nodes[i + 1]
        edge_data = G.get_edge_data(u, v)
        if not edge_data:
            continue

        data = min(edge_data.values(), key=lambda d: d.get("length", float("inf")))
        length = data.get("length", 0)
        norm_length = length / max_len

        slope = float(data.get("slope_level", 3)) / 5.0
        scenery = float(data.get("scenery_level", 3)) / 5.0

        d_cost += weights["distance"] * norm_length
        s_cost += weights["slope"] * slope
        v_cost += weights["scenery"] * (1.0 - scenery)

    return {
        "distance": round(d_cost, 4),
        "slope": round(s_cost, 4),
        "scenery": round(v_cost, 4),
    }


def _find_pois_along_route(G, route_nodes, threshold_m=100.0, limit=8, min_importance=8.0):
    all_pois = load_pois()
    route_coords = []
    for nid in route_nodes:
        try:
            lng, lat = get_node_coords(G, nid)
            route_coords.append((lng, lat))
        except Exception:
            continue

    if not route_coords:
        return []

    along = []
    for poi in all_pois:
        coords = poi.get("coordinates", {})
        poi_lat = coords.get("lat", 0)
        poi_lng = coords.get("lng", 0)

        # GCJ-02 → WGS-84：POI 坐标转成 WGS-84 后与路网节点（WGS-84）比较距离
        poi_lng_wgs, poi_lat_wgs = gcj02_to_wgs84(poi_lng, poi_lat)

        min_dist = float("inf")
        for rlng, rlat in route_coords:
            dist = _haversine(poi_lat_wgs, poi_lng_wgs, rlat, rlng)
            if dist < min_dist:
                min_dist = dist

        if min_dist <= threshold_m:
            from spatial.poi import _flatten_poi
            imp = importance_score(poi)
            if imp < min_importance:
                continue  # 不重要的点（如普通宿舍）不标
            flat = _flatten_poi(poi)
            flat["distance_to_route_m"] = round(min_dist, 1)
            flat["importance"] = round(imp, 2)
            along.append(flat)

    # 按重要度降序（搜索频率 + 景观分 + 类型加权），只返回最重要的前 limit 个
    along.sort(key=lambda p: (-p["importance"], p["distance_to_route_m"]))
    return along[:limit]


def _ensure_network():
    global _network_initialized
    G = get_network()
    if G is not None:
        return G, None

    try:
        G = load_or_download_network()
        _network_initialized = True
        return G, None
    except RuntimeError as e:
        return None, _err("network_load_failed", f"路网加载失败: {e}", 500)


def _weather_snapshot():
    """获取实时天气 + 影响标志（带 30 分钟缓存，失败返回 None 不影响主流程）。"""
    try:
        live = weather_mod.fetch_weather_live()
        if not live:
            return None
        impact = weather_mod.classify_weather(live)
        return {"live": live, "impact": impact}
    except Exception as e:
        logger.warning("天气快照获取失败: %s", e)
        return None


def _weather_public(snap):
    """将天气快照转为前端可用的精简结构（无数据返回 None）。"""
    if not snap:
        return None
    live, impact = snap["live"], snap["impact"]
    return {
        "weather": live.get("weather", ""),
        "temperature": live.get("temperature"),
        "windpower": live.get("windpower", ""),
        "slippery": impact["slippery"],
        "hot": impact["hot"],
        "low_visibility": impact["low_visibility"],
        "label": impact["label"],
        "advice": impact["advice"],
    }


def _agent_response_to_legacy(resp: dict, coord_start=None, coord_end=None) -> dict:
    """Agent 循环响应 → 前端兼容结构（P4 前端再按 response_kind 细分渲染）。

    response_kind:
      route      → task_type=path_planning，透传路径包（含 via/tour 扩展字段）
      candidates → task_type=candidates，携带候选 POI 列表（P4 渲染卡片）
      clarify    → task_type=unknown，message 为澄清问题，附 clarify.options
      chat       → task_type=chat，reply 为答复
    """
    kind = resp.get("response_kind", "chat")
    route = resp.get("route")

    if kind == "route" and route:
        out = {
            "task_type": "path_planning",
            "response_kind": kind,
            "route_kind": resp.get("route_kind", "direct"),
            "explanation": resp.get("message", ""),
            "start": {"name": route.get("start_name", ""), "type": "coord" if coord_start else "poi"},
            "end": {"name": route.get("end_name", ""), "type": "coord" if coord_end else "poi"},
        }
        # 坐标端点：透传原始 GPS 坐标（前端地图聚焦用）
        if coord_start:
            out["start"]["coordinates"] = {"lng": coord_start.get("lng"), "lat": coord_start.get("lat")}
        if coord_end:
            out["end"]["coordinates"] = {"lng": coord_end.get("lng"), "lat": coord_end.get("lat")}
        for k in ("recommended", "shortest", "pois", "filter_status", "overlap_rate",
                  "recommended_length_m", "shortest_length_m", "length_capped", "degraded",
                  "distance_m", "shortest_distance_m", "applied_weights", "mode",
                  "duration_min", "shortest_duration_min", "speed_kmh",
                  "legs", "via", "tour", "detour_ratio"):
            if k in route:
                out[k] = route[k]
        out.setdefault("shortest", out.get("recommended", []))
        out.setdefault("costs", {})
        if resp.get("suggestions"):
            out["suggestions"] = resp["suggestions"]
        else:
            out.setdefault("suggestions", [])
        return out

    if kind == "candidates":
        return {
            "task_type": "candidates",
            "response_kind": kind,
            "message": resp.get("message", ""),
            "candidates": resp.get("candidates") or [],
        }

    if kind == "clarify":
        clarify = resp.get("clarify") or {}
        return {
            "task_type": "unknown",
            "response_kind": kind,
            "message": clarify.get("question") or resp.get("message", ""),
            "clarify": clarify,
        }

    return {
        "task_type": "chat",
        "response_kind": kind,
        "query": "",
        "reply": resp.get("message", ""),
    }


@api_bp.route("/weather", methods=["GET"])
def weather():
    """GET /api/weather — 当前武汉实时天气及对步行/骑行的路况影响"""
    snap = _weather_snapshot()
    if not snap:
        return _err("weather_unavailable", "天气暂时获取不到", 503)
    return _ok({
        "weather": snap["live"]["weather"],
        "temperature": snap["live"]["temperature"],
        "humidity": snap["live"]["humidity"],
        "winddirection": snap["live"]["winddirection"],
        "windpower": snap["live"]["windpower"],
        "reporttime": snap["live"]["reporttime"],
        "slippery": snap["impact"]["slippery"],
        "hot": snap["impact"]["hot"],
        "low_visibility": snap["impact"]["low_visibility"],
        "label": snap["impact"]["label"],
        "advice": snap["impact"]["advice"],
    })


# ===== 管理员鉴权接口 =====
@api_bp.route("/admin/status", methods=["GET"])
def admin_status():
    """GET /api/admin/status — 是否已登录管理员"""
    return _ok({
        "is_admin": _is_admin(),
        "login_enabled": _admin_login_enabled(),
    })


@api_bp.route("/admin/login", methods=["POST"])
def admin_login():
    """POST /api/admin/login — 管理员登录 {password}"""
    if not _admin_login_enabled():
        return _err("admin_disabled", "管理员功能未配置", 403)
    body = request.get_json(silent=True) or {}
    password = str(body.get("password", "")).strip()
    if password == config.ROAD_CONDITION_ADMIN_PASSWORD:
        session["is_admin"] = True
        session.permanent = True
        return _ok({"is_admin": True})
    return _err("invalid_password", "密码错误", 401)


@api_bp.route("/admin/logout", methods=["POST"])
def admin_logout():
    """POST /api/admin/logout — 退出管理员登录"""
    session.pop("is_admin", None)
    return _ok({"is_admin": False})


@api_bp.route("/parse", methods=["POST"])
def parse():
    """POST /api/parse — 自然语言 → 任务意图

    入参 (二选一):
      1) NL 模式:  {"query": "...", "input_method": "nl"}
      2) 快捷按钮: {"start": {...}, "end": {...}, "mode": "...", "input_method": "shortcut"}

    出参:
      {task_type, start, end, constraints, weights, input_method, ambiguity}
    """
    body = request.get_json(silent=True)
    if body is None:
        return _err("invalid_json", "请求体必须为合法 JSON", 400)

    query = body.get("query")
    input_method = body.get("input_method", "nl")

    if input_method == "nl":
        if not query or not isinstance(query, str):
            return _err("missing_query", "NL 模式下 query 字段必填且为字符串", 400)
        if len(query) > 500:
            query = query[:500]
    elif input_method == "shortcut":
        start = body.get("start")
        end = body.get("end")
        mode = body.get("mode", "")
        if not start or not end:
            return _err("missing_endpoints", "快捷模式下 start 和 end 字段必填", 400)

        # 快捷按钮模式 → 直接构建结构化意图，不调 LLM（DEC-011 权重映射）
        preset = SHORTCUT_MODE_PRESETS.get(mode, SHORTCUT_MODE_PRESETS["distance_first"])

        return _ok({
            "task_type": "path_planning",
            "start": start,
            "end": end,
            "constraints": preset["constraints"],
            "weights": preset["weights"],
            # 出行方式：快捷按钮链路携带 travel_mode（步行/骑行/驾车），非法值兜底 walk
            "mode": normalize_mode(body.get("travel_mode")),
            "input_method": "shortcut",
            "ambiguity": None,
            "weight_source": "shortcut",
        })
    else:
        return _err("invalid_input_method", "input_method 必须为 'nl' 或 'shortcut'", 400)

    try:
        intent = parse_query(query)
        intent_data = intent.model_dump() if hasattr(intent, "model_dump") else intent.dict()
        return _ok(intent_data)
    except ValueError as e:
        return _err("parse_validation_error", str(e), 400)
    except Exception as e:
        logger.exception("NL 解析失败")
        return _err("parse_failed", f"NL 解析失败: {e}", 500)


@api_bp.route("/route", methods=["POST"])
def route():
    """POST /api/route — 任务意图 → 多因素路径规划

    入参:
      {
        "task_type": "path_planning",
        "start": {"name": "...", "type": "poi"},
        "end": {"name": "...", "type": "poi"},
        "constraints": {"distance": "...", "slope": "...", "scenery": "..."},
        "weights": {"distance": 0.5, "slope": 0.2, "scenery": 0.3} | null
      }

    出参:
      {
        recommended: [{node_id, lng, lat}, ...],
        shortest: [{node_id, lng, lat}, ...],
        costs: {distance, slope, scenery},
        pois: [...],
        filter_status: str,
        overlap_rate: float,
        recommended_length_m: float,
        shortest_length_m: float,
        length_capped: bool,
        degraded: bool,
        explanation: str
      }
    """
    body = request.get_json(silent=True)
    if body is None:
        return _err("invalid_json", "请求体必须为合法 JSON", 400)

    start = body.get("start")
    end = body.get("end")
    if not start or not end:
        return _err("missing_endpoints", "start 和 end 字段必填", 400)

    start_name = start.get("name")
    end_name = end.get("name")
    if not start_name or not end_name:
        return _err("missing_poi_names", "start.name 和 end.name 必填", 400)

    G, err = _ensure_network()
    if err:
        return err

    start_poi = get_poi(start_name)
    if start_poi is None:
        return _err("poi_not_found", f"起点 '{start_name}' 未找到", 404)

    end_poi = get_poi(end_name)
    if end_poi is None:
        return _err("poi_not_found", f"终点 '{end_name}' 未找到", 404)

    if start_poi["name"] == end_poi["name"]:
        return _err("same_poi", "起点和终点相同，请选择不同的地点", 400)

    # 出行方式：优先 body.travel_mode；兼容 /api/parse 返回体里的 mode 字段
    # （注意与快捷预设 distance_first 等区分：只有值在 TRAVEL_MODES 内才采纳）
    body_mode = body.get("travel_mode")
    if body_mode is None and body.get("mode") in TRAVEL_MODES:
        body_mode = body.get("mode")
    final_mode = _resolve_travel_mode(query=None, body_mode=body_mode, intent_mode=None)

    # 按出行方式过滤路网：snap 用过滤后的图（驾车吸附到最近车行节点）；
    # 坐标展开/沿途 POI 仍用原图 G（副本节点 id 与 geometry 一致）
    G_mode, _mode_status, _mode_penalty = _mode_filtered_graph(G, final_mode)

    # GCJ-02 → WGS-84：POI 坐标来自高德，路网用 WGS-84（DEC-007）
    start_lon_wgs, start_lat_wgs = gcj02_to_wgs84(start_poi["lon"], start_poi["lat"])
    end_lon_wgs, end_lat_wgs = gcj02_to_wgs84(end_poi["lon"], end_poi["lat"])

    try:
        start_node = get_nearest_node(G_mode, start_lon_wgs, start_lat_wgs)
    except RuntimeError as e:
        return _err("nearest_node_failed", f"起点最近节点查找失败: {e}", 500)

    try:
        end_node = get_nearest_node(G_mode, end_lon_wgs, end_lat_wgs)
    except RuntimeError as e:
        return _err("nearest_node_failed", f"终点最近节点查找失败: {e}", 500)

    constraints = body.get("constraints", {})
    # 智能默认：无显式 weights 时根据终点 POI 类型选权重
    raw_weights = body.get("weights")
    weights = _weights_for_destination(end_poi, final_mode, explicit_weights=raw_weights)
    resolved_weights = resolve_weights(weights)

    # 实时天气：雨雪天自动避陡坡、高温倾向树荫（失败不影响规划）
    weather_snap = _weather_snapshot()
    weather_info = weather_snap["live"] if weather_snap else None

    try:
        route_result = compute_route(
            G=G,
            start_node=start_node,
            end_node=end_node,
            constraints=constraints,
            weights=weights,
            mode=final_mode,
            weather_info=weather_info,
        )
    except ValueError as e:
        # 如驾车不可达："驾车无法到达…建议切换骑行或步行"，消息原样透传给前端
        return _err("route_not_found", str(e), 404)
    except Exception as e:
        logger.exception("路径计算异常")
        return _err("route_computation_failed", f"路径计算失败: {e}", 500)

    recommended_nodes = route_result["recommended"]
    shortest_nodes = route_result["shortest"]

    recommended_coords = _path_to_coords(G, recommended_nodes)
    shortest_coords = _path_to_coords(G, shortest_nodes)

    costs = _compute_route_costs(G, recommended_nodes, resolved_weights, route_result.get("max_len", 0.0))

    pois_along = _find_pois_along_route(G, recommended_nodes)

    # WGS-84 → GCJ-02：路网路径坐标转成高德坐标系再返回前端
    # 注意：pois_along 里的 POI 本身来自 pois.json(GCJ-02)，不需要再转！
    recommended_coords = _coords_wgs_to_gcj(recommended_coords)
    shortest_coords = _coords_wgs_to_gcj(shortest_coords)

    response = {
        "recommended": recommended_coords,
        "shortest": shortest_coords,
        "costs": costs,
        "pois": pois_along,
        "filter_status": route_result["filter_status"],
        "overlap_rate": route_result["overlap_rate"],
        "recommended_length_m": route_result["recommended_length_m"],
        "shortest_length_m": route_result["shortest_length_m"],
        "length_capped": route_result["length_capped"],
        "degraded": route_result["degraded"],
        "distance_m": route_result["recommended_length_m"],
        "shortest_distance_m": route_result["shortest_length_m"],
        "applied_weights": route_result.get("applied_weights", resolved_weights),
        "degraded_count": route_result.get("degraded_count", 0),
        # 出行方式与预计用时
        "mode": route_result["mode"],
        "duration_min": route_result["duration_min"],
        "shortest_duration_min": route_result["shortest_duration_min"],
        "speed_kmh": route_result["speed_kmh"],
        "road_conditions_applied": route_result.get("road_conditions_applied", 0),
        "weather_applied": route_result.get("weather_applied", False),
        "weather": _weather_public(weather_snap),
    }

    return _ok(response)


@api_bp.route("/chat", methods=["POST"])
def chat():
    """POST /api/chat — 一站式 NL → 完整流程（解析 + 路径 + 解释）

    入参:
      {
        "query": "我第一次来武大，想看樱花...",
        "context": {...}  // 可选，多轮对话上下文
      }

    出参:
      {
        "task_type": "...",
        "start": {...},
        "end": {...},
        "constraints": {...},
        "weights": {...},
        "recommended": [...],
        "shortest": [...],
        "costs": {...},
        "pois": [...],
        "explanation": "...",
        "filter_status": "...",
        "overlap_rate": 0.35,
        ...
      }
    """
    body = request.get_json(silent=True)
    if body is None:
        return _err("invalid_json", "请求体必须为合法 JSON", 400)

    query = body.get("query")
    if not query or not isinstance(query, str):
        return _err("missing_query", "query 字段必填且为字符串", 400)
    if len(query) > 500:
        query = query[:500]

    context = body.get("context")

    # v2 全 Agent 架构：所有输入优先进入 Agent 循环（LLM 决策 + 工具执行）。
    # LLM 本身故障（断网/鉴权/超时）时落回旧管道——停电保险，不是备用通道。
    try:
        from agents.planner import run_agent
        agent_resp = run_agent(
            query,
            context=context,
            coord_start=body.get("coord_start"),
            coord_end=body.get("coord_end"),
            uid=body.get("whu_uid") or body.get("uid"),
            travel_mode=body.get("travel_mode"),
            coord_waypoints=body.get("coord_waypoints"),
        )
        return _ok(_agent_response_to_legacy(agent_resp,
                    coord_start=body.get("coord_start"),
                    coord_end=body.get("coord_end")))
    except Exception as e:
        logger.warning("Agent 规划器不可用（%s: %s），落回旧管道", type(e).__name__, e)

    try:
        intent = parse_query(query, context)
        # GPS 坐标覆盖（WGS-84；前端「从我这/到我这」触发），必须在 model_dump 前完成
        _apply_coord_override(intent, body.get("coord_start"), body.get("coord_end"))
        intent_data = intent.model_dump() if hasattr(intent, "model_dump") else intent.dict()
    except ValueError as e:
        return _err("parse_validation_error", str(e), 400)
    except Exception as e:
        logger.exception("NL 解析失败")
        return _err("parse_failed", f"NL 解析失败: {e}", 500)

    task_type = intent_data.get("task_type")

    # poi_query → 返回景点详情
    if task_type == "poi_query":
        start = intent_data.get("start")
        poi_name = start.get("name") if start else None
        if poi_name:
            poi, alts = find_poi_ambiguous(poi_name)
            if poi:
                desc = (poi.get("description") or "").strip()
                message = desc if desc else f"这是 {poi['name']} 的信息～"
                return _ok({
                    "task_type": "poi_query",
                    "poi": poi,
                    "message": message,
                    # 供前端多轮上下文保存：问「X在哪」后，下一轮「从A怎么去」可把 X 当作终点承接
                    "start": start,
                })
            guidance = generate_poi_guidance(query, poi_name, alternatives=alts)
        else:
            guidance = generate_poi_guidance(query, poi_name)
        return _ok({
            "task_type": "unknown",
            "message": guidance,
            "example_queries": ["樱顶在哪", "从珞珈门到樱顶"],
        })

    # help → 返回功能介绍
    if task_type == "help":
        return _ok({
            "task_type": "help",
            "message": "我可以帮你规划武大校园路线、查询景点、回答校园问题～",
            "features": ["路径规划（支持避开陡坡/风景优先/最短路径）", "景点查询与介绍", "校园生活问答"],
            "example_queries": ["从珞珈门到樱顶，避开陡坡", "樱花开了吗", "哪个食堂好吃"],
        })

    # unknown → 返回引导
    if task_type == "unknown":
        return _ok({
            "task_type": "unknown",
            "message": intent_data.get("ambiguity", "抱歉，我只能回答武大校园相关的问题哦～"),
            "example_queries": ["从珞珈门到樱顶", "樱顶在哪", "樱花开了吗"],
        })

    # chat → 调用 LLM 闲聊回复
    if task_type == "chat":
        chat_reply = generate_chat_response(query)
        return _ok({
            "task_type": "chat",
            "query": query,
            "reply": chat_reply,
        })

    # path_planning → 继续现有逻辑
    if task_type != "path_planning":
        return _err("unsupported_task", f"暂不支持的任务类型: {task_type}", 400)

    start = intent_data.get("start")
    end = intent_data.get("end")
    if not start or not end:
        # 缺起终点不是"错误"，而是信息不完整——用对话式引导，而非报错弹窗
        if not start and not end:
            guide = "想从哪走到哪呢？告诉我起点和终点，我就能帮你规划啦～比如「从珞珈门到樱顶」😊"
            ambiguity = "请指定起点和终点"
        elif not start:
            guide = "从哪出发呢？告诉我起点就好啦～比如「从珞珈门出发」"
            ambiguity = "请指定起点"
        else:
            guide = "要去哪儿呢？告诉我目的地，我帮你规划路线～比如「到樱顶」"
            ambiguity = "请指定终点"
        return _ok({
            "task_type": "unknown",
            "message": guide,
            "start": start,
            "end": end,
            "constraints": intent_data.get("constraints", {}),
            "weights": intent_data.get("weights"),
            "ambiguity": ambiguity,
            "example_queries": ["从珞珈门到樱顶", "从教五到总图书馆", "去樱顶"],
        })

    G, err = _ensure_network()
    if err:
        return err

    # type="coord"（GPS「我的位置」）直接使用坐标，不走 POI 模糊匹配；
    # type="poi" 保持名称解析 + 多候选消歧引导
    if start.get("type") == "coord" and start.get("coordinates"):
        start_poi, start_alts = {"name": start.get("name") or "我的位置"}, []
    else:
        start_poi, start_alts = find_poi_ambiguous(start.get("name"))
        if start_poi is None:
            guidance = generate_poi_guidance(query, start.get("name"), alternatives=start_alts)
            return _ok({
                "task_type": "unknown",
                "message": guidance,
                "start": start,
                "end": end,
                "ambiguity": "请指定起点",
                "example_queries": ["从牌坊出发"],
            })

    if end.get("type") == "coord" and end.get("coordinates"):
        end_poi, end_alts = {"name": end.get("name") or "我的位置"}, []
    else:
        end_poi, end_alts = find_poi_ambiguous(end.get("name"))
        if end_poi is None:
            guidance = generate_poi_guidance(query, end.get("name"), alternatives=end_alts)
            return _ok({
                "task_type": "unknown",
                "message": guidance,
                "start": start,
                "end": end,
                "ambiguity": "请指定终点",
                "example_queries": ["到樱顶"],
            })

    # 出行方式优先级：NL 显式关键词（骑车/开车/步行…）> body.travel_mode > intent.mode（含上下文继承）> walk
    final_mode = _resolve_travel_mode(
        query=query,
        body_mode=body.get("travel_mode"),
        intent_mode=intent_data.get("mode"),
    )

    # 按出行方式过滤路网：snap 用过滤后的图（驾车吸附到最近车行节点）；
    # 坐标展开/沿途 POI 仍用原图 G（副本节点 id 与 geometry 一致）
    G_mode, _mode_status, _mode_penalty = _mode_filtered_graph(G, final_mode)

    # 坐标统一到 WGS-84：GPS coord 本身即 WGS-84；POI 为 GCJ-02 需转换
    start_lon_wgs, start_lat_wgs = _endpoint_to_wgs(start, start_poi)
    end_lon_wgs, end_lat_wgs = _endpoint_to_wgs(end, end_poi)

    # 起终点重合判定：含坐标时看球面距离（<10m 视为同点）；POI-POI 看规范名
    if start.get("type") == "coord" or end.get("type") == "coord":
        if _haversine(start_lat_wgs, start_lon_wgs, end_lat_wgs, end_lon_wgs) < 10.0:
            return _err("same_poi", "起点和终点距离太近，换一个目的地试试", 400)
    elif start_poi.get("name") == end_poi.get("name"):
        return _err("same_poi", "起点和终点相同，请选择不同的地点", 400)

    try:
        start_node = get_nearest_node(G_mode, start_lon_wgs, start_lat_wgs)
    except RuntimeError as e:
        return _err("nearest_node_failed", f"起点最近节点查找失败: {e}", 500)

    try:
        end_node = get_nearest_node(G_mode, end_lon_wgs, end_lat_wgs)
    except RuntimeError as e:
        return _err("nearest_node_failed", f"终点最近节点查找失败: {e}", 500)

    constraints = intent_data.get("constraints", {})
    # 智能默认：LLM 未输出显式 weights 时，根据终点 POI 类型选权重
    raw_weights = intent_data.get("weights")
    weights = _weights_for_destination(end_poi, final_mode, explicit_weights=raw_weights)

    # 实时天气：雨雪天自动避陡坡、高温倾向树荫（失败不影响规划）
    weather_snap = _weather_snapshot()
    weather_info = weather_snap["live"] if weather_snap else None

    try:
        route_result = compute_route(
            G=G,
            start_node=start_node,
            end_node=end_node,
            constraints=constraints,
            weights=weights,
            mode=final_mode,
            weather_info=weather_info,
        )
    except ValueError as e:
        # 如驾车不可达："驾车无法到达…建议切换骑行或步行"，消息原样透传给前端
        return _err("route_not_found", str(e), 404)
    except Exception as e:
        logger.exception("路径计算异常")
        return _err("route_computation_failed", f"路径计算失败: {e}", 500)

    recommended_nodes = route_result["recommended"]
    shortest_nodes = route_result["shortest"]
    resolved_weights = resolve_weights(weights)

    recommended_coords = _path_to_coords(G, recommended_nodes)
    shortest_coords = _path_to_coords(G, shortest_nodes)

    costs = _compute_route_costs(G, recommended_nodes, resolved_weights, route_result.get("max_len", 0.0))

    pois_along = _find_pois_along_route(G, recommended_nodes)

    route_data_for_explainer = {
        "distance_m": route_result["recommended_length_m"],
        "shortest_distance_m": route_result["shortest_length_m"],
        "costs": costs,
        "pois": pois_along,
        "filter_status": route_result["filter_status"],
        "mode": final_mode,
        "duration_min": route_result["duration_min"],
    }

    explanation = ""
    try:
        weight_source = intent_data.get("weight_source")
        explanation = generate_explanation(route_data_for_explainer, constraints, weights, weight_source)
    except Exception:
        explanation = "已为您规划好路线。"

    # 天气影响提示：把真实天气对路线的调整说清楚（湿滑避坡/高温走树荫）
    weather_pub = _weather_public(weather_snap)
    if weather_pub and weather_pub.get("advice"):
        explanation = (explanation + " " + weather_pub["advice"]).strip()

    # 跟进建议（"可能想问"）：LLM 根据对话上下文动态生成，失败则空列表（前端兜底）
    suggestions = []
    try:
        suggestions = generate_suggestions(query, route_data_for_explainer, constraints, weights)
    except Exception as e:
        logger.warning("跟进建议生成异常: %s", e)

    result = {
        "task_type": intent_data.get("task_type"),
        "start": start,
        "end": end,
        "constraints": constraints,
        "weights": weights,
        # WGS-84 → GCJ-02：路网路径坐标转成高德坐标系再返回前端
        # 注意：pois_along 里的 POI 本身来自 pois.json(GCJ-02)，不需要再转！
        "recommended": _coords_wgs_to_gcj(recommended_coords),
        "shortest": _coords_wgs_to_gcj(shortest_coords),
        "costs": costs,
        "pois": pois_along,
        "filter_status": route_result["filter_status"],
        "overlap_rate": route_result["overlap_rate"],
        "recommended_length_m": route_result["recommended_length_m"],
        "shortest_length_m": route_result["shortest_length_m"],
        "length_capped": route_result["length_capped"],
        "degraded": route_result["degraded"],
        "distance_m": route_result["recommended_length_m"],
        "shortest_distance_m": route_result["shortest_length_m"],
        "applied_weights": route_result.get("applied_weights", resolved_weights),
        "degraded_count": route_result.get("degraded_count", 0),
        # 出行方式与预计用时
        "mode": final_mode,
        "duration_min": route_result["duration_min"],
        "shortest_duration_min": route_result["shortest_duration_min"],
        "speed_kmh": route_result["speed_kmh"],
        "road_conditions_applied": route_result.get("road_conditions_applied", 0),
        "weather_applied": route_result.get("weather_applied", False),
        "weather": weather_pub,
        "explanation": explanation,
        "suggestions": suggestions,
    }
    return _ok(result)


@api_bp.route("/pois", methods=["GET"])
def list_pois():
    """GET /api/pois — POI 列表

    Query 参数:
      type     — 按类型筛选 (landmark/scenery/study)
      season   — 按季节标签筛选 (spring/summer/autumn/winter)
      keyword  — 关键词模糊搜索

    出参:
      {pois: [...]}
    """
    poi_type = request.args.get("type")
    season = request.args.get("season")
    keyword = request.args.get("keyword", "").strip()

    try:
        if keyword:
            pois = search_pois(keyword, poi_type=poi_type, season=season)
        else:
            pois = list_all_pois(poi_type=poi_type, season=season)
        return _ok({"pois": pois})
    except Exception as e:
        logger.exception("POI 列表获取失败")
        return _err("poi_list_failed", f"POI 列表获取失败: {e}", 500)


@api_bp.route("/pois/<name>", methods=["GET"])
def get_single_poi(name):
    """GET /api/pois/<name> — 单 POI 查询（支持模糊匹配）

    出参:
      {poi: {...}}
    """
    if not name or len(name) > 100:
        return _err("invalid_poi_name", "POI 名称参数不合法", 400)

    try:
        poi = get_poi(name, fuzzy=True)
        if poi is None:
            return _err("poi_not_found", f"未找到匹配 '{name}' 的 POI", 404)
        return _ok({"poi": poi})
    except Exception as e:
        logger.exception("POI 查询失败")
        return _err("poi_query_failed", f"POI 查询失败: {e}", 500)


@api_bp.route("/network/init", methods=["POST"])
def network_init():
    """POST /api/network/init — 触发路网加载

    首次调用可能需要下载 OSM 数据（约 30 秒），后续从缓存加载（< 1 秒）。

    出参:
      {status, nodes, edges, cached}
    """
    global _network_initialized

    body = request.get_json(silent=True) or {}
    force = body.get("force", False)

    try:
        if force:
            from spatial.network import reload_network
            G = reload_network()
            _network_initialized = True
            return _ok({
                "status": "loaded",
                "nodes": G.number_of_nodes(),
                "edges": G.number_of_edges(),
                "cached": False,
            })

        G = get_network()
        if G is not None:
            _network_initialized = True
            return _ok({
                "status": "already_loaded",
                "nodes": G.number_of_nodes(),
                "edges": G.number_of_edges(),
                "cached": True,
            })

        from config import ROAD_NETWORK_CACHE
        cache_existed = os.path.exists(ROAD_NETWORK_CACHE)

        G = load_or_download_network()
        _network_initialized = True
        return _ok({
            "status": "loaded",
            "nodes": G.number_of_nodes(),
            "edges": G.number_of_edges(),
            "cached": cache_existed,
        })
    except RuntimeError as e:
        logger.exception("路网加载失败")
        return _err("network_load_failed", str(e), 500)
    except Exception as e:
        logger.exception("路网初始化异常")
        return _err("network_init_failed", f"路网初始化失败: {e}", 500)


# ===========================================================================
# T-022: 场景 3 候选 POI 交互 — POST /api/candidates
# ===========================================================================
@api_bp.route("/candidates", methods=["POST"])
def candidates():
    """POST /api/candidates — scenario 3: find candidate POIs by type near a start point.

    Input: {"start": {"name": "珞珈门"}, "poi_type": "scenery", "keyword": "樱花"}
    Output: {"candidates": [{"name": "...", "type": "...", "scenery_score": N, "distance_m": N}, ...]}
    """
    body = request.get_json(silent=True)
    if body is None:
        return _err("invalid_json", "请求体必须为合法 JSON", 400)

    start = body.get("start")
    if not start or not start.get("name"):
        return _err("missing_start", "start.name 必填", 400)

    start_poi = get_poi(start.get("name"))
    if start_poi is None:
        return _err("poi_not_found", f"起点 '{start.get('name')}' 未找到", 404)

    poi_type = body.get("poi_type")
    keyword = body.get("keyword", "").strip()

    # Get all POIs matching type
    all_pois = list_all_pois(poi_type=poi_type if poi_type else None)

    # Filter by keyword if provided, exclude start POI
    candidates = []
    for poi in all_pois:
        if poi["name"] == start.get("name"):
            continue
        if keyword and keyword not in poi.get("name", "") and keyword not in poi.get("description", ""):
            continue
        candidates.append(poi)

    if not candidates:
        return _err(
            "no_candidates",
            f"未找到匹配的候选POI（类型={poi_type}, 关键词={keyword}）",
            404,
        )

    # Calculate road-network distance from start to each candidate
    G, err = _ensure_network()
    if err:
        # Fallback: use haversine distance if network not loaded
        start_lat = start_poi["lat"]
        start_lon = start_poi["lon"]
        for c in candidates:
            c_lat = c.get("lat", 0)
            c_lon = c.get("lon", 0)
            dist = _haversine(start_lat, start_lon, c_lat, c_lon)
            c["distance_m"] = round(dist, 1)
            c["scenery_score"] = c.get("scenery_score", 3)
    else:
        start_lon_wgs, start_lat_wgs = gcj02_to_wgs84(start_poi["lon"], start_poi["lat"])
        try:
            start_node = get_nearest_node(G, start_lon_wgs, start_lat_wgs)
        except Exception:
            start_node = None

        for c in candidates:
            c_lat = c.get("lat", 0)
            c_lon = c.get("lon", 0)
            if start_node is not None:
                try:
                    c_lon_wgs, c_lat_wgs = gcj02_to_wgs84(c_lon, c_lat)
                    end_node = get_nearest_node(G, c_lon_wgs, c_lat_wgs)
                    try:
                        path = nx.dijkstra_path(G, start_node, end_node, weight="length")
                        dist = _path_length(G, path)
                        c["distance_m"] = round(dist, 1)
                    except Exception:
                        dist = _haversine(start_poi["lat"], start_poi["lon"], c_lat, c_lon)
                        c["distance_m"] = round(dist, 1)
                except Exception:
                    dist = _haversine(start_poi["lat"], start_poi["lon"], c_lat, c_lon)
                    c["distance_m"] = round(dist, 1)
            else:
                dist = _haversine(start_poi["lat"], start_poi["lon"], c_lat, c_lon)
                c["distance_m"] = round(dist, 1)
            c["scenery_score"] = c.get("scenery_score", 3)

    # Sort by scenery_score desc, then distance asc
    candidates.sort(key=lambda p: (-p.get("scenery_score", 3), p.get("distance_m", 9999)))

    # Return top 5
    result = []
    for c in candidates[:5]:
        result.append({
            "name": c["name"],
            "type": c.get("type", "landmark"),
            "scenery_score": c.get("scenery_score", 3),
            "distance_m": c.get("distance_m", 0),
            "description": c.get("description", "")[:80],
        })

    return _ok({"candidates": result, "start": start})


# ===================== 路况管理 =====================

def _parse_time_input(value):
    """把时间入参解析为 Unix 时间戳（秒）。

    支持：None/空串/0 → None；数字 → 秒级时间戳；
    ISO 字符串（如 "2026-09-10T08:00"）→ 本地时间时间戳。
    """
    if value is None or value == "" or value == 0:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            pass
        from datetime import datetime
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            raise ValueError(f"时间格式无法识别: {value}（请用 YYYY-MM-DDTHH:MM 或时间戳）")
    raise ValueError(f"时间参数类型无效: {type(value)}")


@api_bp.route("/road-conditions", methods=["GET"])
def get_road_conditions():
    """GET /api/road-conditions — 返回当前生效的路况事件列表

    管理员（session 或 Token）带 ?all=1 时返回全部事件（含未开始/已过期），并附加 status。
    """
    include_all = request.args.get("all") in ("1", "true", "yes")
    is_admin = _is_admin() if include_all else False
    conditions = list_conditions(include_inactive=include_all and is_admin)
    now = time.time()
    for c in conditions:
        c["type_label"] = CONDITION_LABELS.get(c["type"], c["type"])
        if include_all and is_admin:
            start = c.get("start_time", 0) or 0
            end = c.get("end_time", 0) or 0
            if end and now > end:
                c["status"] = "expired"
            elif start and now < start:
                c["status"] = "scheduled"
            else:
                c["status"] = "active"
    return _ok({"conditions": conditions, "count": len(conditions)})


@api_bp.route("/road-conditions/snap", methods=["GET"])
def snap_road_condition():
    """GET /api/road-conditions/snap?lng=&lat= — 管理员选点预览：把点击点吸附到最近路段。

    返回边标识、吸附点(GCJ-02)、道路名、距离与边几何，供前端把标记移到路上并画线。
    """
    if _require_admin():
        return _err("unauthorized", "需要管理员权限，请先登录", 401)
    try:
        lng = float(request.args.get("lng"))
        lat = float(request.args.get("lat"))
    except (TypeError, ValueError):
        return _err("invalid_lnglat", "lng/lat 必须是数字", 400)

    G, err = _ensure_network()
    if err:
        return err
    snap = snap_to_edge(G, lng, lat)
    if snap is None:
        return _err(
            "too_far_from_road",
            f"点击位置离最近道路超过 {int(SNAP_MAX_DIST_M)} 米，请放大地图点在道路上",
            400,
        )
    return _ok({"snap": snap, "max_dist_m": SNAP_MAX_DIST_M})


@api_bp.route("/road-conditions", methods=["POST"])
def create_road_condition():
    """POST /api/road-conditions — 新增路况事件（需管理员 session 或 Token）

    事件由服务端吸附到最近路段，不接受"半径"参数：
    Input: {
        "type": "closure|construction|event|flooding|accident",
        "name": "樱花大道施工",
        "lng": 114.365,          # 管理员点击点（GCJ-02）
        "lat": 30.536,
        "description": "施工期间禁止通行",
        "start_time": "2026-09-10T08:00",  # 可选，缺省=立即生效
        "end_time": "2026-09-12T18:00"     # 可选，缺省=长期有效
    }
    """
    identity = _admin_identity()
    if identity is None:
        return _err("unauthorized", "需要管理员权限，请先登录", 401)

    body = request.get_json(silent=True)
    if body is None:
        return _err("invalid_json", "请求体必须为合法 JSON", 400)

    cond_type = body.get("type")
    name = (body.get("name") or "").strip()
    lng = body.get("lng")
    lat = body.get("lat")

    if not cond_type or not name or lng is None or lat is None:
        return _err("missing_fields", "type, name, lng, lat 必填", 400)
    if cond_type not in CONDITION_EFFECTS:
        return _err("invalid_type", f"未知路况类型: {cond_type}", 400)

    try:
        lng_f, lat_f = float(lng), float(lat)
        start_ts = _parse_time_input(body.get("start_time"))
        end_ts = _parse_time_input(body.get("end_time"))
        if start_ts and end_ts and end_ts <= start_ts:
            return _err("invalid_time", "结束时间必须晚于开始时间", 400)

        G, err = _ensure_network()
        if err:
            return err
        # 服务端重新吸附，不信任前端坐标
        snap = snap_to_edge(G, lng_f, lat_f)
        if snap is None:
            return _err(
                "too_far_from_road",
                f"点击位置离最近道路超过 {int(SNAP_MAX_DIST_M)} 米，请放大地图点在道路上",
                400,
            )

        condition = add_condition(
            cond_type=cond_type,
            name=name[:30],
            edge=snap,
            click_point={"lng": lng_f, "lat": lat_f},
            description=(body.get("description") or "").strip()[:200],
            start_time=start_ts,
            end_time=end_ts,
            created_by=identity if identity == "web" else "token",
        )
        condition["type_label"] = CONDITION_LABELS.get(cond_type, cond_type)
        return _ok({"condition": condition, "snap": snap}, status=201)
    except ValueError as e:
        return _err("invalid_field", str(e), 400)
    except Exception as e:
        logger.exception("新增路况失败")
        return _err("create_failed", f"新增失败: {e}", 500)


@api_bp.route("/road-conditions/<cond_id>", methods=["PATCH"])
def patch_road_condition(cond_id):
    """PATCH /api/road-conditions/<id> — 更新路况（需管理员）

    支持：
      {"action": "end"}                         立即结束（end_time=now，保留记录可审计）
      {"name": "..."} / {"description": "..."}  改名/补充描述
      {"start_time": ..., "end_time": ...}      调整生效时段（ISO 或时间戳）
    """
    if _require_admin():
        return _err("unauthorized", "需要管理员权限，请先登录", 401)
    body = request.get_json(silent=True) or {}

    changes = {}
    if body.get("action") == "end":
        changes["end_time"] = time.time()
    else:
        for field in ("name", "description"):
            if body.get(field) is not None:
                changes[field] = str(body[field])[:200]
        start_ts = _parse_time_input(body.get("start_time")) if body.get("start_time") is not None else None
        end_ts = _parse_time_input(body.get("end_time")) if body.get("end_time") is not None else None
        if start_ts is not None:
            changes["start_time"] = start_ts
        if end_ts is not None:
            changes["end_time"] = end_ts

    if not changes:
        return _err("empty_changes", "没有可更新的字段", 400)

    updated = update_condition(cond_id, changes)
    if updated is None:
        return _err("not_found", f"路况事件 {cond_id} 不存在", 404)
    updated["type_label"] = CONDITION_LABELS.get(updated.get("type"), updated.get("type"))
    return _ok({"condition": updated})


@api_bp.route("/road-conditions/<cond_id>", methods=["DELETE"])
def delete_road_condition(cond_id):
    """DELETE /api/road-conditions/<id> — 删除路况事件（需管理员 session 或 Token）"""
    if _require_admin():
        return _err("unauthorized", "需要管理员权限，请先登录", 401)
    success = remove_condition(cond_id)
    if not success:
        return _err("not_found", f"路况事件 {cond_id} 不存在", 404)
    return _ok({"message": "已删除", "id": cond_id})


# ===== 行为埋点（P4：用户画像学习 + 产品观测）=====

_TELEMETRY_PATH = Path(__file__).parent.parent / "data" / "telemetry.jsonl"
_TELEMETRY_EVENTS = {
    "route_shown", "route_accept", "candidate_click",
    "clarify_answer", "chat", "error",
}


@api_bp.route("/telemetry", methods=["POST"])
def telemetry():
    """POST /api/telemetry — 前端行为埋点。

    入参: {"uid": "whu_uid", "event": "route_accept", ...事件字段}
    - route_shown:  路径曝光（记 exposure，不学权重）
    - route_accept: 路径采纳（EMA 更新用户画像，需带 applied_weights）
    所有事件追加写入 data/telemetry.jsonl 供离线分析。
    埋点是锦上添花：任何失败都返回 ok，绝不影响前端主流程。
    """
    body = request.get_json(silent=True) or {}
    uid = body.get("uid") or body.get("whu_uid")
    event = body.get("event")

    if not uid or event not in _TELEMETRY_EVENTS:
        return _ok({"recorded": False})

    record = {
        "ts": time.time(),
        "uid": uid,
        "event": event,
        "payload": {k: v for k, v in body.items() if k not in ("uid", "whu_uid", "event")},
    }
    try:
        with open(_TELEMETRY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("埋点写入失败: %s", e)

    # 画像学习：曝光/采纳反馈
    try:
        from agents import profile
        weights = body.get("applied_weights")
        if event == "route_shown":
            profile.record_route_feedback(uid, weights, accepted=False)
        elif event == "route_accept":
            profile.record_route_feedback(uid, weights, accepted=True)
    except Exception as e:
        logger.warning("画像更新失败: %s", e)

    return _ok({"recorded": True})