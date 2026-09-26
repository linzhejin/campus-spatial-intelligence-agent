# -*- coding: utf-8 -*-
"""数据真实性/准确性全面校验，输出 data_validation_report.json。

校验项：
A. POI 结构完整性、坐标范围、坐标系一致性
B. POI 点在所属校区多边形内（GCJ-02）
C. 重复坐标/重复名称检测
D. 校门专项（按正式库实际条目统计）
E. 路网 GraphML：节点/边、坐标范围、边属性分布、连通性
F. 路段标注 road_annotations.json 覆盖率
"""
import json
import math
import os
import sys
from collections import Counter, defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import networkx as nx
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform as shapely_transform

from config import CAMPUS_POLYS_GCJ
from spatial.coord_transform import gcj02_to_wgs84

report = {}

# ============ A. POI 基础校验 ============
with open(os.path.join(PROJECT_ROOT, "data", "pois.json"), encoding="utf-8") as f:
    pdata = json.load(f)
pois = pdata["pois"]

valid_types = {"gate", "study", "dorm", "dining", "sports", "scenery", "service", "area"}
valid_campus = {"文理学部", "工学部", "信息学部"}
OSM_CAMPUS_NAMES = {
    "武汉大学信息学部": "信息学部",
    "武汉大学文理学部": "文理学部",
    "武汉大学工学部": "工学部",
}
OSM_BOUNDARIES_PATH = os.path.join(
    PROJECT_ROOT, "scripts", "fetch", "osm_campus_boundaries.geojson"
)
OSM_TO_METERS = Transformer.from_crs(4326, 32650, always_xy=True).transform
OSM_BOUNDARIES = {}
if os.path.exists(OSM_BOUNDARIES_PATH):
    with open(OSM_BOUNDARIES_PATH, encoding="utf-8") as f:
        for feature in json.load(f).get("features", []):
            props = feature.get("properties", {})
            campus = OSM_CAMPUS_NAMES.get(props.get("name"))
            if campus and props.get("osm_id") and feature.get("geometry"):
                OSM_BOUNDARIES[props["osm_id"]] = (
                    campus, shapely_transform(OSM_TO_METERS, shape(feature["geometry"]))
                )

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


def which_campus_for_poi(poi, lng, lat):
    """Prefer a reviewed OSM campus boundary ref over overlapping legacy polygons.

    POI markers on campus boundaries can differ from the mapped polygon by a
    few metres. A 15 m allowance is used only for a POI carrying an explicit
    OSM boundary reference; legacy entries continue to use the original
    configured campus polygons.
    """
    refs = poi.get("campus_boundary_refs") or []
    if refs:
        wgs_lng, wgs_lat = gcj02_to_wgs84(float(lng), float(lat))
        point = shapely_transform(OSM_TO_METERS, Point(wgs_lng, wgs_lat))
        for ref in refs:
            evidence = OSM_BOUNDARIES.get(ref)
            if evidence:
                campus, polygon = evidence
                if polygon.distance(point) <= 15.0:
                    return campus
    return which_campus(lng, lat)

missing_fields = []
bad_range = []
bad_type = []
bad_campus = []
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
    if p.get("campus") not in valid_campus and not (
            p.get("type") == "area" and p.get("campus") == "whu"):
        bad_campus.append((pid, p["name"], p.get("campus")))
    # 校区归属
    claimed = p.get("campus")
    actual = which_campus_for_poi(p, lng, lat)
    if actual is None:
        outside_all.append((pid, p["name"], claimed, lng, lat))
    elif actual != claimed and not (p.get("type") == "area" and claimed == "whu"):
        campus_mismatch.append((pid, p["name"], claimed, actual, lng, lat))

report["poi_total"] = len(pois)
report["poi_declared_count"] = pdata.get("count")
report["poi_count_matches_declared"] = pdata.get("count") == len(pois)
report["poi_by_type"] = dict(Counter(p["type"] for p in pois))
report["poi_by_campus"] = dict(Counter(p["campus"] for p in pois))
report["poi_rated"] = sum(1 for p in pois if p.get("rating"))
report["missing_fields"] = missing_fields
report["bad_range"] = bad_range
report["bad_type"] = bad_type
report["bad_campus"] = bad_campus
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
G = nx.read_graphml(os.path.join(PROJECT_ROOT, "data", "whu_road_network.graphml"), node_type=int)
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
with open(os.path.join(PROJECT_ROOT, "data", "road_annotations.json"), encoding="utf-8") as f:
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
    "placeholder_records": sum("占位" in str(e.get("note", "")) for e in ann_edges),
}

# ============ 输出 ============
with open(os.path.join(PROJECT_ROOT, "data_validation_report.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=1, default=str)

print("=== 校验摘要 ===")
print(f"POI 总数: {report['poi_total']}")
print(f"缺字段: {len(missing_fields)}  坐标超范围: {len(bad_range)}  类型非法: {len(bad_type)}  学部字段非法: {len(bad_campus)}")
print(f"校区归属不符: {len(campus_mismatch)}  三校区多边形外: {len(outside_all)}")
print(f"12m 内近重复坐标对: {len(close_pairs)}")
print(f"重名: {len(dup_names)}  别名跨POI冲突: {len(alias_conflicts)}  别名撞正式名: {len(alias_name_clash)}")
print(f"校门: {len(gates)}")
print(f"路网: {G.number_of_nodes()} 节点 / {G.number_of_edges()} 边, "
      f"总长 {report['network']['total_length_km']} km, 连通分量 {len(comps)}")
print(f"路段标注: {len(ann_edges)} 条 ({report['annotations']['coverage_pct_of_edges']}%)")
print(f"其中占位说明: {report['annotations']['placeholder_records']} 条（未按实测处理）")

critical = (missing_fields or bad_range or bad_type or bad_campus or campus_mismatch or outside_all
            or dup_names or alias_conflicts or alias_name_clash
            or not report["poi_count_matches_declared"])
if critical:
    sys.exit(1)
