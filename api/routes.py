"""
漫步珞珈 (WHU-Walker) — RESTful API 路由

6 个端点:
  POST /api/parse   — 自然语言 → 结构化任务意图
  POST /api/route   — 任务意图 → 多因素路径规划
  POST /api/chat    — 一站式 NL → 解析 + 路径 + 解释
  GET  /api/pois   — POI 列表（支持 type/season 筛选）
  GET  /api/pois/<name> — 单 POI 查询
  POST /api/network/init — 触发路网加载
"""
import logging
import math
import os
import re

import networkx as nx
from flask import Blueprint, request, jsonify

from agents.parser import parse_query
from agents.explainer import generate_explanation, generate_chat_response, generate_suggestions, generate_poi_guidance
from spatial.poi import get_poi, search_pois, list_all_pois, load_pois, find_poi_ambiguous, importance_score
from spatial.network import get_network, load_or_download_network, get_nearest_node, get_node_coords
from spatial.routing import compute_route, resolve_weights, _path_length
from spatial.coord_transform import gcj02_to_wgs84, wgs84_to_gcj02

logger = logging.getLogger(__name__)

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

    # GCJ-02 → WGS-84：POI 坐标来自高德，路网用 WGS-84（DEC-007）
    start_lon_wgs, start_lat_wgs = gcj02_to_wgs84(start_poi["lon"], start_poi["lat"])
    end_lon_wgs, end_lat_wgs = gcj02_to_wgs84(end_poi["lon"], end_poi["lat"])

    try:
        start_node = get_nearest_node(G, start_lon_wgs, start_lat_wgs)
    except RuntimeError as e:
        return _err("nearest_node_failed", f"起点最近节点查找失败: {e}", 500)

    try:
        end_node = get_nearest_node(G, end_lon_wgs, end_lat_wgs)
    except RuntimeError as e:
        return _err("nearest_node_failed", f"终点最近节点查找失败: {e}", 500)

    constraints = body.get("constraints", {})
    weights = body.get("weights")
    resolved_weights = resolve_weights(weights)

    try:
        route_result = compute_route(
            G=G,
            start_node=start_node,
            end_node=end_node,
            constraints=constraints,
            weights=weights,
        )
    except ValueError as e:
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
    try:
        intent = parse_query(query, context)
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
            "example_queries": ["樱顶在哪", "从牌坊到樱顶"],
        })

    # help → 返回功能介绍
    if task_type == "help":
        return _ok({
            "task_type": "help",
            "message": "我可以帮你规划武大校园路线、查询景点、回答校园问题～",
            "features": ["路径规划（支持避开陡坡/风景优先/最短路径）", "景点查询与介绍", "校园生活问答"],
            "example_queries": ["从牌坊到樱顶，避开陡坡", "樱花开了吗", "哪个食堂好吃"],
        })

    # unknown → 返回引导
    if task_type == "unknown":
        return _ok({
            "task_type": "unknown",
            "message": intent_data.get("ambiguity", "抱歉，我只能回答武大校园相关的问题哦～"),
            "example_queries": ["从牌坊到樱顶", "樱顶在哪", "樱花开了吗"],
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
            guide = "想从哪走到哪呢？告诉我起点和终点，我就能帮你规划啦～比如「从牌坊到樱顶」😊"
            ambiguity = "请指定起点和终点"
        elif not start:
            guide = "从哪出发呢？告诉我起点就好啦～比如「从牌坊出发」"
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
            "example_queries": ["从牌坊到樱顶", "从教五到总图书馆", "去樱顶"],
        })

    start_name = start.get("name")
    end_name = end.get("name")

    G, err = _ensure_network()
    if err:
        return err

    start_poi, start_alts = find_poi_ambiguous(start_name)
    if start_poi is None:
        guidance = generate_poi_guidance(query, start_name, alternatives=start_alts)
        return _ok({
            "task_type": "unknown",
            "message": guidance,
            "start": start,
            "end": end,
            "ambiguity": "请指定起点",
            "example_queries": ["从牌坊出发"],
        })

    end_poi, end_alts = find_poi_ambiguous(end_name)
    if end_poi is None:
        guidance = generate_poi_guidance(query, end_name, alternatives=end_alts)
        return _ok({
            "task_type": "unknown",
            "message": guidance,
            "start": start,
            "end": end,
            "ambiguity": "请指定终点",
            "example_queries": ["到樱顶"],
        })

    if start_poi["name"] == end_poi["name"]:
        return _err("same_poi", "起点和终点相同，请选择不同的地点", 400)

    # GCJ-02 → WGS-84：POI 坐标来自高德，路网用 WGS-84（DEC-007）
    start_lon_wgs, start_lat_wgs = gcj02_to_wgs84(start_poi["lon"], start_poi["lat"])
    end_lon_wgs, end_lat_wgs = gcj02_to_wgs84(end_poi["lon"], end_poi["lat"])

    try:
        start_node = get_nearest_node(G, start_lon_wgs, start_lat_wgs)
    except RuntimeError as e:
        return _err("nearest_node_failed", f"起点最近节点查找失败: {e}", 500)

    try:
        end_node = get_nearest_node(G, end_lon_wgs, end_lat_wgs)
    except RuntimeError as e:
        return _err("nearest_node_failed", f"终点最近节点查找失败: {e}", 500)

    constraints = intent_data.get("constraints", {})
    weights = intent_data.get("weights")

    try:
        route_result = compute_route(
            G=G,
            start_node=start_node,
            end_node=end_node,
            constraints=constraints,
            weights=weights,
        )
    except ValueError as e:
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
    }

    explanation = ""
    try:
        weight_source = intent_data.get("weight_source")
        explanation = generate_explanation(route_data_for_explainer, constraints, weights, weight_source)
    except Exception:
        explanation = "已为您规划好路线。"

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

    Input: {"start": {"name": "牌坊"}, "poi_type": "scenery", "keyword": "樱花"}
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