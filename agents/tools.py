"""珞珈智行 — Agent 工具箱（LLM function calling 的工具定义与执行器）。

设计原则（v2 全 Agent 架构）：
- LLM 只做决策，不做计算：坐标转换、路网吸附、Dijkstra、顺路比、游览排序全部在此
- 所有工具返回 dict（可 JSON 序列化），失败返回 {"error": ..., "message": ...}
  由 planner 回注给 LLM 自我修正，不抛异常中断循环
- 涉及校内地点的信息一律来自这里（resolve_poi / search_poi_candidates），
  配合系统提示约束 LLM 不编造校内设施
"""

import json
import logging
import math
import re
import time

import config
from spatial.poi import (
    find_poi_ambiguous, search_by_category, search_pois, list_all_pois,
    load_pois, importance_score, _flatten_poi,
)
from spatial.network import get_network, load_or_download_network, get_nearest_node, get_node_coords
from spatial.routing import (
    compute_route, compute_via_route, compute_tour_route,
    rank_via_candidates, resolve_weights, filter_graph_for_mode,
    estimate_duration_min, MODE_SPEEDS_KMH, build_turn_by_turn,
)
from spatial.coord_transform import gcj02_to_wgs84, wgs84_to_gcj02
from spatial.amap_poi import navigation_wgs
from spatial.road_conditions import list_conditions, CONDITION_LABELS
from spatial import weather as weather_mod

logger = logging.getLogger(__name__)

# 沿途 POI 标注阈值（与 routes.py 保持一致的体验）
_ALONG_ROUTE_THRESHOLD_M = 100.0
_ALONG_ROUTE_LIMIT = 8
_ALONG_ROUTE_MIN_IMPORTANCE = 8.0

# 游览默认选点数量与总长上限
_TOUR_DEFAULT_MAX_POIS = 6
_TOUR_MAX_TOTAL_M = 6000.0


# ===========================================================================
# 工具入参 Schema（OpenAI function calling 格式，DeepSeek 兼容）
# ===========================================================================

_ENDPOINT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "地点名称（POI 名/别名）；coord 类型时为显示名"},
        "type": {"type": "string", "enum": ["poi", "coord"],
                 "description": "poi=校内地点名；coord=WGS-84 坐标（如用户 GPS 定位）"},
        "lng": {"type": "number", "description": "经度（仅 type=coord 时填，WGS-84）"},
        "lat": {"type": "number", "description": "纬度（仅 type=coord 时填，WGS-84）"},
    },
    "required": ["name"],
}

_PREFERENCE_SCHEMA = {
    "constraints": {
        "type": "object",
        "description": "硬约束。仅在用户明确说'避开/不要'时设置",
        "properties": {
            "distance": {"type": "string", "enum": ["short", "medium", "long"]},
            "slope": {"type": "string", "enum": ["avoid", "normal", "prefer"]},
            "scenery": {"type": "string", "enum": ["high", "normal"]},
        },
    },
    "weights": {
        "type": "object",
        "description": "软权重，三项均在 0.05~0.90 且和为 1。普通通勤不填（默认 0.90/0.05/0.05）",
        "properties": {
            "distance": {"type": "number"},
            "slope": {"type": "number"},
            "scenery": {"type": "number"},
        },
    },
    "mode": {"type": "string", "enum": ["walk", "bike", "drive"],
             "description": "出行方式，默认 walk；用户明确说骑车/开车时才改"},
}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "resolve_poi",
            "description": "按名称解析校内地点。用户提到的地点名不明确或你想确认它是否存在时调用。",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "地点名称或别名"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_poi_candidates",
            "description": "按类别/关键词检索校内地点候选。用户说'想吃饭/想喝咖啡/想跑步'这类只有目的没有具体地点的需求时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "subcategory": {
                        "type": "string",
                        "description": "细分类别：canteen 食堂 / restaurant 餐厅 / fastfood 快餐小吃 / "
                                       "coffee 咖啡 / tea_drink 奶茶饮品 / supermarket 超市 / "
                                       "library 图书馆 / classroom 教学楼 / gym 体育馆 / field 运动场 / "
                                       "pool 游泳池 / sakura 赏樱 / lake 湖泊 / hill 山景 / park 公园广场 / "
                                       "landmark 地标 / bank 银行 / hospital 医院 / post 邮政 等",
                    },
                    "poi_type": {"type": "string", "enum": ["dining", "study", "sports", "dorm", "gate",
                                                            "scenery", "service", "area"]},
                    "keyword": {"type": "string", "description": "名称关键词模糊匹配"},
                    "season": {"type": "string", "enum": ["spring", "summer", "autumn", "winter"]},
                    "near_poi": {"type": "string", "description": "限定在该地点附近（POI 名），返回带距离"},
                    "include_minor": {"type": "boolean",
                                      "description": "是否包含小店铺（连锁奶茶/咖啡档口），默认 true"},
                    "limit": {"type": "integer", "description": "返回数量上限，默认 10"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_route",
            "description": "规划起点到终点的路径（多因素优化：距离/坡度/景观）。起点终点都明确时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": _ENDPOINT_SCHEMA,
                    "end": _ENDPOINT_SCHEMA,
                    **_PREFERENCE_SCHEMA,
                },
                "required": ["start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_via_route",
            "description": "规划带途经点的路径：如'去上课路上顺便买个笔记本'。via 可以指名具体地点，"
                         "也可以只给类别（自动从该类别里挑最顺路的），也可以用坐标设途经点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": _ENDPOINT_SCHEMA,
                    "end": _ENDPOINT_SCHEMA,
                    "via_name": {"type": "string", "description": "途经地点名（与 via_subcategory/via_coord 三选一）"},
                    "via_subcategory": {"type": "string",
                                        "description": "途经类别（如 supermarket/coffee/canteen），"
                                                       "自动选顺路的，与 via_name 二选一"},
                    "via_coord": {"type": "object",
                                  "description": "途经点坐标（WGS-84），与 via_name/via_subcategory 三选一",
                                  "properties": {
                                      "lng": {"type": "number", "description": "经度 WGS-84"},
                                      "lat": {"type": "number", "description": "纬度 WGS-84"},
                                  },
                                  "required": ["lng", "lat"]},
                    **_PREFERENCE_SCHEMA,
                },
                "required": ["start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_tour",
            "description": "规划多点游览路线：如'游客想逛遍全校''推荐一条赏樱路线'。"
                         "自动选点排序，生成环线或开放路线。",
            "parameters": {
                "type": "object",
                "properties": {
                    "theme": {"type": "string",
                              "description": "游览主题：sakura 赏樱 / scenery 全校风景（默认）/ "
                                             "lake 湖畔 / landmark 地标建筑"},
                    "season": {"type": "string", "enum": ["spring", "summer", "autumn", "winter"],
                               "description": "按季节标签筛点，如春天赏樱"},
                    "start": _ENDPOINT_SCHEMA,
                    "loop": {"type": "boolean", "description": "true=回到起点环线（默认），false=开放路线"},
                    "max_pois": {"type": "integer", "description": "游览点数量上限 2~8，默认 6"},
                    **_PREFERENCE_SCHEMA,
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_multimodal_route",
            "description": "规划分段换乘路径：如'先骑车到樱花大道再步行到珞珈山'"
                         "'骑车到校门再走进去'这类一次出行含多种出行方式的需求。"
                         "每段单独给 mode 和终点；第 N 段终点自动作为第 N+1 段起点。"
                         "不用于普通单方式路径（用 plan_route 即可）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": _ENDPOINT_SCHEMA,
                    "legs": {
                        "type": "array",
                        "description": "换乘段列表（2~4 段）。每段独立规划，按顺序拼接",
                        "items": {
                            "type": "object",
                            "properties": {
                                "mode": {"type": "string",
                                         "enum": ["walk", "bike", "drive"],
                                         "description": "本段出行方式"},
                                "end": _ENDPOINT_SCHEMA,
                                "via_name": {"type": "string",
                                             "description": "本段途经点（可选）；"
                                                            "不填则直接走终点"},
                            },
                            "required": ["mode", "end"],
                        },
                        "minItems": 2,
                        "maxItems": 4,
                    },
                    **{k: v for k, v in _PREFERENCE_SCHEMA.items()
                       if k != "mode"},
                },
                "required": ["start", "legs"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询武汉当前实时天气。用户问天气/要不要带伞/适不适合出门时必须调用，不允许凭印象回答。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_road_conditions",
            "description": "查询当前生效的封路/施工/积水/事故/活动管制等路况事件（绑定具体路段，步行也会绕封路）。用户问'哪里在修路''哪段封了''能不能走'时必须调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "信息不足或存在歧义时向用户提问澄清（如起点缺失、地点有多个候选）。调用后本轮对话结束。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "给用户的问题，口语化、简短"},
                    "options": {"type": "array", "items": {"type": "string"},
                                "description": "可选回答，最多 4 个"},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_followup",
            "description": "向用户推荐 2~3 个后续可能的问法。在给出最终答复前调用，让用户能一键继续。",
            "parameters": {
                "type": "object",
                "properties": {
                    "suggestions": {
                        "type": "array",
                        "items": {"type": "object",
                                  "properties": {
                                      "label": {"type": "string", "description": "按钮显示文字（≤10 字）"},
                                      "query": {"type": "string", "description": "点击后发送的自然语言 query"},
                                  },
                                  "required": ["label", "query"]},
                        "description": "2~3 个跟进建议",
                    },
                },
                "required": ["suggestions"],
            },
        },
    },
]

TOOL_NAMES = {t["function"]["name"] for t in TOOL_SCHEMAS}

# 终止型工具：调用即结束本轮循环
TERMINAL_TOOLS = {"ask_user"}


# ===========================================================================
# 内部辅助（确定性计算）
# ===========================================================================

def _ensure_graph():
    G = get_network()
    if G is not None:
        return G
    return load_or_download_network()


def _parse_linestring(wkt):
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


def _path_to_coords(G, route_nodes, route_edges=None):
    """路径节点序列 → 密集坐标序列（WGS-84，含边 geometry 中间点）。"""
    if not route_nodes:
        return []
    coords = []
    for i in range(len(route_nodes) - 1):
        u, v = route_nodes[i], route_nodes[i + 1]
        edge_data = G.get_edge_data(u, v)
        if not edge_data:
            continue
        data = (edge_data[route_edges[i][2]] if route_edges is not None
                else min(edge_data.values(), key=lambda d: d.get("length", float("inf"))))
        if not coords:
            ulng, ulat = get_node_coords(G, u)
            coords.append({"lng": round(ulng, 6), "lat": round(ulat, 6)})
        pts = _parse_linestring(data.get("geometry")) if data.get("geometry") else None
        if pts and len(pts) >= 2:
            ulng, ulat = get_node_coords(G, u)
            if (pts[0][0] - ulng) ** 2 + (pts[0][1] - ulat) ** 2 > (pts[-1][0] - ulng) ** 2 + (pts[-1][1] - ulat) ** 2:
                pts.reverse()
            for lng, lat in pts[1:]:
                coords.append({"lng": round(lng, 6), "lat": round(lat, 6)})
        else:
            vlng, vlat = get_node_coords(G, v)
            coords.append({"lng": round(vlng, 6), "lat": round(vlat, 6)})
    return coords


def _coords_wgs_to_gcj(coords_list):
    if not coords_list:
        return coords_list
    return [{"lng": round(gcj_lng, 6), "lat": round(gcj_lat, 6)}
            for c in coords_list
            for gcj_lng, gcj_lat in [wgs84_to_gcj02(c["lng"], c["lat"])]]


def _build_steps_gcj(G, route_nodes, mode, end_name="", route_edges=None):
    """逐步转向指令（动作点转 GCJ-02）。失败返回 []，不阻断路径规划。"""
    if not route_nodes or len(route_nodes) < 2:
        return []
    try:
        steps = build_turn_by_turn(
            G, route_nodes, mode=mode, end_name=end_name or "", route_edges=route_edges,
        )
    except Exception:
        logger.warning("转向指令生成失败 mode=%s", mode, exc_info=True)
        return []
    for s in steps:
        p = s.get("point")
        if p:
            g_lng, g_lat = wgs84_to_gcj02(p["lng"], p["lat"])
            s["point"] = {"lng": round(g_lng, 6), "lat": round(g_lat, 6)}
    return steps


def _merge_leg_steps(legs):
    """合并多段（途经/环线）指令：中间到达点转为"途经点"提示，去掉后续段的出发指令，
    累计距离整体平移并重排 seq。"""
    merged = []
    cum_offset = 0.0
    valid = [leg.get("steps") or [] for leg in legs]
    valid = [(i, st) for i, st in enumerate(valid) if st]
    for idx, (leg_i, steps) in enumerate(valid):
        is_last = idx == len(valid) - 1
        for s in steps:
            t = s.get("type")
            if idx > 0 and t == "depart":
                continue
            ns = dict(s)
            if t == "arrive" and not is_last:
                ns["type"] = "via"
                ns["action"] = "via"
                via_name = legs[leg_i].get("end_name") or "途经点"
                ns["text"] = f"到达途经点（{via_name}），继续前行"
            ns["cumulative_m"] = round(cum_offset + float(s.get("cumulative_m", 0) or 0), 1)
            merged.append(ns)
        # 下一段的累计距离偏移 = 本段最后一条累计值（本段总长）
        cum_offset += float(steps[-1].get("cumulative_m", 0) or 0)
    for i, s in enumerate(merged):
        s["seq"] = i
    return merged


def _haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _poi_public(poi: dict, with_coords: bool = True) -> dict:
    """给 LLM/前端看的精简 POI 结构（坐标保持 GCJ-02，与前端一致）。"""
    out = {
        "name": poi.get("name", ""),
        "type": poi.get("type", ""),
        "subcategory": poi.get("subcategory", ""),
        "description": (poi.get("description") or "")[:120],
        "scenery_score": poi.get("scenery_score"),
    }
    if with_coords:
        out["lat"] = poi.get("lat")
        out["lng"] = poi.get("lon") or poi.get("lng")
    return out


def _resolve_endpoint(ref: dict, G_mode):
    """把 {name} 或 {type:coord,lng,lat} 解析为路网节点。

    Returns: (node_id, display_name, error_dict_or_None)
    """
    if not isinstance(ref, dict):
        return None, None, {"error": "invalid_endpoint", "message": "起终点格式不正确"}
    name = (ref.get("name") or "").strip()
    if ref.get("type") == "coord":
        coord = ref.get("coordinates") or {}
        lng = coord.get("lng") if coord else None
        lat = coord.get("lat") if coord else None
        # 兼容 LLM 直接在顶层放 lng/lat 的情况
        if lng is None:
            lng = ref.get("lng")
        if lat is None:
            lat = ref.get("lat")
        if lng is not None and lat is not None:
            try:
                lng, lat = float(lng), float(lat)
            except (TypeError, ValueError):
                return None, None, {"error": "invalid_coord", "message": f"坐标无法识别: {ref}"}
            node = get_nearest_node(G_mode, lng, lat)
            return node, name or "我的位置", None
    if not name:
        return None, None, {"error": "missing_name", "message": "地点名为空"}
    poi, alts = find_poi_ambiguous(name)
    if poi is None:
        return None, None, {
            "error": "poi_not_found",
            "message": f"校内没找到「{name}」",
            "candidates": [_poi_public(a, with_coords=False) for a in (alts or [])][:5],
        }
    lng_wgs, lat_wgs = navigation_wgs(poi, G_mode.graph.get('travel_mode', 'walk'))
    node = get_nearest_node(G_mode, lng_wgs, lat_wgs)
    return node, poi["name"], None


def _pois_along_route(G, route_nodes):
    """沿途重要 POI（返回 GCJ-02，与前端一致）。"""
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
    for poi in load_pois():
        coords = poi.get("coordinates", {})
        poi_lng_wgs, poi_lat_wgs = gcj02_to_wgs84(coords.get("lng", 0), coords.get("lat", 0))
        min_dist = min((_haversine(poi_lat_wgs, poi_lng_wgs, rlat, rlng)
                        for rlng, rlat in route_coords), default=float("inf"))
        if min_dist <= _ALONG_ROUTE_THRESHOLD_M:
            imp = importance_score(poi)
            if imp < _ALONG_ROUTE_MIN_IMPORTANCE:
                continue
            flat = _flatten_poi(poi)
            flat["distance_to_route_m"] = round(min_dist, 1)
            flat["importance"] = round(imp, 2)
            along.append(flat)
    along.sort(key=lambda p: (-p["importance"], p["distance_to_route_m"]))
    return along[:_ALONG_ROUTE_LIMIT]


def _weather_snapshot():
    try:
        live = weather_mod.fetch_weather_live()
        if not live:
            return None
        return {"live": live, "impact": weather_mod.classify_weather(live)}
    except Exception as e:
        logger.warning("天气快照获取失败: %s", e)
        return None


def _weather_public(snap):
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


def _route_payload(G, route_result, start_name, end_name, mode):
    """compute_route 结果 → 前端/LLM 可用的路径包（坐标 GCJ-02）。"""
    recommended_nodes = route_result["recommended"]
    shortest_nodes = route_result["shortest"]
    recommended_edges = route_result["recommended_edges"]
    shortest_edges = route_result["shortest_edges"]
    return {
        "start_name": start_name,
        "end_name": end_name,
        "recommended": _coords_wgs_to_gcj(
            _path_to_coords(G, recommended_nodes, recommended_edges)),
        "shortest": _coords_wgs_to_gcj(
            _path_to_coords(G, shortest_nodes, shortest_edges)),
        "recommended_edge_ids": recommended_edges,
        "shortest_edge_ids": shortest_edges,
        "steps": _build_steps_gcj(
            G, recommended_nodes, mode, end_name=end_name, route_edges=recommended_edges),
        "pois": _pois_along_route(G, recommended_nodes),
        "filter_status": route_result["filter_status"],
        "overlap_rate": route_result["overlap_rate"],
        "recommended_length_m": route_result["recommended_length_m"],
        "shortest_length_m": route_result["shortest_length_m"],
        "distance_m": route_result["recommended_length_m"],
        "duration_min": route_result["duration_min"],
        "shortest_duration_min": route_result["shortest_duration_min"],
        "applied_weights": route_result.get("applied_weights"),
        "degraded": route_result["degraded"],
        "length_capped": route_result["length_capped"],
        "mode": route_result["mode"],
        "speed_kmh": route_result.get("speed_kmh", MODE_SPEEDS_KMH.get(mode, 4.5)),
    }


# ===========================================================================
# 工具执行器
# ===========================================================================

def _tool_resolve_poi(args, ctx):
    poi, alts = find_poi_ambiguous(args.get("name", ""))
    if poi:
        return {"found": True, "poi": _poi_public(poi)}, None
    return {
        "found": False,
        "message": f"校内没找到「{args.get('name', '')}」",
        "candidates": [_poi_public(a, with_coords=False) for a in (alts or [])][:5],
    }, None


def _tool_search_poi_candidates(args, ctx):
    limit = max(1, min(int(args.get("limit", 10) or 10), 20))
    include_minor = bool(args.get("include_minor", True))
    subcategory = args.get("subcategory")
    poi_type = args.get("poi_type")
    keyword = (args.get("keyword") or "").strip()
    season = args.get("season")

    if keyword:
        results = search_pois(keyword, poi_type=poi_type, season=season,
                              subcategory=subcategory, include_minor=include_minor)[:limit]
    else:
        results = search_by_category(subcategory=subcategory, poi_type=poi_type,
                                     season=season, include_minor=include_minor,
                                     limit=limit)
    if not results:
        return {"candidates": [], "message": "校内暂无匹配的地点，可换个类别或关键词试试"}, None

    # near_poi：附直线距离（km 级排序够用，路网距离留给规划工具）
    near_name = (args.get("near_poi") or "").strip()
    if near_name:
        near_poi, _ = find_poi_ambiguous(near_name)
        if near_poi:
            for p in results:
                p["distance_m"] = round(_haversine(
                    near_poi["lat"], near_poi["lon"], p.get("lat", 0), p.get("lon", p.get("lng", 0))
                ), 0)
            results.sort(key=lambda p: p["distance_m"])

    candidates = [_poi_public(p) for p in results]
    return {"candidates": candidates, "count": len(candidates)}, {"candidates": candidates}


def _plan_common(args, ctx):
    """公共准备：路网、方式过滤、天气。返回 (G, G_mode, mode, weather_info)。"""
    G = _ensure_graph()
    mode = args.get("mode") or "walk"
    if mode not in ("walk", "bike", "drive"):
        mode = "walk"
    G_mode, _, _ = filter_graph_for_mode(G, mode)
    snap = _weather_snapshot()
    return G, G_mode, mode, (snap["live"] if snap else None)


def _tool_plan_route(args, ctx):
    G, G_mode, mode, weather_info = _plan_common(args, ctx)
    start_node, start_name, err = _resolve_endpoint(args.get("start"), G_mode)
    if err:
        return err, None
    end_node, end_name, err = _resolve_endpoint(args.get("end"), G_mode)
    if err:
        return err, None
    if start_node == end_node:
        return {"error": "same_poi", "message": "起点和终点相同（或距离太近），换个目的地试试"}, None

    try:
        result = compute_route(
            G=G, start_node=start_node, end_node=end_node,
            constraints=args.get("constraints") or {},
            weights=args.get("weights"), mode=mode, weather_info=weather_info,
        )
    except ValueError as e:
        return {"error": "route_not_found", "message": str(e)}, None

    payload = _route_payload(G, result, start_name, end_name, mode)
    return payload, {"route": payload}


def _tool_plan_via_route(args, ctx):
    G, G_mode, mode, weather_info = _plan_common(args, ctx)
    start_node, start_name, err = _resolve_endpoint(args.get("start"), G_mode)
    if err:
        return err, None
    end_node, end_name, err = _resolve_endpoint(args.get("end"), G_mode)
    if err:
        return err, None

    via_name = (args.get("via_name") or "").strip()
    via_sub = (args.get("via_subcategory") or "").strip()
    via_coord = args.get("via_coord")
    if not via_name and not via_sub and not via_coord:
        return {"error": "missing_via", "message": "缺少途经点：请用 via_name、via_subcategory 或 via_coord 指定"}, None

    if via_coord:
        # 坐标途经点：直接吸附到最近路网节点
        try:
            via_lng = float(via_coord.get("lng"))
            via_lat = float(via_coord.get("lat"))
            via_node = get_nearest_node(G_mode, via_lng, via_lat)
            via_display = "地图途经点"
            via_info = {"name": via_display}
            detour_ratio = None
        except (TypeError, ValueError, RuntimeError) as e:
            return {"error": "via_coord_invalid", "message": f"途经点坐标无效: {e}"}, None
    elif via_name:
        via_node, via_display, err = _resolve_endpoint({"name": via_name}, G_mode)
        if err:
            return err, None
        via_poi, _ = find_poi_ambiguous(via_name)
        via_info = _poi_public(via_poi) if via_poi else {"name": via_display}
        detour_ratio = None
    else:
        # 类别途经：检索候选 → 顺路性排序 → 取最顺路的
        cands = search_by_category(subcategory=via_sub, include_minor=True, limit=10)
        if not cands:
            return {"error": "no_via_candidates",
                    "message": f"校内没有「{via_sub}」类别的地点，换个类别试试"}, None
        poi_nodes = []
        for p in cands:
            try:
                lng_wgs, lat_wgs = gcj02_to_wgs84(p["lon"], p["lat"])
                poi_nodes.append((p, get_nearest_node(G_mode, lng_wgs, lat_wgs)))
            except Exception:
                continue
        on_the_way, off_the_way = rank_via_candidates(G, start_node, end_node, poi_nodes, mode=mode)
        if not on_the_way:
            return {
                "error": "no_on_the_way_via",
                "message": "顺路范围内没有合适的途经点（绕行比都超过 1.5），"
                           "可以放弃途经直接规划，或让用户在以下相对近的候选中选择",
                "off_the_way": [{"poi": _poi_public(p), "detour_ratio": r}
                                for p, _, r in off_the_way[:3]],
            }, None
        via_poi, via_node, detour_ratio = on_the_way[0]
        via_info = _poi_public(via_poi)
        via_display = via_poi["name"]

    try:
        result = compute_via_route(
            G, start_node, via_node, end_node,
            constraints=args.get("constraints") or {},
            weights=args.get("weights"), mode=mode, weather_info=weather_info,
        )
    except ValueError as e:
        return {"error": "route_not_found", "message": str(e)}, None

    leg1 = _route_payload(G, result["leg1"], start_name, via_info["name"], mode)
    leg2 = _route_payload(G, result["leg2"], via_info["name"], end_name, mode)
    payload = {
        "via": via_info,
        "legs": [leg1, leg2],
        # 前端兼容：推荐路径为两段拼接，起终点标注
        "start_name": start_name,
        "end_name": end_name,
        "recommended": leg1["recommended"] + leg2["recommended"],
        "recommended_edge_ids": (
            leg1["recommended_edge_ids"] + leg2["recommended_edge_ids"]),
        "steps": _merge_leg_steps([leg1, leg2]),
        "recommended_length_m": result["total_length_m"],
        "distance_m": result["total_length_m"],
        "detour_ratio": result["detour_ratio"],
        "detour_ratio_shortest": detour_ratio,
        "duration_min": round(estimate_duration_min(result["total_length_m"], mode), 1),
        "mode": mode,
        "pois": leg1["pois"] + leg2["pois"],
    }
    return payload, {"route": payload, "route_kind": "via"}


def _tool_plan_multimodal_route(args, ctx):
    """分段换乘路径：用户显式给每段 mode + end，按顺序拼接。

    schema: start + legs: array[{mode, end, via_name?}]。
    第 N 段终点自动作为第 N+1 段起点（节点重新吸附到对应 mode 的 G_mode）。
    每段独立 compute_route，最后合并 legs/recommended/steps/duration_min。
    """
    legs_in = args.get("legs") or []
    if not isinstance(legs_in, list) or len(legs_in) < 2:
        return {"error": "invalid_legs",
                "message": "至少需要 2 段换乘信息（每段含 mode 和 end）"}, None
    if len(legs_in) > 4:
        return {"error": "too_many_legs",
                "message": "换乘段数过多（最多 4 段），可拆成多次规划"}, None

    G = _ensure_graph()
    weather_info = None
    try:
        snap = _weather_snapshot()
        if snap:
            weather_info = snap.get("live")
    except Exception:
        pass

    # 起点（按第一段 mode 吸附）
    first_mode = legs_in[0].get("mode") or "walk"
    if first_mode not in ("walk", "bike", "drive"):
        first_mode = "walk"
    G_first, _, _ = filter_graph_for_mode(G, first_mode)
    start_node, start_name, err = _resolve_endpoint(args.get("start"), G_first)
    if err:
        return err, None

    legs_payload = []
    prev_node = start_node
    prev_name = start_name
    cumulative_len = 0.0
    cumulative_dur = 0.0
    recommended_all = []
    pois_all = []

    for idx, leg in enumerate(legs_in):
        mode = leg.get("mode") or "walk"
        if mode not in ("walk", "bike", "drive"):
            mode = "walk"
        end_ref = leg.get("end")
        if not isinstance(end_ref, dict):
            return {"error": "invalid_leg_end",
                    "message": f"第 {idx + 1} 段终点格式不正确"}, None

        # 每段独立过滤路网（mode 不同 → G_mode 不同）
        G_mode, _, _ = filter_graph_for_mode(G, mode)
        end_node, end_name, err = _resolve_endpoint(end_ref, G_mode)
        if err:
            return err, None
        # 起点也要重新吸附到本段 G_mode（前一段终点是 walk-only 时可能不在本段图里）
        if prev_node not in G_mode:
            # 用前一终点坐标重新吸附到本段图最近 in-mode 节点
            try:
                prev_lng, prev_lat = get_node_coords(G, prev_node)
                prev_node = get_nearest_node(G_mode, prev_lng, prev_lat)
            except Exception:
                return {"error": "leg_start_unreachable",
                        "message": f"第 {idx + 1} 段起点在 {mode} 模式下无可达节点附近，"
                                   f"可调整换乘点位置"}, None

        if prev_node == end_node:
            return {"error": "same_poi",
                    "message": f"第 {idx + 1} 段起终点相同，可省略该段"}, None

        via_name = (leg.get("via_name") or "").strip()
        try:
            if via_name:
                via_node, via_display, err = _resolve_endpoint({"name": via_name}, G_mode)
                if err:
                    return err, None
                result = compute_via_route(
                    G, prev_node, via_node, end_node,
                    constraints=args.get("constraints") or {},
                    weights=args.get("weights"), mode=mode,
                    weather_info=weather_info,
                )
                leg1 = _route_payload(G, result["leg1"], prev_name, via_display, mode)
                leg2 = _route_payload(G, result["leg2"], via_display, end_name, mode)
                leg_payload = {
                    "via": {"name": via_display},
                    "legs": [leg1, leg2],
                    "recommended": leg1["recommended"] + leg2["recommended"],
                    "steps": _merge_leg_steps([leg1, leg2]),
                    "recommended_length_m": result["total_length_m"],
                    "distance_m": result["total_length_m"],
                    "duration_min": round(estimate_duration_min(result["total_length_m"], mode), 1),
                    "mode": mode,
                    "pois": leg1["pois"] + leg2["pois"],
                }
            else:
                result = compute_route(
                    G=G, start_node=prev_node, end_node=end_node,
                    constraints=args.get("constraints") or {},
                    weights=args.get("weights"), mode=mode,
                    weather_info=weather_info,
                )
                leg_payload = _route_payload(G, result, prev_name, end_name, mode)
        except ValueError as e:
            return {"error": "route_not_found",
                    "message": f"第 {idx + 1} 段（{mode}：{prev_name}→{end_name}）不可达：{e}"}, None

        legs_payload.append(leg_payload)
        cumulative_len += float(leg_payload["recommended_length_m"] or 0)
        cumulative_dur += float(leg_payload["duration_min"] or 0)
        recommended_all += leg_payload["recommended"]
        pois_all += leg_payload.get("pois") or []
        prev_node = end_node
        prev_name = end_name

    # 合并多段 steps（_merge_leg_steps 已处理跨段 arrive→via 转换）
    merged_steps = _merge_leg_steps(legs_payload)

    payload = {
        "legs": legs_payload,
        "start_name": start_name,
        "end_name": prev_name,
        "recommended": recommended_all,
        "recommended_edge_ids": [
            edge for leg in legs_payload for edge in leg["recommended_edge_ids"]
        ],
        "steps": merged_steps,
        "recommended_length_m": round(cumulative_len, 1),
        "distance_m": round(cumulative_len, 1),
        "duration_min": round(cumulative_dur, 1),
        # 整体 mode 用第一段（前端默认渲染色），每段 mode 在 legs[].mode 里
        "mode": legs_payload[0]["mode"],
        "pois": pois_all,
    }
    return payload, {"route": payload, "route_kind": "multimodal"}


def _tool_plan_tour(args, ctx):
    G, G_mode, mode, weather_info = _plan_common(args, ctx)

    theme = (args.get("theme") or "scenery").strip()
    theme_map = {
        "sakura": {"subcategory": "sakura"},
        "lake": {"subcategory": "lake"},
        "landmark": {"subcategory": "landmark"},
        "scenery": {"poi_type": "scenery"},
    }
    sel = theme_map.get(theme, {"poi_type": "scenery"})
    max_pois = max(2, min(int(args.get("max_pois", _TOUR_DEFAULT_MAX_POIS) or _TOUR_DEFAULT_MAX_POIS), 8))
    pois = search_by_category(season=args.get("season"), include_minor=False, limit=max_pois, **sel)
    if theme != "scenery" and len(pois) < 2:
        # 主题点太少时并入全校风景点
        pois = (pois + search_by_category(poi_type="scenery", season=args.get("season"),
                                          include_minor=False, limit=max_pois))[:max_pois]
    if len(pois) < 2:
        return {"error": "no_tour_pois", "message": "可游览的校内景点不足，换个主题试试"}, None

    # 起点：可选；默认以重要度最高的点为锚
    start_node, start_name = None, None
    start_ref = args.get("start")
    if start_ref:
        start_node, start_name, err = _resolve_endpoint(start_ref, G_mode)
        if err:
            return err, None

    poi_nodes = []
    for p in pois:
        try:
            lng_wgs, lat_wgs = gcj02_to_wgs84(p["lon"], p["lat"])
            node = get_nearest_node(G_mode, lng_wgs, lat_wgs)
        except Exception:
            continue
        p2 = dict(p)
        p2["importance"] = importance_score(p)
        poi_nodes.append((p2, node))

    loop = bool(args.get("loop", True)) if start_node is None else bool(args.get("loop", True))
    result = compute_tour_route(
        G, poi_nodes, start_node=start_node, loop=loop,
        constraints=args.get("constraints") or {}, weights=args.get("weights"),
        mode=mode, max_total_m=_TOUR_MAX_TOTAL_M, weather_info=weather_info,
    )
    if not result["legs"]:
        return {"error": "tour_failed", "message": "这些景点之间暂时无法连通，换一批试试"}, None

    legs_payload = []
    seq_names = [start_name] if start_name else []
    seq_names += [p["name"] for p in result["ordered_pois"]]
    for i, leg in enumerate(result["legs"]):
        a = seq_names[i] if i < len(seq_names) else f"第{i + 1}站"
        b = seq_names[i + 1] if i + 1 < len(seq_names) else seq_names[0]
        legs_payload.append(_route_payload(G, leg, a, b, mode))

    ordered_public = [_poi_public(p) for p in result["ordered_pois"]]
    payload = {
        "tour": {
            "theme": theme,
            "ordered_pois": ordered_public,
            "loop": result["loop"],
            "legs": legs_payload,
            "total_length_m": result["total_length_m"],
            "duration_min": round(estimate_duration_min(result["total_length_m"], mode), 1),
            "dropped": [p.get("name", "") for p in result["dropped"]],
            "mode": mode,
        },
        # 前端兼容：整条环线作为 recommended 返回
        "start_name": seq_names[0] if seq_names else "",
        "recommended": [c for leg in legs_payload for c in leg["recommended"]],
        "recommended_edge_ids": [
            edge for leg in legs_payload for edge in leg["recommended_edge_ids"]
        ],
        "steps": _merge_leg_steps(legs_payload),
        "recommended_length_m": result["total_length_m"],
        "distance_m": result["total_length_m"],
    }
    return payload, {"route": payload, "route_kind": "tour"}


def _tool_get_weather(args, ctx):
    pub = _weather_public(_weather_snapshot())
    if not pub:
        return {"error": "weather_unavailable", "message": "天气暂时获取不到"}, None
    return pub, None


def _tool_list_road_conditions(args, ctx):
    conds = list_conditions()
    now = time.time()
    out = []
    for c in conds:
        road = (c.get("edge") or {}).get("road_name", "")
        out.append({
            "name": c.get("name", ""),
            "type_label": CONDITION_LABELS.get(c.get("type"), c.get("type", "")),
            "road_name": road,
            "location": (road + "路段") if road else "",
            "description": (c.get("description") or "")[:100],
            "end_time": c.get("end_time"),
        })
    return {"conditions": out, "count": len(out),
            "message": "当前没有生效中的封路/施工" if not out else ""}, None


def _tool_ask_user(args, ctx):
    question = (args.get("question") or "").strip() or "能再说得具体一点吗？"
    options = [str(o)[:30] for o in (args.get("options") or [])][:4]
    return {"question": question, "options": options}, {"clarify": {"question": question, "options": options}}


def _tool_suggest_followup(args, ctx):
    raw = args.get("suggestions") or []
    suggestions = []
    for s in raw[:3]:
        if not isinstance(s, dict):
            continue
        label = str(s.get("label", ""))[:10]
        query = str(s.get("query", ""))[:200]
        if label and query:
            suggestions.append({"label": label, "query": query})
    if not suggestions:
        return {"suggestions": []}, None
    return {"suggestions": suggestions}, {"suggestions": suggestions}


_EXECUTORS = {
    "resolve_poi": _tool_resolve_poi,
    "search_poi_candidates": _tool_search_poi_candidates,
    "plan_route": _tool_plan_route,
    "plan_via_route": _tool_plan_via_route,
    "plan_multimodal_route": _tool_plan_multimodal_route,
    "plan_tour": _tool_plan_tour,
    "get_weather": _tool_get_weather,
    "list_road_conditions": _tool_list_road_conditions,
    "ask_user": _tool_ask_user,
    "suggest_followup": _tool_suggest_followup,
}


def execute_tool(name: str, args: dict) -> tuple:
    """执行工具。返回 (result_for_llm, artifact_or_None)。

    artifact 用于 planner 组装最终响应（route / candidates / clarify）。
    任何内部异常都转为 error dict 回注，不抛出。
    """
    executor = _EXECUTORS.get(name)
    if executor is None:
        return {"error": "unknown_tool", "message": f"工具 {name} 不存在"}, None
    if not isinstance(args, dict):
        args = {}
    try:
        return executor(args, None)
    except Exception as e:
        logger.exception("工具 %s 执行异常", name)
        return {"error": "tool_exception", "message": f"{type(e).__name__}: {e}"}, None


def result_to_json(result: dict, max_len: int = 4000) -> str:
    """工具结果压缩为 LLM 可读的 JSON 文本（截断路径坐标等大体量字段）。

    路径坐标对 LLM 决策无用且极占 token，只保留统计信息；
    完整坐标由 planner 从 artifact 里取，直接进 API 响应。
    """
    compact = {}
    for k, v in result.items():
        if k in ("recommended", "shortest"):
            compact[k] = f"[{len(v)} 个坐标点]" if isinstance(v, list) else v
        elif k == "steps":
            compact[k] = f"[{len(v)} 条转向指令]" if isinstance(v, list) else v
        elif k == "legs":
            compact[k] = [
                {kk: (f"[{len(vv)} 个坐标点]" if kk in ("recommended", "shortest") and isinstance(vv, list)
                      else (f"[{len(vv)} 条转向指令]" if kk == "steps" and isinstance(vv, list)
                            else ("[略]" if kk == "pois" else vv)))
                 for kk, vv in leg.items()}
                for leg in v
            ] if isinstance(v, list) else v
        elif k == "pois" and isinstance(v, list):
            compact[k] = [p.get("name", "") for p in v][:10]
        else:
            compact[k] = v
    text = json.dumps(compact, ensure_ascii=False)
    return text[:max_len]
