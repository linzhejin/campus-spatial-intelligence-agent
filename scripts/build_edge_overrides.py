# -*- coding: utf-8 -*-
"""
基于路网审计结果生成人工覆盖文件 data/edge_overrides.json

分层处理穿楼边（审计脚本检出 210 条有向记录 / 约百条物理边）：
  1. 车行道硬封禁（bike/drive）：无名道路 + 楼内长度≥15m + 占比≥60%
     —— 机动车不可能穿楼，无名 service/unclassified 多为画进楼体的错线/后勤穿堂
  2. 命名道路（如"求是一路"）一律不自动封禁——高概率是 OSM 建筑轮廓圈大盖住了
     主路，仅列入待人工复核清单 scripts/audit_output/named_review.json
  3. 全部穿楼候选边统一加 walk_penalty=10（软惩罚，与食堂穿楼同策略）：
     步行仍可走楼间连廊，但不再成为穿楼捷径

用法：python scripts/build_edge_overrides.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import networkx as nx
from shapely.ops import transform as sh_transform
from shapely.wkt import loads as wkt_loads

from spatial.network import load_or_download_network, get_node_coords
from spatial.routing import _edge_highway_tags

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit_output")
UTM = "EPSG:32650"
CROSS_M = 6.0
CROSS_RATIO = 0.45
HARD_M = 15.0
HARD_RATIO = 0.60
WALK_PENALTY = 10.0
BENIGN_TAGS = {"steps", "elevator", "escalator", "corridor", "platform"}
VEHICULAR_TAGS = {
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential",
    "service", "living_street", "road", "busway",
}


def edge_geom(G, u, v, data):
    raw = data.get("geometry")
    if raw and isinstance(raw, str):
        try:
            return wkt_loads(raw)
        except Exception:
            pass
    a = get_node_coords(G, u)
    b = get_node_coords(G, v)
    from shapely.geometry import LineString
    return LineString([a, b])


def to_utm(geom):
    from pyproj import Transformer
    t = Transformer.from_crs("EPSG:4326", UTM, always_xy=True)
    return sh_transform(t.transform, geom)


def fetch_buildings():
    import osmnx as ox
    try:
        from config import WHU_BBOX
    except ImportError:
        WHU_BBOX = {"north": 30.548, "south": 30.528, "east": 114.375, "west": 114.35}
    b = WHU_BBOX
    try:
        return ox.features_from_bbox(
            bbox=(b["west"], b["south"], b["east"], b["north"]),
            tags={"building": True})
    except TypeError:
        return ox.features_from_bbox(
            north=b["north"], south=b["south"], east=b["east"], west=b["west"],
            tags={"building": True})


def is_named(d):
    nm = str(d.get("name", "")).strip()
    return bool(nm) and "未命名" not in nm


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    G = load_or_download_network()
    gdf = fetch_buildings()
    print(f"建筑 {len(gdf)} 个")

    buildings = []
    for _, row in gdf.iterrows():
        try:
            g = row.geometry
            if g and not g.is_empty:
                buildings.append({
                    "geom": to_utm(g),
                    "building": str(row.get("building", "")) if row.get("building") is not None else "",
                    "name": str(row.get("name", "")) if row.get("name") is not None else "",
                    "levels": str(row.get("building:levels", "")) if row.get("building:levels") is not None else "",
                })
        except Exception:
            continue

    hard_keys = set()       # (u,v,k) 车行道硬封禁
    soft_keys = set()       # (u,v,k) 步行软惩罚
    named_review = []
    raw_records = []

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
        hits = []
        for b in buildings:
            try:
                inter = gu.intersection(b["geom"])
                if not inter.is_empty and inter.length > 1:
                    inside += inter.length
                    hits.append({"m": round(inter.length, 1), "building": b["building"],
                                 "name": b["name"], "levels": b["levels"]})
            except Exception:
                continue
        if total <= 0 or inside < CROSS_M or inside / total < CROSS_RATIO:
            continue

        rec = {
            "u": u, "v": v, "k": k, "tags": tags,
            "name": str(d.get("name", "")),
            "length": round(total, 1), "inside_m": round(inside, 1),
            "ratio": round(inside / total, 2), "hits": hits[:3],
        }
        raw_records.append(rec)

        named = is_named(d)
        vehicular = any(t in VEHICULAR_TAGS for t in tags)
        if named:
            named_review.append(rec)
        else:
            # 无名边：步行软惩罚；车行道且高置信时再对 bike/drive 硬封禁
            soft_keys.add((u, v, k))
            if vehicular and inside >= HARD_M and inside / total >= HARD_RATIO:
                hard_keys.add((u, v, k))

    # 双向补齐：检测只在一个方向命中时，反方向同几何边同样处理
    edge_index = {}
    for u, v, k, d in G.edges(keys=True, data=True):
        edge_index.setdefault((u, v, k), d)
    for keyset in (hard_keys, soft_keys):
        for (u, v, k) in list(keyset):
            rev = (v, u, k)
            if rev in edge_index and rev not in keyset:
                keyset.add(rev)

    overrides = []
    all_keys = hard_keys | soft_keys
    for (u, v, k) in sorted(all_keys):
        d = edge_index.get((u, v, k), {})
        entry = {
            "u": u, "v": v, "k": k,
            "walk_penalty": WALK_PENALTY,
            "reason": "crosses_building",
            "name_hint": str(d.get("name", ""))[:40],
        }
        if (u, v, k) in hard_keys:
            entry["blocked_modes"] = ["bike", "drive"]
        overrides.append(entry)

    out = {
        "_comment": "人工路网覆盖：blocked_modes 硬封禁模式；walk_penalty 步行软惩罚倍数。"
                    "由 scripts/build_edge_overrides.py 依据 OSM 建筑轮廓相交检测生成，"
                    "命名道路不自动封禁。",
        "edges": overrides,
    }
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "edge_overrides.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    with open(os.path.join(OUT_DIR, "named_review.json"), "w", encoding="utf-8") as f:
        json.dump(named_review, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT_DIR, "cross_building_full.json"), "w", encoding="utf-8") as f:
        json.dump(raw_records, f, ensure_ascii=False, indent=1)

    print(f"穿楼候选有向边 {len(raw_records)} 条")
    print(f"  → 步行软惩罚（含双向）: {len(soft_keys)} 条")
    print(f"  → 骑行/驾车硬封禁（含双向）: {len(hard_keys)} 条")
    print(f"  → 命名道路待人工复核（不自动处理）: {len(named_review)} 条")
    print(f"overrides 条目（去重合并）: {len(overrides)} 条 → {path}")
    print("\n命名道路复核清单：")
    for r in named_review:
        print(f"  {r['name'][:20]:20s} {r['tags']} 楼内{r['inside_m']}m/{r['length']}m "
              f"({int(r['ratio']*100)}%) hits={[h['name'] or h['building'] for h in r['hits']]}")


if __name__ == "__main__":
    main()
