"""
R2 固定多因素权重基线 (Fixed-Weight Baseline)

不解析用户偏好，统一用 DEFAULT_WEIGHTS 调用 compute_route。
用于评估"无 LLM 偏好解析、但有完整多因素算法"的路径质量，
隔离 LLM 权重解析的贡献。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 注入项目根目录，使 from spatial/agents 可直接导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from spatial.routing import compute_route, DEFAULT_WEIGHTS


def fixed_weight_route(G, start_node: int, end_node: int, constraints: dict | None = None, mode: str = "walk") -> dict:
    """R2：固定权重基线。

    用 DEFAULT_WEIGHTS（distance:0.5/slope:0.2/scenery:0.3）调用 compute_route，
    不读取 LLM 输出的 weights。返回 compute_route 的完整结果。

    Args:
        G: OSMnx MultiDiGraph 路网
        start_node: 起点节点 ID
        end_node: 终点节点 ID
        constraints: 硬约束 dict（可空，基线一般不传偏好）
        mode: 出行方式 walk/bike/drive

    Returns:
        compute_route 的返回 dict
    """
    # 关键：传入固定的 DEFAULT_WEIGHTS，忽略任何 LLM 解析出的偏好
    return compute_route(
        G=G,
        start_node=start_node,
        end_node=end_node,
        constraints=constraints or {},
        weights=dict(DEFAULT_WEIGHTS),  # 固定权重，不随查询变化
        mode=mode,
    )


if __name__ == "__main__":
    # CLI 自检
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
    res = fixed_weight_route(G, sn, en, mode="walk")
    print(f"固定权重推荐路径长度: {res['recommended_length_m']}m, "
          f"最短: {res['shortest_length_m']}m, overlap={res['overlap_rate']}")
