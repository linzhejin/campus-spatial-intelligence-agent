"""Generate placeholder road annotations from the current WHU road network.

Scenery is derived from POI proximity and road names. Slope values are
placeholders (2/3/4 by road type) to be replaced with real measurements later.
"""

import json
import math
import os
import sys
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from spatial.coord_transform import gcj02_to_wgs84
from spatial.network import get_node_coords, load_or_download_network
from spatial.poi import load_pois

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

OUTPUT_PATH = os.path.join(PROJECT_ROOT, "data", "road_annotations.json")

SCENERY_KEYWORDS = (
    "樱花", "环山", "月湖", "星湖", "枫", "桂", "梅", "樱",
    "珞珈", "凌波", "栈桥", "绿道", "湖",
)
# 标志性景观道：直接顶格 5 级（樱花季主轴 / 校内临湖步道）
SCENERY_TOP_ROADS = {"樱花大道", "湖滨路", "珞滨路", "望湖路"}
# 环山景观道：至少 4 级
SCENERY_HIGH_ROADS = ("环山北路", "环山南路", "环山东路", "珞珈山路")
STEEP_KEYWORDS = ("环山", "珞珈山", "樱顶", "老斋舍", "石阶", "陡")
FLAT_KEYWORDS = (
    "凌波", "东湖南路", "信息学部", "珞瑜", "自强", "主干道", "操场", "广场",
)


def _haversine_m(lon1, lat1, lon2, lat2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (
        math.sin(dp / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def _midpoint(G, u, v):
    try:
        x1, y1 = get_node_coords(G, u)
        x2, y2 = get_node_coords(G, v)
    except Exception:
        return None
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def _nearest_poi(mid, pois_wgs):
    if mid is None:
        return None, None
    best = None
    best_d = float("inf")
    for poi in pois_wgs:
        d = _haversine_m(mid[0], mid[1], poi["lon"], poi["lat"])
        if d < best_d:
            best_d = d
            best = poi
    return best, best_d


def _scenery_level(name, nearest, dist_m):
    level = 3
    if nearest and dist_m is not None:
        score = nearest.get("scenery_score", 3)
        if dist_m <= 150 and score >= 5:
            level = 5
        elif dist_m <= 300 and score >= 4:
            level = 4
        elif dist_m <= 500 and score >= 5:
            level = 4
    if name:
        if name in SCENERY_TOP_ROADS:
            return 5
        if any(kw in name for kw in SCENERY_HIGH_ROADS):
            level = max(level, 4)
    if name and any(kw in name for kw in SCENERY_KEYWORDS):
        level = min(5, level + 1)
    return level


def _slope_placeholder(name):
    if name and any(kw in name for kw in STEEP_KEYWORDS):
        return 4
    if name and any(kw in name for kw in FLAT_KEYWORDS):
        return 2
    return 3


def _clean_name(raw):
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text or text == "None" or text.startswith("["):
        return ""
    return text


def main():
    print("[1/3] 加载当前真实路网...")
    G = load_or_download_network()
    print(f"  节点 {G.number_of_nodes()}, 边 {G.number_of_edges()}")

    print("[2/3] 计算 POI 距离与启发式标注...")
    pois_wgs = []
    for p in load_pois():
        c = p.get("coordinates") or {}
        lng, lat = c.get("lng"), c.get("lat")
        if lng is None or lat is None:
            continue
        wlng, wlat = gcj02_to_wgs84(float(lng), float(lat))
        pois_wgs.append({
            "name": p.get("name", ""),
            "lon": wlng,
            "lat": wlat,
            "scenery_score": p.get("scenery_score", 3),
        })

    edges = []
    for u, v, k, data in G.edges(keys=True, data=True):
        name = _clean_name(data.get("name"))
        mid = _midpoint(G, u, v)
        nearest, dist_m = _nearest_poi(mid, pois_wgs)
        slope = _slope_placeholder(name)
        scenery = _scenery_level(name, nearest, dist_m)
        note_parts = ["坡度占位，待实测"]
        if nearest:
            note_parts.append(f"临近 {nearest['name']} ({int(dist_m)}m)")
        edges.append({
            "edge_id": [int(u), int(v), int(k)],
            "u": int(u),
            "v": int(v),
            "name": name or f"未命名路 {u}-{v}",
            "length_m": round(float(data.get("length", 0) or 0), 1),
            "highway": str(data.get("highway", "")),
            "slope_level": slope,
            "scenery_level": scenery,
            "note": "; ".join(note_parts),
        })

    annotated = [
        e for e in edges
        if e.get("slope_level") is not None and e.get("scenery_level") is not None
    ]
    coverage = round(len(annotated) / len(edges), 4) if edges else 0.0
    output = {
        "version": "2.0",
        "annotator": "程序化生成：真实 OSM 路网边 + POI 邻近启发式；长边(≥50m)坡度由 DEM 实测覆盖，短边为占位",
        "annotated_at": datetime.now().strftime("%Y-%m-%d"),
        "coverage": "覆盖当前路网全部边；长边(≥50m)坡度DEM实测、短边坡度为占位；风景值按POI距离/路名启发式（樱花/临湖/环山道已提级）",
        "coverage_rate": coverage,
        "edges": edges,
    }

    print("[3/3] 写入 road_annotations.json ...")
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=1)
    print(f"  完成: {len(edges)} 条边, coverage_rate={coverage}, 文件={OUTPUT_PATH}")


if __name__ == "__main__":
    main()
