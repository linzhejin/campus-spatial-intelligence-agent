"""
校园边界裁剪脚本（修复校外 POI 混入 + 路线穿城问题）

做三件事：
  1. POI 过滤：删除三学部校园多边形外的 POI（珞珈山剧院/武汉工学院/东湖食堂等校外点）
     + 名称黑名单（国家网络安全学院等 OSM 误 tag）
  2. 路网裁剪：按校园多边形（WGS-84）外扩约 120m 缓冲裁剪 graphml，
     缓冲带保证校门过街连接（信息学部↔文理学部跨珞瑜路）不被切断，
     同时切掉武珞路/珞瑜路/八一路等通往街道口/广埠屯/小洪山的城市绕行路段
  3. 标注重建：基于裁剪后路网 + 过滤后 POI 重新生成风景标注，
     并从旧标注恢复 DEM 实测坡度（裁剪只删边不增边，坡度值全部可沿用）

用法：
  python scripts/clip_to_campus.py            # 执行裁剪（自动备份）
  python scripts/clip_to_campus.py --dry-run  # 只打印将删除/保留的统计
"""

import json
import os
import shutil
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from config import CAMPUS_POLYS_GCJ, POI_NAME_BLACKLIST  # noqa: E402
from spatial.coord_transform import gcj02_to_wgs84  # noqa: E402

POIS_PATH = os.path.join(PROJECT_ROOT, "data", "pois.json")
GRAPHML = os.path.join(PROJECT_ROOT, "data", "whu_road_network.graphml")
ANN_PATH = os.path.join(PROJECT_ROOT, "data", "road_annotations.json")

# 路网裁剪缓冲（度）：约 120m，保留校门过街/校门口路段
CLIP_BUFFER_DEG = 0.0011

# 校外城市道路黑名单：即使在缓冲带内也必须移除（防止路线穿城）
# 这些路沿校园边界，缓冲带会包含它们，但导航不应走校外市政道路
OUTSIDE_ROAD_NAMES = {
    "八一路", "东湖南路", "卓刀泉北路", "广八路", "茶港路", "广卓路",
    "珞狮路", "珞狮北路", "珞瑜路", "珞喻路",
    "珞喻路辅路", "珞狮路辅路", "武珞路辅路", "武珞路",
    "卓刀泉南路", "卓刀泉路", "群光南路", "洪福巷",
    "武工路", "机电路", "科技小路", "明志路", "汇志大道", "神龙园路",
}

# 各学部腹地锚点（GCJ-02），用于重叠多边形区的学部归属消歧
_CAMPUS_ANCHORS = {
    "文理学部": (114.3610, 30.5375),
    "工学部":   (114.3715, 30.5410),
    "信息学部": (114.3755, 30.5240),
}


def _nearest_campus(lng, lat, candidates):
    """在包含该点的学部中，取离腹地锚点最近的（多边形重叠区消歧）。"""
    import math
    best, best_d = None, float("inf")
    for name in candidates:
        ax, ay = _CAMPUS_ANCHORS[name]
        dx = (lng - ax) * math.cos(math.radians(lat))
        dy = lat - ay
        d = dx * dx + dy * dy
        if d < best_d:
            best, best_d = name, d
    return best


def point_in_polygon(lng, lat, polygon):
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > lat) != (yj > lat)) and \
                (lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def in_any_campus(lng, lat, polys):
    return any(point_in_polygon(lng, lat, p) for p in polys.values())


def filter_pois(dry_run=False):
    data = json.load(open(POIS_PATH, encoding="utf-8"))
    kept, dropped = [], []
    for p in data["pois"]:
        lng, lat = p["coordinates"]["lng"], p["coordinates"]["lat"]
        reason = None
        hit = [name for name, poly in CAMPUS_POLYS_GCJ.items()
               if point_in_polygon(lng, lat, poly)]
        if not hit:
            reason = "校外"
        elif any(b in p["name"] for b in POI_NAME_BLACKLIST):
            reason = "黑名单"
        if reason:
            dropped.append((p["name"], lng, lat, reason))
        else:
            # 按多边形 + 腹地锚点重新归属学部（修正 OSM 锚点最近法的误归属）
            p["campus"] = _nearest_campus(lng, lat, hit)
            kept.append(p)

    print(f"\n== POI 过滤 ==")
    print(f"保留 {len(kept)} / 删除 {len(dropped)}")
    for name, lng, lat, reason in sorted(dropped, key=lambda x: x[3]):
        print(f"  [{reason}] {name} ({lng:.4f}, {lat:.4f})")

    if not dry_run:
        shutil.copy(POIS_PATH, POIS_PATH + ".bak-campus")
        data["pois"] = kept
        data["count"] = len(kept)
        data["source"] = data.get("source", "") + "；经校园多边形裁剪过滤校外点"
        json.dump(data, open(POIS_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("已写回 pois.json（备份 .bak-campus）")
    return len(kept), len(dropped)


def clip_network(dry_run=False):
    import networkx as nx
    from shapely.geometry import Point, Polygon
    from shapely.ops import unary_union

    # 多边形转 WGS-84（路网坐标系）
    polys_wgs = []
    for name, poly in CAMPUS_POLYS_GCJ.items():
        wgs = [gcj02_to_wgs84(lng, lat) for lng, lat in poly]
        polys_wgs.append(Polygon(wgs))
    campus_union = unary_union(polys_wgs)
    clip_poly = campus_union.buffer(CLIP_BUFFER_DEG)

    G = nx.read_graphml(GRAPHML, node_type=int)
    n0, e0 = G.number_of_nodes(), G.number_of_edges()

    if dry_run:
        inside = sum(
            1 for _, d in G.nodes(data=True)
            if clip_poly.covers(Point(float(d["x"]), float(d["y"])))
        )
        print(f"\n== 路网裁剪（dry-run）==")
        print(f"节点 {n0} → 缓冲多边形内 {inside}（将删 {n0 - inside}）")
        return

    # osmnx 2.x 已移除 truncate_graph_polygon，手动等价实现：
    # 保留缓冲多边形内节点（covers 含边界），再丢弃校外碎片小分量
    keep_nodes = {
        n for n, d in G.nodes(data=True)
        if clip_poly.covers(Point(float(d["x"]), float(d["y"])))
    }
    G_clip = G.subgraph(keep_nodes).copy()

    comps = sorted(nx.weakly_connected_components(G_clip), key=len, reverse=True)
    big = [c for c in comps if len(c) >= 20]
    if not big:
        raise RuntimeError("裁剪后无有效连通分量，请检查多边形/缓冲设置")
    main_ratio = len(comps[0]) / G_clip.number_of_nodes()
    if len(big) > 1:
        print(f"警告: 存在 {len(big)} 个大连通分量，规模: {[len(c) for c in big]}")
        print("  → 若某校区独立，说明缓冲带不足，应调大 CLIP_BUFFER_DEG")
    G_clip = G.subgraph(set().union(*big)).copy()

    # 注意：不直接移除校外道路边，因为湖滨/信息学部西区等区域需要通过
    # 东湖南路/珞瑜路等边界道路连接主路网。改由 routing.py 的高惩罚系数
    # （_OUTSIDE_ROAD_PENALTY=10.0）来避免导航走校外道路，同时保持连通性。

    n1, e1 = G_clip.number_of_nodes(), G_clip.number_of_edges()
    print(f"\n== 路网裁剪 ==")
    print(f"节点 {n0} → {n1}（删 {n0 - n1}），边 {e0} → {e1}（删 {e0 - e1}）")
    print(f"主连通分量占比: {main_ratio*100:.1f}%")

    shutil.copy(GRAPHML, GRAPHML + ".bak-campus")
    # 清洗不可序列化属性后保存
    for _, _, ed in G_clip.edges(data=True):
        for k in list(ed.keys()):
            v = ed[k]
            if isinstance(v, (list, dict, set, tuple)) or hasattr(v, "wkt"):
                ed[k] = str(v)
    for _, nd in G_clip.nodes(data=True):
        for k in list(nd.keys()):
            v = nd[k]
            if isinstance(v, (list, dict, set, tuple)) or hasattr(v, "wkt"):
                nd[k] = str(v)
    nx.write_graphml(G_clip, GRAPHML)
    print("已写回 whu_road_network.graphml（备份 .bak-campus）")


def rebuild_annotations():
    """基于裁剪后路网 + 过滤后 POI 重新生成标注，并恢复旧 DEM 实测坡度。"""
    # 先备份含 DEM 的旧标注
    old = json.load(open(ANN_PATH, encoding="utf-8"))
    dem_by_pair = {}
    for e in old["edges"]:
        note = e.get("note") or ""
        if "DEM实测" in note:
            dem_by_pair[(e["u"], e["v"])] = (e["slope_level"], note.split(";")[0].strip())

    # 重新生成占位标注（内部从裁剪后 graphml + 过滤后 pois.json 读取）
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))
    import importlib
    import generate_placeholder_annotations as gen
    importlib.reload(gen)
    gen.main()

    # 恢复 DEM 实测
    new = json.load(open(ANN_PATH, encoding="utf-8"))
    restored = 0
    for e in new["edges"]:
        hit = dem_by_pair.get((e["u"], e["v"]))
        if hit:
            level, dem_note = hit
            e["slope_level"] = level
            tail = e["note"].split(";", 1)[1].strip() if ";" in e["note"] else ""
            e["note"] = dem_note + (f"; {tail}" if tail else "")
            restored += 1
    new["slope_source"] = old.get("slope_source", "")
    json.dump(new, open(ANN_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n== 标注重建 ==")
    print(f"新标注 {len(new['edges'])} 条边，恢复 DEM 实测坡度 {restored} 条")


def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        filter_pois(dry_run=True)
        clip_network(dry_run=True)
        print("\n[dry-run] 未写文件")
        return

    filter_pois()
    clip_network()
    rebuild_annotations()
    print("\n裁剪完成。建议运行: python -m pytest tests -q")


if __name__ == "__main__":
    main()
