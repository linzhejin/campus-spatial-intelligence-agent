"""
高德 Web 服务 POI 候选查询脚本（接口不保证全量）

范围：武汉大学老校区三个学部（文理学部 / 工学部 / 信息学部），不含医学部。

策略：
  1. 对三个学部锚点分别用 place/around（圆形搜索）+ 关键词补搜，抓高德 POI；
  2. 用三学部 OSM 边界过滤候选，并检查名称/地址；
  3. 按 高德 id + 坐标 去重，与现有 data/pois.json 的 24 个手工 POI 比对标记；
  4. 输出 data/pois_candidates.json（**不覆盖**正式数据），供人工核对后合并。

坐标说明：高德返回 GCJ-02；只在范围判定时转换为 WGS-84 与 OSM 边界比较。

用法：
  python scripts/fetch_pois_amap.py            # 抓取并生成候选清单
  python scripts/fetch_pois_amap.py --report   # 只打印现有候选清单的统计报告
"""

import json
import math
import os
import sys
import time
from datetime import datetime

import httpx
from dotenv import dotenv_values

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
from scripts.fetch.campus_scope import campus_for_gcj, load_whu_polygons  # noqa: E402
POIS_PATH = os.path.join(PROJECT_ROOT, "data", "pois.json")
OUT_PATH = os.path.join(PROJECT_ROOT, "data", "pois_candidates.json")
AROUND_API = "https://restapi.amap.com/v3/place/around"

# 三学部 OSM 面内锚点（GCJ-02）；圆形只负责请求，入库范围由 OSM 边界判定。
CAMPUSES = [
    {"name": "文理学部", "anchor": (114.366379, 30.536838), "radius": 1600},
    {"name": "工学部", "anchor": (114.362163, 30.542629), "radius": 950},
    {"name": "信息学部", "anchor": (114.360214, 30.528503), "radius": 900},
]

# 高德 POI 大类 type code（| 分隔为多类）
TYPE_CODES = "140000|050000|080000|110000|120000|150000"
# 关键词补搜（类型码覆盖不到的校内说法）
KEYWORDS = ["食堂", "宿舍", "学生公寓", "教学楼", "图书馆", "操场", "体育馆", "校门", "大门"]

# 校内标识：名称或地址命中即认为是武大相关
WHU_HINTS = ("武汉大学", "武大", "珞珈", "whu", "WHU")


def keep_campus_candidate(name, address):
    whu_named = any(h in (name + address) for h in WHU_HINTS)
    campus_word = any(k in name for k in (
        "食堂", "宿舍", "公寓", "教学楼", "图书馆", "操场", "体育馆"))
    return whu_named or campus_word or (len(name) >= 2 and name.endswith("门"))


def haversine_m(lng1, lat1, lng2, lat2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def point_in_polygon(lng, lat, polygon):
    """射线法判断点是否在多边形内（polygon 为 (lng, lat) 顶点列表）。"""
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


def amap_around(client, key, location, radius, types=None, keywords=None):
    """分页调用 place/around，返回全部 pois 原始列表。"""
    out = []
    for page in range(1, 11):  # 最多 10 页 × 25 = 250 条/组
        params = {
            "key": key,
            "location": f"{location[0]},{location[1]}",
            "radius": radius,
            "offset": 25,
            "page": page,
            "extensions": "all",
        }
        if types:
            params["types"] = types
        if keywords:
            params["keywords"] = keywords
        try:
            r = client.get(AROUND_API, params=params, timeout=15)
            d = r.json()
        except Exception as e:  # noqa: BLE001
            print(f"    ! 请求异常 page={page}: {e}")
            break
        if d.get("status") != "1":
            print(f"    ! 高德返回错误: {d.get('info')} (code={d.get('infocode')})")
            break
        pois = d.get("pois") or []
        out.extend(pois)
        if len(pois) < 25:
            break
        time.sleep(0.15)
    return out


def classify(name, type_code):
    """按名称 + 高德类型码映射到项目 category。"""
    if "图书馆" in name or type_code.startswith("1406"):
        return "study"
    if any(k in name for k in ("食堂", "餐厅", "美食城")) or type_code.startswith("05"):
        return "dining"
    if any(k in name for k in ("操场", "体育馆", "运动", "体育场")) or type_code.startswith("08"):
        return "sports"
    if any(k in name for k in ("宿舍", "公寓", "学生寝室")):
        return "dorm"
    if len(name) >= 2 and name.endswith("门"):
        return "gate"
    if type_code.startswith("11") or any(k in name for k in ("山", "湖", "广场", "樱", "斋舍")):
        return "scenery"
    if type_code.startswith("14"):  # 科教文化（教学楼/学院）
        return "study"
    return "landmark"


def main():
    env = dotenv_values(os.path.join(PROJECT_ROOT, ".env"))
    key = env.get("AMAP_WEB_KEY") or env.get("AMAP_KEY", "")
    if not key or key.startswith("your-"):
        print("缺少有效的高德 Web 服务 key：请在 .env 中配置 AMAP_WEB_KEY")
        print("（高德控制台 → 应用管理 → 创建 key → 服务平台选『Web服务』）")
        return 1

    with open(POIS_PATH, encoding="utf-8") as f:
        existing = json.load(f)["pois"]
    existing_names = {p["name"] for p in existing}
    campus_polygons = load_whu_polygons()

    candidates = {}  # amap_id 或 坐标key -> record（去重）
    stats = {"raw": 0, "off_campus": 0, "kept": 0}

    with httpx.Client() as client:
        for campus in CAMPUSES:
            print(f"\n=== 抓取 {campus['name']}（锚点 {campus['anchor']}）===")
            raw_pois = []
            # ① 按类型码大圆搜索
            raw_pois += amap_around(client, key, campus["anchor"], campus["radius"], types=TYPE_CODES)
            # ② 关键词补搜
            for kw in KEYWORDS:
                raw_pois += amap_around(client, key, campus["anchor"], campus["radius"], keywords=kw)
                time.sleep(0.1)

            print(f"  原始返回 {len(raw_pois)} 条，开始过滤…")
            stats["raw"] += len(raw_pois)

            for p in raw_pois:
                loc = p.get("location") or ""
                if "," not in loc:
                    continue
                lng_s, lat_s = loc.split(",")
                lng, lat = float(lng_s), float(lat_s)
                name = (p.get("name") or "").strip()
                addr = (p.get("address") or "").strip()
                if not name:
                    continue

                gate_like = len(name) >= 2 and name.endswith("门")
                assigned_campus = campus_for_gcj(
                    lng, lat, campus_polygons,
                    boundary_tolerance_m=25 if gate_like else 0)
                whu_named = any(h in (name + addr) for h in WHU_HINTS)
                if assigned_campus is None:
                    stats["off_campus"] += 1
                    continue
                if not keep_campus_candidate(name, addr):
                    stats["off_campus"] += 1
                    continue

                type_code = p.get("typecode") or ""
                dedup_key = p.get("id") or f"{lng:.6f},{lat:.6f}"
                if dedup_key in candidates:
                    # 已存在：若本次学部信息更准（更近锚点）则更新
                    continue
                candidates[dedup_key] = {
                    "amap_id": p.get("id", ""),
                    "name": name,
                    "lng": round(lng, 6),
                    "lat": round(lat, 6),
                    "type_code": type_code,
                    "type_name": p.get("type", ""),
                    "address": addr,
                    "campus": assigned_campus,
                    "category": classify(name, type_code),
                    "whu_named": whu_named,
                    "already_exists": name in existing_names,
                }
                stats["kept"] += 1

    records = sorted(
        candidates.values(),
        key=lambda r: (r["campus"], r["category"], r["name"]),
    )
    output = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "note": "高德 place/around 抓取的候选 POI（GCJ-02 坐标），需人工核对后合并进 pois.json",
        "count": len(records),
        "candidates": records,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n===== 抓取完成 =====")
    print(f"原始 {stats['raw']} 条 → 过滤校外 {stats['off_campus']} 条 → 保留 {stats['kept']} 条")
    print(f"其中与现有 24 个手工 POI 同名: {sum(1 for r in records if r['already_exists'])} 条")
    print(f"已写入: {OUT_PATH}")
    print_report(records)
    return 0


def print_report(records):
    """按 学部 × category 打印统计。"""
    by = {}
    for r in records:
        by.setdefault(r["campus"], {}).setdefault(r["category"], []).append(r["name"])
    print("\n----- 分类统计 -----")
    for campus, cats in by.items():
        print(f"\n【{campus}】")
        for cat in ("study", "dining", "sports", "dorm", "gate", "scenery", "landmark"):
            names = cats.get(cat)
            if names:
                print(f"  {cat:<9} ({len(names):>2}): {', '.join(names[:12])}"
                      + (f" …(+{len(names)-12})" if len(names) > 12 else ""))


if __name__ == "__main__":
    if "--report" in sys.argv and os.path.exists(OUT_PATH):
        data = json.loads(open(OUT_PATH, encoding="utf-8").read())
        print_report(data["candidates"])
    else:
        sys.exit(main())
