"""
R1 最短路径基线 (Shortest-Path Baseline)

只用 networkx dijkstra_path(weight="length") 计算纯几何最短路径，
不考虑坡度/景观等多因素成本。作为路径质量评估的下界基线。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 注入项目根目录，使 from spatial/agents 可直接导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import networkx as nx

from spatial.routing import (
    _path_length,
    _compute_overlap,
    _weighted_avg_attr,
    estimate_duration_min,
    normalize_mode,
    MODE_SPEEDS_KMH,
    _normalize_lengths,
)


def shortest_path_route(G: nx.MultiDiGraph, start_node: int, end_node: int, mode: str = "walk") -> dict:
    """R1：纯最短路径基线。

    用 dijkstra_path(weight="length") 计算几何最短路径，recommended == shortest。
    返回与 spatial.routing.compute_route 兼容的 dict。

    Args:
        G: OSMnx MultiDiGraph 路网
        start_node: 起点节点 ID
        end_node: 终点节点 ID
        mode: 出行方式 walk/bike/drive（仅影响 duration 估算）

    Returns:
        兼容 compute_route 输出的 dict（recommended/shortest 相同）
    """
    mode = normalize_mode(mode)

    try:
        path = nx.dijkstra_path(G, start_node, end_node, weight="length")
    except nx.NetworkXNoPath:
        raise ValueError(f"最短路径不可达: start={start_node} end={end_node}")

    rec_len = _path_length(G, path)
    short_len = rec_len  # 基线无差异
    overlap = _compute_overlap(path, path)  # 1.0
    max_len, _ = _normalize_lengths(G)

    slope_avg = _weighted_avg_attr(G, path, "slope_level")
    scenery_avg = _weighted_avg_attr(G, path, "scenery_level")

    return {
        "recommended": path,
        "shortest": path,
        "filter_status": "shortest_baseline",
        "overlap_rate": round(overlap, 4),
        "degraded": False,
        "degraded_count": 0,
        "recommended_length_m": round(rec_len, 1),
        "shortest_length_m": round(short_len, 1),
        "applied_weights": {"distance": 1.0, "slope": 0.0, "scenery": 0.0},
        "length_capped": False,
        "max_len": max_len,
        "_annotation_degraded": False,
        "mode": mode,
        "duration_min": estimate_duration_min(rec_len, mode),
        "shortest_duration_min": estimate_duration_min(short_len, mode),
        "speed_kmh": MODE_SPEEDS_KMH[mode],
        "road_conditions_applied": 0,
        "weather_applied": False,
        "slope_avg_recommended": round(slope_avg, 3),
        "scenery_avg_recommended": round(scenery_avg, 3),
        "slope_avg_shortest": round(slope_avg, 3),
        "scenery_avg_shortest": round(scenery_avg, 3),
    }


if __name__ == "__main__":
    # CLI 自检：加载路网后在牌坊→樱顶上跑一次最短路径
    from spatial.network import load_or_download_network, get_nearest_node
    from spatial.poi import get_poi
    from spatial.coord_transform import gcj02_to_wgs84

    G = load_or_download_network()
    s = get_poi("珞珈门")
    e = get_poi("樱顶")
    s_lng, s_lat = gcj02_to_wgs84(s["lon"], s["lat"])
    e_lng, e_lat = gcj02_to_wgs84(e["lon"], e["lat"])
    sn = get_nearest_node(G, s_lng, s_lat)
    en = get_nearest_node(G, e_lng, e_lat)
    res = shortest_path_route(G, sn, en, mode="walk")
    print(f"最短路径长度: {res['recommended_length_m']}m, 节点数: {len(res['recommended'])}")
