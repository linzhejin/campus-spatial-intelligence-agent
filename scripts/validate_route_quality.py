# -*- coding: utf-8 -*-
"""
路径质量 OD 矩阵验证：台阶/穿楼边侵入率 + 可达性

从 data/pois.json 抽样 OD 对，在 walk/bike/drive 三模式下真实跑路径规划，
对比"修复前基线（无 edge_overrides + 旧台阶漏网模拟）"与"修复后"：
  - steps 边侵入次数（bike/drive 目标 0）
  - 穿楼候选边侵入次数
  - 无路径/异常次数（可达性不得退化）

用法：python scripts/validate_route_quality.py [抽样对数，默认1200]
"""
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import networkx as nx

from spatial.network import (
    load_or_download_network, get_nearest_node, _merge_annotations, _annotations_path,
)
from spatial.routing import (
    compute_route, _edge_highway_tags, _edge_blocked_modes,
    _NON_VEHICULAR_HIGHWAY,
)
from spatial.coord_transform import gcj02_to_wgs84

random.seed(20260916)


def load_poi_nodes(G):
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "pois.json"), encoding="utf-8") as f:
        pois = json.load(f)["pois"]
    nodes = []
    for p in pois:
        c = p.get("coordinates", {})
        lng, lat = gcj02_to_wgs84(float(c["lng"]), float(c["lat"]))
        try:
            nodes.append((p["name"], get_nearest_node(G, lng, lat)))
        except Exception:
            continue
    return nodes


def path_edges(G, edge_ids):
    out = []
    for u, v, key in edge_ids:
        if G.has_edge(u, v, key):
            out.append((u, v, key, G[u][v][key]))
    return out


def run_matrix(G, pairs, label, crossing_edges):
    stats = {m: {"n": 0, "fail": 0, "steps": 0, "cross_bike_drive": 0,
                 "cross_walk": 0, "blocked_attr": 0} for m in ("walk", "bike", "drive")}
    t0 = time.time()
    for s_name, s, t_name, t in pairs:
        for mode in ("walk", "bike", "drive"):
            st = stats[mode]
            st["n"] += 1
            try:
                res = compute_route(G, s, t, mode=mode)
                selected_edges = res["recommended_edges"]
            except Exception:
                st["fail"] += 1
                continue
            for u, v, k, d in path_edges(G, selected_edges):
                tags = _edge_highway_tags(d)
                bm = _edge_blocked_modes(d)
                if any(x in ("steps", "elevator", "escalator") for x in tags):
                    st["steps"] += 1
                if (u, v, k) in crossing_edges:
                    if mode == "walk":
                        st["cross_walk"] += 1
                    else:
                        st["cross_bike_drive"] += 1
                if mode in ("bike", "drive") and (mode in bm or "all" in bm):
                    st["blocked_attr"] += 1
    dt = time.time() - t0
    print(f"\n=== {label}（{len(pairs)} 对 OD × 3 模式，{dt:.0f}s）===")
    for mode in ("walk", "bike", "drive"):
        st = stats[mode]
        print(f"  {mode:5s}: 路径{st['n']:4d}  失败{st['fail']:3d}  "
              f"台阶侵入{st['steps']:4d}  疑似穿楼边"
              f"{st['cross_walk'] + st['cross_bike_drive']:4d}  "
              f"封禁边漏网{st['blocked_attr']:4d}")
    return stats


def reachability_regressions(base, fixed):
    """相同 OD 样本中，新策略不能增加任一方式的不可达数量。"""
    return {mode: (base[mode]["fail"], fixed[mode]["fail"])
            for mode in ("walk", "bike", "drive")
            if fixed[mode]["fail"] > base[mode]["fail"]}


def main():
    n_pairs = int(sys.argv[1]) if len(sys.argv) > 1 else 1200

    print("加载修复后路网（含 annotations + edge_overrides）...")
    G_fixed = load_or_download_network()
    poi_nodes = load_poi_nodes(G_fixed)
    print(f"有效 POI 节点 {len(poi_nodes)} 个")

    pairs = []
    for _ in range(n_pairs):
        s, t = random.sample(poi_nodes, 2)
        pairs.append((s[0], s[1], t[0], t[1]))

    issue_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "audit_output", "audit_issues.json")
    if not os.path.exists(issue_path):
        raise RuntimeError("缺少建筑相交审计清单，请先运行 scripts/audit_network.py")
    with open(issue_path, encoding="utf-8") as f:
        crossing_edges = {(e["u"], e["v"], e.get("k", 0))
                          for e in json.load(f).get("cross_building_edges", [])}

    # 基线：重读缓存 + 仅 annotations（模拟 overrides 不存在）
    print("构建基线路网（无 edge_overrides）...")
    cache = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "whu_road_network.graphml")
    G_base = nx.read_graphml(cache, node_type=int)
    ann = _annotations_path()
    if os.path.exists(ann):
        _merge_annotations(G_base, ann)

    base = run_matrix(G_base, pairs, "修复前基线", crossing_edges)
    fixed = run_matrix(G_fixed, pairs, "修复后", crossing_edges)

    print("\n=== 结论 ===")
    for mode in ("bike", "drive"):
        ok_steps = fixed[mode]["steps"] == 0
        ok_block = fixed[mode]["blocked_attr"] == 0
        print(f"  {mode}: 台阶侵入 {base[mode]['steps']} → {fixed[mode]['steps']} "
              f"{'通过' if ok_steps else '失败'}；封禁边漏网 {fixed[mode]['blocked_attr']} "
              f"{'通过' if ok_block else '失败'}；失败 {base[mode]['fail']} → {fixed[mode]['fail']}")
    print(f"  walk: 疑似穿楼边侵入 {base['walk']['cross_walk']} → {fixed['walk']['cross_walk']}；"
          f"失败 {base['walk']['fail']} → {fixed['walk']['fail']}")
    print(f"  bike: 疑似穿楼边侵入 {base['bike']['cross_bike_drive']} → "
          f"{fixed['bike']['cross_bike_drive']}")
    print(f"  drive: 疑似穿楼边侵入 {base['drive']['cross_bike_drive']} → "
          f"{fixed['drive']['cross_bike_drive']}")
    regressions = reachability_regressions(base, fixed)
    if regressions:
        print(f"[失败] 相同 OD 样本的可达性下降: {regressions}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
