"""
为宽泛片区名（梅园/枫园/桂园/信息学部等）补充"区域中心点" POI。

背景：
  这些片区名在用户 query 中高频出现（"从梅园到枫园"），但 POI 库中只有
  具体建筑（梅园食堂、枫园教学楼…），没有片区代表点，导致路径规划无法起终点。
  为避免与建筑名串匹配，匹配层对宽泛词做了子串保护；本脚本以【精确同名】
  POI 的形式补充区域质心，精确匹配优先于宽泛词保护，互不冲突。

质心来源：
  - 自动质心 = 名称含片区关键词的全部 POI 坐标算术平均（GCJ-02）
  - 文理学部 POI 命名中几乎不含"文理学部"字样，使用人工锚点（樱园片区中心）

幂等：以 id="area_<zone>" 判重，重复运行不会产生重复条目。

用法：
  python scripts/data/add_area_centroids.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
POIS_PATH = PROJECT_ROOT / "data" / "pois.json"

# 片区名 → 名称匹配关键词（None 表示不做自动质心，只用手工锚点）
ZONE_KEYWORDS = {
    "梅园": ["梅园"],
    "枫园": ["枫园"],
    "桂园": ["桂园"],
    "樱园": ["樱园"],
    "湖滨": ["湖滨"],
    # 注："星湖"本身已是真实 POI（湖泊），不再生成区域点
    "信息学部": ["信息学部", "星湖"],
    "工学部": ["工学部"],
}

# 无法从命名自动聚合的片区，人工锚点（GCJ-02）
MANUAL_ANCHORS = {
    "文理学部": {"lng": 114.3628, "lat": 30.5388, "note": "樱园片区中心（老斋舍-樱花大道一带）"},
}

ZONE_ALIASES = {
    "信息学部": ["信息学部"],
    "工学部": ["工学部"],
    "文理学部": ["文理学部"],
}


def centroid_of(pois: list[dict], keywords: list[str]) -> tuple[float, float, int]:
    hits = [p for p in pois if any(k in p.get("name", "") for k in keywords)]
    if not hits:
        return 0.0, 0.0, 0
    lng = sum(p["coordinates"]["lng"] for p in hits) / len(hits)
    lat = sum(p["coordinates"]["lat"] for p in hits) / len(hits)
    return round(lng, 6), round(lat, 6), len(hits)


def build_zone_poi(zone: str, lng: float, lat: float, n_members: int, note: str = "") -> dict:
    return {
        "id": f"area_{zone}",
        "name": zone,
        "aliases": ZONE_ALIASES.get(zone, []),
        "coordinates": {"lng": lng, "lat": lat},
        "type": "area",
        "campus": "whu",
        "category": "片区中心点",
        "description": f"{zone}片区中心" + (f"（由 {n_members} 个片区设施坐标聚合）" if n_members else f"（{note}）"),
        "season_tags": [],
        "scenery_score": 3,
        "rating": None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="补充片区中心点 POI")
    parser.add_argument("--dry-run", action="store_true", help="只打印结果，不写文件")
    args = parser.parse_args(argv)

    with open(POIS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    pois: list[dict] = data["pois"]
    existing_ids = {p.get("id") for p in pois}

    added = []
    for zone, keywords in ZONE_KEYWORDS.items():
        lng, lat, n = centroid_of(pois, keywords)
        if n == 0:
            print(f"[跳过] {zone}: 无匹配 POI，且未配置手工锚点", file=sys.stderr)
            continue
        poi = build_zone_poi(zone, lng, lat, n)
        if poi["id"] in existing_ids:
            print(f"[已存在] {zone} -> ({lng}, {lat}), n={n}")
            continue
        pois.append(poi)
        added.append(poi)
        print(f"[新增] {zone} -> ({lng}, {lat}), 聚合 {n} 个设施")

    for zone, anchor in MANUAL_ANCHORS.items():
        poi = build_zone_poi(zone, anchor["lng"], anchor["lat"], 0, anchor.get("note", ""))
        if poi["id"] in existing_ids:
            print(f"[已存在] {zone} -> ({anchor['lng']}, {anchor['lat']}) 手工锚点")
            continue
        pois.append(poi)
        added.append(poi)
        print(f"[新增] {zone} -> ({anchor['lng']}, {anchor['lat']}) 手工锚点: {anchor.get('note', '')}")

    if args.dry_run:
        print(f"\n(dry-run) 拟新增 {len(added)} 条，未写文件。")
        return 0

    data["pois"] = pois
    data["count"] = len(pois)
    with open(POIS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n完成：新增 {len(added)} 条，POI 总数 {len(pois)}，已写回 {POIS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
