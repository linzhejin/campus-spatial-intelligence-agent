"""
从 DEM（open-elevation 免费 API）自动计算路段坡度，更新 road_annotations.json 的 slope_level。

背景：
  - 现有 road_annotations.json 中 97% 的边 slope_level=3 是占位值（note 标注「坡度占位，非实测」），
    导致「避开陡坡」硬约束（过滤 slope=5 / 惩罚 slope=4）几乎无数据支撑。
  - 本脚本用 open-elevation 的 SRTM/ASTER 高程计算真实坡度。

关键约束与对策：
  - DEM 分辨率约 30m，而 OSM 步行网中位边长仅 24m，node-to-node 直接算会在陡崖处产生假「悬崖」
    （如樱顶南坡相邻单元格 101m→40m，41m 边算出 149% 坡度）。
  - 对策①：只对长度 ≥ MIN_LEN_M 的主干道计算（短段坡度在 30m DEM 下不可靠，保持默认 3）。
  - 对策②：先对节点高程做半径内中位数平滑，抑制单个 DEM 单元格的跳变。

用法：
  python scripts/compute_slope_from_dem.py            # 计算并写回（自动备份）
  python scripts/compute_slope_from_dem.py --dry-run  # 只打印分布，不写文件
"""

import json
import math
import os
import sys
import time
from collections import Counter

import httpx
import networkx as nx

MIN_LEN_M = 50.0        # 只更新长度 ≥ 50m 的主干道
SMOOTH_RADIUS_M = 60.0  # 节点高程中位数平滑半径（米）
BATCH_SIZE = 200        # open-elevation 单次批量点数
REQUEST_DELAY = 0.3     # 请求间隔（秒），避免限流

# 坡度分级阈值（gradient = rise / run，即 |Δelev| / length）
SLOPE_BINS = [
    (0.02, 1),      # < 2%   平坦
    (0.05, 2),      # 2-5%   缓坡
    (0.08, 3),      # 5-8%   中等
    (0.15, 4),      # 8-15%  较陡
    (math.inf, 5),  # ≥15%   很陡
]

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPHML = os.path.join(PROJECT_ROOT, "data", "whu_road_network.graphml")
ANN_PATH = os.path.join(PROJECT_ROOT, "data", "road_annotations.json")
ELEV_API = "https://api.open-elevation.com/api/v1/lookup"


def slope_level(gradient: float) -> int:
    for threshold, level in SLOPE_BINS:
        if gradient < threshold:
            return level
    return 5


def fetch_elevations(points):
    """points: [(lat, lng), ...] -> {index: elevation}，失败点自动重试。"""
    out = {}
    pending = list(range(len(points)))
    for attempt in range(3):
        if not pending:
            break
        still = []
        for start in range(0, len(pending), BATCH_SIZE):
            chunk = pending[start:start + BATCH_SIZE]
            locs = [
                {"latitude": points[i][0], "longitude": points[i][1]}
                for i in chunk
            ]
            try:
                r = httpx.post(ELEV_API, json={"locations": locs}, timeout=60)
                r.raise_for_status()
                results = r.json().get("results", [])
                for i, item in zip(chunk, results):
                    if item and item.get("elevation") is not None:
                        out[i] = float(item["elevation"])
                    else:
                        still.append(i)
            except Exception as e:
                print(f"    批量请求异常（第 {attempt + 1} 轮）: {e}")
                still.extend(chunk)
            time.sleep(REQUEST_DELAY)
        pending = still
        if pending:
            print(f"    {len(pending)} 个点待重试...")
    return out


def smooth_elevations(node_ids, points, elev, radius_m=SMOOTH_RADIUS_M):
    """
    用半径内节点的中位数高程做空间平滑，抑制 DEM 单元格跳变。

    返回 {node_id: 平滑后的高程}。
    """
    import numpy as np

    lats = np.array([p[0] for p in points])
    lngs = np.array([p[1] for p in points])
    lat0 = float(lats.mean())
    lng0 = float(lngs.mean())
    # 局部等距圆柱投影到米
    y = (lats - lat0) * 111000.0
    x = (lngs - lng0) * 111000.0 * math.cos(math.radians(lat0))
    coords = np.column_stack([x, y])

    h = np.array([elev.get(n, np.nan) for n in node_ids], dtype=float)

    try:
        from scipy.spatial import cKDTree
        tree = cKDTree(coords)
        neighbors = tree.query_ball_point(coords, r=radius_m)
    except ImportError:
        # 无 scipy 时退化为暴力距离（n 较小）
        dist2 = ((coords[:, None, :] - coords[None, :, :]) ** 2).sum(-1)
        neighbors = [np.where(d <= radius_m ** 2)[0] for d in dist2]

    out = {}
    for i, n in enumerate(node_ids):
        vals = h[neighbors[i]]
        vals = vals[~np.isnan(vals)]
        if len(vals):
            out[n] = float(np.median(vals))
        elif n in elev:
            out[n] = elev[n]
    return out


def main(dry_run=False):
    print("加载路网节点坐标（WGS-84）...")
    G = nx.read_graphml(GRAPHML, node_type=int)
    node_coords = {
        n: (float(d["y"]), float(d["x"]))
        for n, d in G.nodes(data=True)
    }

    print("加载标注...")
    with open(ANN_PATH, encoding="utf-8") as f:
        ann = json.load(f)
    edges = ann.get("edges", [])
    print(f"总边数 {len(edges)}")

    # 目标边：长度 ≥ MIN_LEN_M
    targets = [e for e in edges if (e.get("length_m") or 0) >= MIN_LEN_M]
    print(f"长度 ≥ {MIN_LEN_M}m 的目标边 {len(targets)}")

    # 需要高程的节点
    node_ids = sorted({
        nid
        for e in targets
        for nid in (e.get("u"), e.get("v"))
        if nid in node_coords
    })
    points = [node_coords[n] for n in node_ids]
    print(f"需高程节点 {len(node_ids)}，开始请求 open-elevation...")

    raw = fetch_elevations(points)
    node_raw = {node_ids[i]: h for i, h in raw.items()}
    print(f"获得高程 {len(node_raw)}/{len(node_ids)}")

    print(f"空间平滑（半径 {SMOOTH_RADIUS_M}m，中位数）...")
    node_elev = smooth_elevations(node_ids, points, node_raw)

    dist = Counter()
    updated = 0
    max_grad = 0.0
    for e in edges:
        u, v = e.get("u"), e.get("v")
        length = e.get("length_m") or 0
        if length < MIN_LEN_M:
            dist["<50m(保留默认)"] += 1
            continue
        if u not in node_elev or v not in node_elev:
            dist["无高程(保留默认)"] += 1
            continue

        de = abs(node_elev[v] - node_elev[u])
        grad = de / length if length > 0 else 0.0
        max_grad = max(max_grad, grad)
        lvl = slope_level(grad)

        e["slope_level"] = lvl
        # 保留 note 中「; 临近 XX」这类人工信息，只替换坡度占位部分
        orig_note = e.get("note") or ""
        tail = orig_note.split(";", 1)[1].strip() if ";" in orig_note else ""
        new_note = f"DEM实测 坡度{grad * 100:.1f}%（Δelev={de:.0f}m）"
        if tail:
            new_note += f"; {tail}"
        e["note"] = new_note

        updated += 1
        dist[f"level {lvl}"] += 1

    print("slope_level 分布:", dict(sorted(dist.items(), key=lambda x: str(x[0]))))
    print(f"更新条数: {updated}，最大坡度: {max_grad * 100:.1f}%")

    if dry_run:
        print("[dry-run] 未写文件")
        return

    bak = ANN_PATH + ".bak"
    with open(ANN_PATH, encoding="utf-8") as f:
        original = f.read()
    with open(bak, "w", encoding="utf-8") as f:
        f.write(original)
    print(f"已备份原文件 → {os.path.basename(bak)}")

    ann["slope_source"] = "open-elevation DEM (SRTM/ASTER), computed by scripts/compute_slope_from_dem.py"
    with open(ANN_PATH, "w", encoding="utf-8") as f:
        json.dump(ann, f, ensure_ascii=False, indent=2)
    print(f"已写回 {ANN_PATH}，更新 {updated} 条边 slope_level")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
