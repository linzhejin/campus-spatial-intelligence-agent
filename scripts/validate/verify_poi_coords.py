"""
用高德 Web 服务 place search 校验 data/pois.json 中每个 POI 的坐标，输出偏差报告。

- POI 坐标为 GCJ-02（高德坐标系），高德 place search 返回的 location 也是 GCJ-02，可直接比较。
- 对每个 POI 依次尝试多种关键词组合，取距当前坐标最近的高德匹配。
- 偏差 > 50m 标记 ⚠，> 200m 标记 ✗（基本可判定坐标错误）。

用法：
  python scripts/verify_poi_coords.py
"""

import json
import math
import os
import sys
import time

import httpx
from dotenv import dotenv_values

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POIS_PATH = os.path.join(PROJECT_ROOT, "data", "pois.json")
API = "https://restapi.amap.com/v3/place/text"


def haversine_m(lng1, lat1, lng2, lat2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def search(key, keywords, city="武汉", offset=10):
    r = httpx.get(
        API,
        params={"key": key, "keywords": keywords, "city": city, "offset": offset},
        timeout=15,
    )
    d = r.json()
    if d.get("status") != "1":
        return []
    return d.get("pois", [])


def main():
    env = dotenv_values(os.path.join(PROJECT_ROOT, ".env"))
    key = env.get("AMAP_WEB_KEY", "")
    if not key:
        print("缺少 AMAP_WEB_KEY（.env 中未配置），退出。")
        return 1

    with open(POIS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    pois = data["pois"]

    print(f"{'POI':<12} {'当前坐标':<24} {'最佳匹配':<20} {'偏差':>8}  判定")
    print("-" * 90)

    bad = []
    for p in pois:
        name = p["name"]
        clng, clat = p["coordinates"]["lng"], p["coordinates"]["lat"]
        aliases = p.get("aliases", []) or []

        # 候选关键词：优先「武汉大学+名称」，其次名称本身、别名
        candidates = []
        for kw in ([f"武汉大学{name}", name] + [f"武汉大学{a}" for a in aliases]):
            if kw not in candidates:
                candidates.append(kw)

        best = None
        best_dist = float("inf")
        for kw in candidates:
            for poi in search(key, kw):
                if not poi.get("location"):
                    continue
                lng_s, lat_s = poi["location"].split(",")
                dist = haversine_m(clng, clat, float(lng_s), float(lat_s))
                if dist < best_dist:
                    best_dist = dist
                    best = poi
            time.sleep(0.2)

        if best is None:
            print(f"{name:<12} {clng:.5f},{clat:.5f}   {'(无匹配)':<20} {'—':>8}   ?")
            bad.append((name, None, None))
            continue

        lng_b, lat_b = best["location"].split(",")
        flag = "OK" if best_dist <= 50 else ("WARN" if best_dist <= 200 else "BAD")
        print(
            f"{name:<12} {clng:.5f},{clat:.5f}   "
            f"{best['name'][:18]:<20} {best_dist:>7.0f}m  {flag}"
        )
        if best_dist > 50:
            bad.append((name, best_dist, (float(lng_b), float(lat_b), best["name"])))

    print("-" * 90)
    if bad:
        print(f"共 {len(bad)} 个 POI 偏差 > 50m（需核对）:")
        for name, dist, loc in bad:
            if loc:
                print(f"  {name}: 偏差{dist:.0f}m, 高德建议 {loc[2]} @ {loc[0]:.5f},{loc[1]:.5f}")
            else:
                print(f"  {name}: 无高德匹配")
    else:
        print("所有 POI 坐标偏差 ≤ 50m，无需修正。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
