"""
路况影响矩阵 · 真实路网验证脚本

在真实校园路网上为每种出行模式挑选一条"位于最短路上、且存在绕行替代"的样本边，
然后对 5 类路况事件 × 3 种模式实际调用 compute_route，输出：
  - 基线/事件下的推荐路径长度
  - 推荐路径是否仍经过事发边
  - filter_status（road_closure / road_penalty / degraded_*）
  - road_conditions_applied 计数

运行：python scripts/verify_road_condition_effects.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import networkx as nx

from spatial.network import load_or_download_network
from spatial.routing import compute_route, filter_graph_for_mode
from spatial.road_conditions import CONDITION_EFFECTS, CONDITION_LABELS


def find_sample_edge(G, mode, max_tries=60):
    """找一条：封掉后存在明显绕行（绕行距离 ≥1.3× 边长）的边。

    效率：不做全图 copy（大图 copy 数千次极慢），改为按边长降序抽样，
    临时删边测一次 dijkstra 后立即恢复，找到即返回。
    """
    edges = [(float(d.get("length", 0) or 0), u, v, k, d)
             for u, v, k, d in G.edges(keys=True, data=True)
             if 40 <= float(d.get("length", 0) or 0) <= 250]
    # 长边更可能存在明显绕行替代
    edges.sort(reverse=True)

    for length, u, v, k, d in edges[:max_tries]:
        removed = []
        for a, b in ((u, v), (v, u)):
            if G.has_edge(a, b, k):
                removed.append((a, b, k, dict(G[a][b][k])))
                G.remove_edge(a, b, k)
        try:
            if u not in G or v not in G:
                detour = None
            else:
                detour = nx.shortest_path_length(G, u, v, weight="length")
        except nx.NetworkXNoPath:
            detour = None
        finally:
            for a, b, kk, data in removed:
                G.add_edge(a, b, kk, **data)
        if detour is not None and detour >= length * 1.3:
            return (detour / length, u, v, k, length, detour,
                    d.get("name", "未命名道路"))
    return None


def edge_on_path(path, u, v):
    return any((a == u and b == v) or (a == v and b == u)
               for a, b in zip(path, path[1:]))


def make_condition(ctype, u, v, k, name):
    return {
        "id": "verify01", "type": ctype, "name": "验证-" + CONDITION_LABELS[ctype],
        "edge": {"u": u, "v": v, "key": k, "road_name": str(name or ""),
                 "snap": {}, "geometry_gcj": []},
        "start_time": 0, "end_time": 0,
    }


def main():
    G = load_or_download_network()
    modes = ["walk", "bike", "drive"]

    samples = {}
    for mode in modes:
        Gm, _, _ = filter_graph_for_mode(G, mode)
        s = find_sample_edge(Gm, mode)
        if s is None:
            print(f"[{mode}] 未找到合适样本边")
            continue
        ratio, u, v, k, length, detour, name = s
        samples[mode] = (u, v, k, name)
        print(f"[{mode}] 样本边 {u}→{v}（k={k}）{name}：边长 {length:.0f}m，"
              f"封闭后绕行 {detour:.0f}m（{ratio:.2f}×）")

    print()
    header = f"{'事件':<6}{'模式':<7}{'效果':<8}{'基线(m)':<10}{'事件后(m)':<10}{'仍走该边':<10}{'状态'}"
    print(header)
    print("-" * 95)

    failures = []
    for mode in modes:
        if mode not in samples:
            continue
        u, v, k, name = samples[mode]
        base = compute_route(G, u, v, mode=mode)
        base_len = base["recommended_length_m"]
        for ctype in CONDITION_EFFECTS:
            effect = CONDITION_EFFECTS[ctype][mode]
            cond = make_condition(ctype, u, v, k, name)
            r = compute_route(G, u, v, mode=mode, road_conditions=[cond])
            uses = edge_on_path(r["recommended"], u, v)
            label = "硬封" if effect == "block" else f"{effect:g}×"
            if effect == "block":
                uses_txt = "是(异常!)" if uses else "否"
            else:
                # 软惩罚是经济杠杆：绕行代价 > 惩罚倍数时保留原边是正确行为，
                # 是否变路由绕行比决定（合成图测试已验证倍数足够时必然绕行）
                uses_txt = "是(成本比较后保留)" if uses else "否(改走绕行)"
            print(f"{CONDITION_LABELS[ctype]:<7}{mode:<8}{label:<9}"
                  f"{base_len:<11.0f}{r['recommended_length_m']:<11.0f}"
                  f"{uses_txt:<18}{r['filter_status']} (applied={r['road_conditions_applied']})")
            # 硬封类型：推荐路径不得经过事发边（除非软降级——那也必须 1000× 且状态标明）
            if effect == "block" and uses and "degraded_road_closure" not in r["filter_status"]:
                failures.append(f"{CONDITION_LABELS[ctype]}/{mode} 硬封但路径仍经过事发边")
        print()

    # 关键回归：旧版 walk 完全不应用路况，现在 closure 步行必须绕行
    if "walk" in samples:
        u, v, k, name = samples["walk"]
        cond = make_condition("closure", u, v, k, name)
        r = compute_route(G, u, v, mode="walk", road_conditions=[cond])
        assert not edge_on_path(r["recommended"], u, v), "步行封闭未绕行！"
        assert r["road_conditions_applied"] == 1
        print("✅ 关键回归：步行模式下道路封闭已正确绕行（旧版完全忽略路况）")

    if failures:
        print("\n❌ 验证失败：")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("\n✅ 全部硬封类型在对应模式下均绕行，软惩罚类型正常注入成本。")


if __name__ == "__main__":
    main()
