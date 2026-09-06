# -*- coding: utf-8 -*-
"""数据真实性/准确性全面校验，输出 data_validation_report.json。

校验项：
A. POI 结构完整性、坐标范围、坐标系一致性
B. POI 点在所属校区多边形内（GCJ-02）
C. 重复坐标/重复名称检测
D. 校门专项（13 个校门）
E. 路网 GraphML：节点/边、坐标范围、边属性分布、连通性
F. 路段标注 road_annotations.json 覆盖率
"""
import json
import math
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import networkx as nx

from config import CAMPUS_POLYS_GCJ

report = {}

# ============ A. POI 基础校验 ============
with open("data/pois.json", encoding="utf-8") as f:
    pdata = json.load(f)
pois = pdata["pois"]

valid_types = {"gate", "study", "dorm", "dining", "sports", "scenery", "service"}
valid_campus = {"文理学部", "工学部", "信息学部"}

issues = []

def pip(lng, lat, poly):
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > lat) != (yj > lat)) and \
                (lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside

def which_campus(lng, lat):
    for name, poly in CAMPUS_POLYS_GCJ.items():
        if pip(lng, lat, poly):
            return name
    return None

missing_fields = []
bad_range = []
bad_type = []
campus_mismatch = []
outside_all = []

for p in pois:
    pid = p.get("id", "?")
    # 必填字段
    for fld in ("id", "name", "coordinates", "type", "campus"):
        if fld not in p or p[fld] in (None, ""):
            missing_fields.append((pid, p.get("name", ""), fld))
    lng = p.get("coordinates", {}).get("lng")
    lat = p.get("coordinates", {}).get("lat")
    if lng is None or lat is None:
        continue
    # 武汉范围
    if not (114.30 < lng < 114.42 and 30.50 < lat < 30.58):
        bad_range.append((pid, p["name"], lng, lat))
    if p.get("type") not in valid_types:
        bad_type.append((pid, p["name"], p.get("type")))
    # 校区归属
    claimed = p.get("campus")
    actual = which_campus(lng, lat)
    if actual is None:
        outside_all.append((pid, p["name"], claimed, lng, lat))
    elif actual != claimed:
        campus_mismatch.append((pid, p["name"], claimed, actual, lng, lat))

report["poi_total"] = len(pois)
report["poi_by_type"] = dict(Counter(p["type"] for p in pois))
report["poi_by_campus"] = dict(Counter(p["campus"] for p in pois))
report["poi_rated"] = sum(1 for p in pois if p.get("rating"))
report["missing_fields"] = missing_fields
report["bad_range"] = bad_range
report["bad_type"] = bad_type
report["campus_mismatch"] = campus_mismatch
report["outside_all_polys"] = outside_all

# ============ C. 重复检测 ============
def hav(a, b):
    R = 6371000
    lat1, lng1, lat2, lng2 = map(math.radians, [a[1], a[0], b[1], b[0]])
    dlat, dlng = lat2 - lat1, lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))

coords = [(p["id"], p["name"], p["coordinates"]["lng"], p["coordinates"]["lat"]) for p in pois]
close_pairs = []
for i in range(len(coords)):
    for j in range(i + 1, len(coords)):
        d = hav((coords[i][2], coords[i][3]), (coords[j][2], coords[j][3]))
        if d < 12:
            close_pairs.append((round(d, 1), coords[i][1], coords[j][1]))

name_map = defaultdict(list)
alias_map = defaultdict(list)
for p in pois:
    name_map[p["name"]].append(p["id"])
    for a in p.get("aliases", []):
        alias_map[a].append(p["id"])
dup_names = {n: ids for n, ids in name_map.items() if len(ids) > 1}
alias_conflicts = {}
for a, ids in alias_map.items():
    if len(set(ids)) > 1:
        alias_conflicts[a] = ids
# 别名与其他 POI 正式名冲突
alias_name_clash = {}
for p in pois:
    for a in p.get("aliases", []):
        if a in name_map and name_map[a] != [p["id"]]:
            alias_name_clash[a] = (p["id"], p["name"], name_map[a])

report["close_pairs_lt12m"] = close_pairs
report["duplicate_names"] = dup_names
report["alias_conflicts"] = {k: v for k, v in alias_conflicts.items()}
report["alias_name_clash"] = alias_name_clash

# ============ D. 校门专项 ============
gates = [p for p in pois if p["type"] == "gate"]
report["gates"] = [
    {"id": g["id"], "name": g["name"], "campus": g["campus"],
     "lng": g["coordinates"]["lng"], "lat": g["coordinates"]["lat"],
     "rating": g.get("rating"),
     "aliases": g.get("aliases", [])}
    for g in sorted(gates, key=lambda g: g["id"])
]

# ============ E. 路网校验 ============
G = nx.read_graphml("data/whu_road_network.graphml", node_type=int)
xs = [float(d["x"]) for _, d in G.nodes(data=True)]
ys = [float(d["y"]) for _, d in G.nodes(data=True)]

edges = list(G.edges(data=True, keys=True))
lengths = [float(d.get("length", 0)) for _, _, _, d in edges]
slope_vals = Counter()
scenery_vals = Counter()
named = 0
for _, _, _, d in edges:
    if d.get("name"):
        named += 1
    sv = d.get("slope_level")
    slope_vals[sv if sv is not None else "缺失"] += 1
    cv = d.get("scenery_level")
    scenery_vals[cv if cv is not None else "缺失"] += 1

und = G.to_undirected()
comps = list(nx.connected_components(und))
comp_sizes = sorted((len(c) for c in comps), reverse=True)

road_names = Counter(d.get("name") for _, _, _, d in edges if d.get("name"))

report["network"] = {
    "nodes": G.number_of_nodes(),
    "edges": G.number_of_edges(),
    "lng_range": [round(min(xs), 6), round(max(xs), 6)],
    "lat_range": [round(min(ys), 6), round(max(ys), 6)],
    "total_length_km": round(sum(lengths) / 1000, 2),
    "edge_len_min_m": round(min(lengths), 1),
    "edge_len_max_m": round(max(lengths), 1),
    "edge_len_mean_m": round(sum(lengths) / len(lengths), 1),
    "slope_level_dist": dict(slope_vals),
    "scenery_level_dist": dict(scenery_vals),
    "named_edges": named,
    "named_edges_pct": round(100 * named / len(edges), 1),
    "components": len(comps),
    "largest_component_nodes": comp_sizes[0],
    "isolated_small_components": [c for c in comp_sizes[1:] if c <= 5][:20],
    "road_names_top": road_names.most_common(25),
}

# ============ F. 路段标注 ============
with open("data/road_annotations.json", encoding="utf-8") as f:
    ann = json.load(f)
ann_edges = ann.get("edges", [])
meta = {k: v for k, v in ann.items() if k != "edges"}

# 统计标注内容
ann_slope = sum(1 for e in ann_edges if e.get("slope_level") is not None)
ann_scenery = sum(1 for e in ann_edges if e.get("scenery_level") is not None)
ann_name = sum(1 for e in ann_edges if e.get("name"))

report["annotations"] = {
    "meta": meta,
    "total_records": len(ann_edges),
    "with_slope": ann_slope,
    "with_scenery": ann_scenery,
    "with_name": ann_name,
    "coverage_pct_of_edges": round(100 * len(ann_edges) / G.number_of_edges(), 1),
}

# ============ 输出 ============
with open("data_validation_report.json", "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=1, default=str)

print("=== 校验摘要 ===")
print(f"POI 总数: {report['poi_total']}")
print(f"缺字段: {len(missing_fields)}  坐标超范围: {len(bad_range)}  类型非法: {len(bad_type)}")
print(f"校区归属不符: {len(campus_mismatch)}  三校区多边形外: {len(outside_all)}")
print(f"12m 内近重复坐标对: {len(close_pairs)}")
print(f"重名: {len(dup_names)}  别名跨POI冲突: {len(alias_conflicts)}  别名撞正式名: {len(alias_name_clash)}")
print(f"校门: {len(gates)}")
print(f"路网: {G.number_of_nodes()} 节点 / {G.number_of_edges()} 边, "
      f"总长 {report['network']['total_length_km']} km, 连通分量 {len(comps)}")
print(f"路段标注: {len(ann_edges)} 条 ({report['annotations']['coverage_pct_of_edges']}%)")
