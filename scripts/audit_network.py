# -*- coding: utf-8 -*-
"""
路网-底图匹配审计脚本

排查两类线上问题：
  1. 路线穿过建筑（OSM 路网线位与高德卫星底图不符 / 穿楼捷径）
  2. 骑行/驾车路线经过楼梯（台阶被错标成车行道，或过滤失效）

产出：
  - 控制台统计报告
  - scripts/audit_output/network_audit.html  卫星叠加可视化（人工核查用）
  - scripts/audit_output/audit_issues.json   问题边机器清单（供 edge_overrides 录入参考）

用法：python scripts/audit_network.py
首次运行会从 OSM 下载路网与建筑轮廓，约 1~2 分钟。
"""
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import networkx as nx
from shapely.geometry import LineString, shape
from shapely.ops import transform as sh_transform
from shapely.wkt import loads as wkt_loads

from spatial.network import load_or_download_network, get_node_coords
from spatial.routing import filter_graph_for_mode, _edge_highway_tags, _road_name_list
from spatial.coord_transform import wgs84_to_gcj02

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit_output")
os.makedirs(OUT_DIR, exist_ok=True)

# UTM 50N（武大 114.3E），米制投影，用于长度/距离计算
UTM = "EPSG:32650"

# 穿楼判定：边在建筑 footprint 内的长度阈值与占比阈值
CROSS_M = 6.0
CROSS_RATIO = 0.45
# 与台阶几何贴行（疑似错标车行道）阈值
STEPS_NEAR_M = 3.0

# 这些标签贴楼/穿楼属于正常（垂直交通、建筑内通道、隧道）
BENIGN_TAGS = {"steps", "elevator", "escalator", "corridor", "platform"}


def edge_geom(G, u, v, data):
    """取边几何（shapely LineString, WGS-84）。"""
    raw = data.get("geometry")
    if raw and isinstance(raw, str):
        try:
            return wkt_loads(raw)
        except Exception:
            pass
    a = get_node_coords(G, u)
    b = get_node_coords(G, v)
    return LineString([a, b])


def to_utm(geom):
    try:
        from pyproj import Transformer
        t = Transformer.from_crs("EPSG:4326", UTM, always_xy=True)
        return sh_transform(t.transform, geom)
    except Exception:
        return geom


def highway_dist(G):
    c = Counter()
    for _, _, d in G.edges(keys=False, data=True):
        tags = _edge_highway_tags(d) or ["<空>"]
        for t in tags:
            c[t] += 1
    return c


def fetch_buildings():
    """从 OSM 下载校园建筑轮廓（WGS-84 GeoDataFrame）。"""
    import osmnx as ox
    try:
        from config import WHU_BBOX
    except ImportError:
        WHU_BBOX = {"north": 30.5480, "south": 30.5280, "east": 114.3750, "west": 114.3500}
    b = WHU_BBOX
    try:
        gdf = ox.features_from_bbox(
            bbox=(b["west"], b["south"], b["east"], b["north"]),
            tags={"building": True},
        )
    except TypeError:
        gdf = ox.features_from_bbox(
            north=b["north"], south=b["south"], east=b["east"], west=b["west"],
            tags={"building": True},
        )
    return gdf


def main():
    print("=" * 60)
    print("1. 加载路网")
    print("=" * 60)
    G = load_or_download_network()
    print(f"节点 {G.number_of_nodes()}  有向边 {G.number_of_edges()}")

    dist = highway_dist(G)
    print("\nhighway 标签分布：")
    for tag, n in dist.most_common():
        print(f"  {tag:20s} {n}")

    # ---------------------------------------------------------------- steps
    print("\n" + "=" * 60)
    print("2. 台阶/垂直交通边清单")
    print("=" * 60)
    steps_edges = []
    for u, v, k, d in G.edges(keys=True, data=True):
        tags = _edge_highway_tags(d)
        if any(t in {"steps", "elevator", "escalator"} for t in tags):
            steps_edges.append((u, v, k, d, tags))
    print(f"steps/elevator/escalator 边共 {len(steps_edges)} 条")
    for u, v, k, d, tags in steps_edges[:15]:
        nm = d.get("name", "")
        print(f"  ({u},{v},{k}) tags={tags} name={nm} len={d.get('length')}")
    if len(steps_edges) > 15:
        print(f"  … 其余 {len(steps_edges) - 15} 条略")

    # ------------------------------------------------- 模式过滤有效性
    print("\n" + "=" * 60)
    print("3. 各模式过滤有效性验证")
    print("=" * 60)
    filter_report = {}
    for mode in ("walk", "bike", "drive"):
        Gm, status, pen = filter_graph_for_mode(G, mode)
        bad = []
        for u, v, k, d in Gm.edges(keys=True, data=True):
            tags = _edge_highway_tags(d)
            if mode == "bike" and tags and all(t in {"steps", "elevator", "escalator"} for t in tags):
                bad.append((u, v, k, tags))
            if mode == "drive" and not any(t in {
                "motorway", "motorway_link", "trunk", "trunk_link",
                "primary", "primary_link", "secondary", "secondary_link",
                "tertiary", "tertiary_link", "unclassified", "residential",
                "service", "living_street", "road",
            } for t in tags):
                bad.append((u, v, k, tags))
        filter_report[mode] = (Gm.number_of_edges(), status, len(bad))
        print(f"  {mode:5s}: 剩余边 {Gm.number_of_edges():5d}  status={status}  残留违规边={len(bad)}")
        for item in bad[:10]:
            print(f"      违规: {item}")

    # ------------------------------------------------- 贴台阶车行道
    print("\n" + "=" * 60)
    print("4. 与台阶几何贴行的疑似错标边（drive/bike 视角）")
    print("=" * 60)
    steps_geom_utm = []
    for u, v, k, d, _ in steps_edges:
        g = edge_geom(G, u, v, d)
        if g:
            steps_geom_utm.append((u, v, k, to_utm(g)))

    near_steps = []
    Gd, _, _ = filter_graph_for_mode(G, "drive")
    for u, v, k, d in Gd.edges(keys=True, data=True):
        g = edge_geom(Gd, u, v, d)
        if not g:
            continue
        gu = to_utm(g)
        for su, sv, sk, sg in steps_geom_utm:
            if (su == u and sv == v) or (su == v and sv == u):
                continue
            try:
                if gu.distance(sg) <= STEPS_NEAR_M:
                    near_steps.append({
                        "u": u, "v": v, "k": k,
                        "tags": _edge_highway_tags(d),
                        "name": str(d.get("name", "")),
                        "length": round(float(d.get("length", 0)), 1),
                        "near_steps": [su, sv, sk],
                    })
                    break
            except Exception:
                pass
    print(f"drive 图中与台阶 ≤{STEPS_NEAR_M}m 的边 {len(near_steps)} 条（需卫星图人工确认）")
    for e in near_steps[:20]:
        print(f"  ({e['u']},{e['v']},{e['k']}) {e['tags']} {e['name']} {e['length']}m")

    # ------------------------------------------------- 建筑相交
    print("\n" + "=" * 60)
    print("5. 路网穿建筑检测（OSM building footprints）")
    print("=" * 60)
    buildings = []
    try:
        gdf = fetch_buildings()
        print(f"下载建筑轮廓 {len(gdf)} 个")
        for _, row in gdf.iterrows():
            try:
                geom = row.geometry
                if geom and not geom.is_empty:
                    buildings.append(to_utm(geom))
            except Exception:
                continue
    except Exception as e:
        print(f"建筑轮廓下载失败，跳过穿楼检测: {e}")

    cross_building = []
    if buildings:
        for u, v, k, d in G.edges(keys=True, data=True):
            tags = _edge_highway_tags(d)
            if any(t in BENIGN_TAGS for t in tags):
                continue
            g = edge_geom(G, u, v, d)
            if not g:
                continue
            gu = to_utm(g)
            total = gu.length
            inside = 0.0
            hit_names = []
            for b in buildings:
                try:
                    inter = gu.intersection(b)
                    if not inter.is_empty:
                        inside += inter.length
                        if inter.length > 1:
                            hit_names.append(round(inter.length, 1))
                except Exception:
                    continue
            if total > 0 and inside >= CROSS_M and inside / total >= CROSS_RATIO:
                cross_building.append({
                    "u": u, "v": v, "k": k,
                    "tags": tags,
                    "name": str(d.get("name", "")),
                    "length": round(total, 1),
                    "inside_m": round(inside, 1),
                    "ratio": round(inside / total, 2),
                })
        print(f"穿楼候选边 {len(cross_building)} 条（建筑内 {CROSS_M}m 以上且占比 ≥{CROSS_RATIO}）")
        for e in cross_building[:30]:
            print(f"  ({e['u']},{e['v']},{e['k']}) {e['tags']} {e['name']} "
                  f"{e['length']}m 楼内{e['inside_m']}m {int(e['ratio']*100)}%")

    # ------------------------------------------------- JSON 产物
    issues = {
        "near_steps_edges": near_steps,
        "cross_building_edges": cross_building,
        "steps_edges": [[u, v, k] for u, v, k, _, _ in steps_edges],
        "counts": {"nodes": G.number_of_nodes(), "edges": G.number_of_edges(),
                   "highway": dict(dist)},
    }
    json_path = os.path.join(OUT_DIR, "audit_issues.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(issues, f, ensure_ascii=False, indent=1)
    print(f"\n问题清单已写入 {json_path}")

    # ------------------------------------------------- HTML 可视化
    write_html(G, buildings_utm=buildings, steps_edges=steps_edges,
               near_steps=near_steps, cross_building=cross_building)
    print(f"可视化已写入 {os.path.join(OUT_DIR, 'network_audit.html')}")


def _coords_gcj(G, u, v, data):
    g = edge_geom(G, u, v, data)
    if not g:
        return []
    return [[wgs84_to_gcj02(x, y)[1], wgs84_to_gcj02(x, y)[0]] for x, y in g.coords]


def write_html(G, buildings_utm, steps_edges, near_steps, cross_building):
    all_edges = []
    for u, v, k, d in G.edges(keys=True, data=True):
        cs = _coords_gcj(G, u, v, d)
        if cs:
            all_edges.append(cs)

    steps_set = [_coords_gcj(G, u, v, d) for u, v, k, d, _ in steps_edges]
    steps_set = [c for c in steps_set if c]

    near_keys = {(e["u"], e["v"], e["k"]) for e in near_steps}
    cross_keys = {(e["u"], e["v"], e["k"]) for e in cross_building}
    near_lines, cross_lines, cross_meta = [], [], []
    for u, v, k, d in G.edges(keys=True, data=True):
        cs = _coords_gcj(G, u, v, d)
        if not cs:
            continue
        if (u, v, k) in cross_keys:
            cross_lines.append(cs)
            m = next(e for e in cross_building if e["u"] == u and e["v"] == v and e["k"] == k)
            cross_meta.append({"coords": cs, "info": f"{m['tags']} {m['name']} 楼内{m['inside_m']}m/{m['length']}m"})
        elif (u, v, k) in near_keys:
            near_lines.append(cs)

    # 建筑轮廓转回 WGS 再转 GCJ（简化：直接从 UTM 反投影）
    building_coords = []
    try:
        from pyproj import Transformer
        from shapely.ops import transform as sh_transform2
        t = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)
        for bu in buildings_utm:
            bw = sh_transform2(t.transform, bu)
            if bw.geom_type == "Polygon":
                rings = [list(bw.exterior.coords)]
            elif bw.geom_type == "MultiPolygon":
                rings = [list(p.exterior.coords) for p in bw.geoms]
            else:
                continue
            for ring in rings:
                building_coords.append([[wgs84_to_gcj02(x, y)[1], wgs84_to_gcj02(x, y)[0]] for x, y in ring])
    except Exception:
        pass

    payload = {
        "all": all_edges, "steps": steps_set,
        "near": near_lines, "cross": cross_meta, "buildings": building_coords,
    }
    html = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>路网审计 - 珞珈智行</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>html,body{margin:0;height:100%}#map{height:100%}
.legend{position:fixed;top:10px;left:10px;background:#fff;padding:10px 14px;border-radius:10px;
font:13px/1.8 system-ui;box-shadow:0 2px 12px rgba(0,0,0,.2);z-index:999}
.sw{display:inline-block;width:22px;height:4px;vertical-align:middle;margin-right:6px}</style>
</head><body><div id="map"></div>
<div class="legend"><b>路网-卫星叠加审计</b><br>
<span class="sw" style="background:#888"></span>全部路网<br>
<span class="sw" style="background:#f5a623"></span>台阶 steps<br>
<span class="sw" style="background:#7b2ff7"></span>贴台阶疑似错标<br>
<span class="sw" style="background:#e53935"></span>穿楼候选<br>
<span class="sw" style="background:rgba(233,30,99,.25)"></span>OSM 建筑轮廓<br>
<small>底图：高德卫星（GCJ-02）</small></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const DATA = __PAYLOAD__;
const map = L.map('map').setView([30.5365, 114.3625], 16);
L.tileLayer('https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}',{
 subdomains:['1','2','3','4'],maxZoom:20,attribution:'高德卫星'}).addTo(map);
L.tileLayer('https://webst0{s}.is.autonavi.com/appmaptile?style=8&x={x}&y={y}&z={z}',{
 subdomains:['1','2','3','4'],maxZoom:20,opacity:.65}).addTo(map);
DATA.buildings.forEach(c=>L.polygon(c,{color:'#e91e63',weight:1,fillColor:'#e91e63',fillOpacity:.12}).addTo(map));
DATA.all.forEach(c=>L.polyline(c,{color:'#888',weight:2,opacity:.55}).addTo(map));
DATA.steps.forEach(c=>L.polyline(c,{color:'#f5a623',weight:5,opacity:.9}).addTo(map));
DATA.near.forEach(c=>L.polyline(c,{color:'#7b2ff7',weight:5,opacity:.95}).addTo(map));
DATA.cross.forEach(e=>L.polyline(e.coords,{color:'#e53935',weight:6,opacity:.95})
 .bindPopup(e.info).addTo(map));
</script></body></html>"""
    html = html.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False))
    with open(os.path.join(OUT_DIR, "network_audit.html"), "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    main()
