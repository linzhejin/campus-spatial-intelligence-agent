"""
高德 POI 全量拉取脚本 V2（校园范围 + 类型过滤）

策略：
  1. place/polygon + place/around 网格化，覆盖三个学部；
  2. 用高德 types 参数限制为校园相关类型（科教、体育、风景、住宿）；
  3. 名称关键词过滤：保留 教学楼/食堂/宿舍/图书馆/操场/校门/学院/楼/舍/园/山/湖 等；
  4. 排除商铺类：咖啡/奶茶/餐厅/停车场/公交站/快递/美容/酒店/超市/药店 等；
  5. 按 amap_id 去重，按校园多边形过滤；
  6. 输出 pois_new.json。
"""

import json
import os
import sys
import time
from datetime import datetime

import httpx
from dotenv import dotenv_values

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
from config import CAMPUS_POLYS_GCJ  # noqa: E402

POIS_PATH = os.path.join(PROJECT_ROOT, "data", "pois.json")
OUT_PATH = os.path.join(PROJECT_ROOT, "data", "pois_new.json")
POLYGON_API = "https://restapi.amap.com/v3/place/polygon"
AROUND_API = "https://restapi.amap.com/v3/place/around"

# 高德类型码：只拉取校园相关大类
# 140000=科教文化, 110000=风景名胜, 080000=体育休闲, 120000=商务住宅(含宿舍)
# 150500=城市广场, 060000=住宿服务, 190000=通行设施(含校门)
TYPE_CODES = "140000|110000|080000|120000|150500|190000"

# 关键词补搜（类型码可能遗漏的校园说法）
KEYWORDS = ["教学楼", "食堂", "宿舍", "公寓", "图书馆", "操场", "体育馆", "校门", "学院", "楼", "舍"]

# 保留关键词（名称命中其一才保留）
KEEP_KEYWORDS = (
    "武汉大学", "武大", "珞珈", "教学楼", "教", "食堂", "风味", "宿舍", "公寓", "寝室", "舍",
    "图书馆", "图", "操场", "体育", "球场", "游泳", "校门", "大门", "门", "学院", "楼",
    "广场", "山", "湖", "樱", "枫", "梅", "桂", "湖滨", "星湖", "牌坊", "斋舍", "园",
    "中心", "院", "馆", "堂", "亭", "台",
)

# 排除关键词（命中即删，商业/设施类）
EXCLUDE_KEYWORDS = (
    "咖啡", "奶茶", "茶", "餐厅", "饭馆", "火锅", "烧烤", "米粉", "面馆", "小吃", "快餐",
    "便当", "披萨", "汉堡", "寿司", "料理", "甜品", "蛋糕", "面包", "烘焙", "冰淇淋",
    "酒吧", "酒馆",
    "停车场", "车位",
    "公交站", "地铁站", "出租", "车站",
    "快递", "丰巢", "外卖柜", "快递柜", "菜鸟",
    "美容", "美发", "造型", "理发", "沙龙", "美甲", "按摩", "SPA", "足浴",
    "酒店", "宾馆", "旅店", "民宿", "旅馆",
    "超市", "便利店", "商店", "小卖部", "杂货",
    "药店", "药房", "医院", "诊所", "口腔", "体检",
    "银行", "ATM", "取款", "工商", "建设", "农业", "中国银",
    "移动", "联通", "电信", "营业厅",
    "彩票", "烟酒", "网吧", "KTV", "影院", "健身",
    "维修", "服务中心", "营业厅",
    "公司", "有限", "集团", "门市", "代理",
    "停车场", "充电", "加油",
    "外卖", "快递",
)


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


def keep_poi(name, addr, type_code):
    """判断是否保留此POI。"""
    full = (name + " " + addr)
    # 排除商业类
    for kw in EXCLUDE_KEYWORDS:
        if kw in name:  # 只看名称，地址里可能有"武汉大学"
            return False
    # 保留校园相关
    for kw in KEEP_KEYWORDS:
        if kw in name:
            return True
    return False


def classify(name, type_code):
    if "图书馆" in name or type_code.startswith("1406"):
        return "study"
    if any(k in name for k in ("食堂", "风味", "餐厅", "美食城")) and not any(k in name for k in ("咖啡", "奶茶")):
        return "dining"
    if any(k in name for k in ("操场", "体育馆", "运动", "体育场", "球场", "游泳")) or type_code.startswith("08"):
        return "sports"
    if any(k in name for k in ("宿舍", "公寓", "寝室", "舍")):
        return "dorm"
    if ("门" in name and any(k in name for k in ("校门", "大门", "凌波", "珞瑜", "洪波", "牌坊", "珞珈门", "二门", "南门", "北门", "东门", "西门"))):
        return "gate"
    if type_code.startswith("11") or any(k in name for k in ("山", "湖", "广场", "樱", "斋舍", "园")):
        return "scenery"
    if type_code.startswith("14") or any(k in name for k in ("教学楼", "学院", "楼", "教", "研究", "中心")):
        return "study"
    return "landmark"


def fetch_by_polygon(client, key, polygon, types=None):
    poly_str = ";".join(f"{lng},{lat}" for lng, lat in polygon)
    out = []
    for page in range(1, 101):
        params = {
            "key": key, "polygon": poly_str,
            "offset": 25, "page": page, "extensions": "all",
        }
        if types:
            params["types"] = types
        try:
            r = client.get(POLYGON_API, params=params, timeout=15)
            d = r.json()
        except Exception as e:
            print(f"    polygon 异常 page={page}: {e}")
            break
        if d.get("status") != "1":
            print(f"    polygon 错误: {d.get('info')}")
            break
        pois = d.get("pois") or []
        out.extend(pois)
        if len(pois) < 25:
            break
        time.sleep(0.1)
    return out


def fetch_by_grid(client, key, polygon, types=None, keywords=None, grid_step=0.003, radius=350):
    lngs = [p[0] for p in polygon]
    lats = [p[1] for p in polygon]
    min_lng, max_lng = min(lngs), max(lngs)
    min_lat, max_lat = min(lats), max(lats)
    out = []
    lng = min_lng
    while lng <= max_lng:
        lat = min_lat
        while lat <= max_lat:
            if point_in_polygon(lng, lat, polygon):
                kw_list = keywords if keywords else [None]
                for kw in kw_list:
                    for page in range(1, 6):
                        params = {
                            "key": key,
                            "location": f"{lng},{lat}",
                            "radius": radius,
                            "offset": 25, "page": page, "extensions": "all",
                        }
                        if types:
                            params["types"] = types
                        if kw:
                            params["keywords"] = kw
                        try:
                            r = client.get(AROUND_API, params=params, timeout=15)
                            d = r.json()
                        except Exception:
                            break
                        if d.get("status") != "1":
                            break
                        pois = d.get("pois") or []
                        out.extend(pois)
                        if len(pois) < 25:
                            break
                        time.sleep(0.05)
            lat += grid_step
        lng += grid_step
    return out


def main():
    env = dotenv_values(os.path.join(PROJECT_ROOT, ".env"))
    key = env.get("AMAP_WEB_KEY") or env.get("AMAP_KEY", "")
    if not key or key.startswith("your-"):
        print("缺少高德 Web 服务 key")
        return 1

    with open(POIS_PATH, encoding="utf-8") as f:
        existing_data = json.load(f)
    existing = existing_data.get("pois", [])
    existing_by_name = {p["name"]: p for p in existing}

    candidates = {}
    stats = {"raw": 0, "filtered_out": 0, "kept": 0}

    with httpx.Client() as client:
        for campus_name, polygon in CAMPUS_POLYS_GCJ.items():
            print(f"\n=== 拉取 {campus_name} ===")

            # ① place/polygon 按类型码
            print(f"  [1/3] place/polygon (types={TYPE_CODES[:20]}...) ...")
            raw1 = fetch_by_polygon(client, key, polygon, types=TYPE_CODES)
            print(f"        返回 {len(raw1)} 条")

            # ② place/around 按类型码
            print(f"  [2/3] place/around 网格化 (types) ...")
            raw2 = fetch_by_grid(client, key, polygon, types=TYPE_CODES)
            print(f"        返回 {len(raw2)} 条")

            # ③ place/around 按关键词
            print(f"  [3/3] place/around 关键词补搜 ...")
            raw3 = fetch_by_grid(client, key, polygon, keywords=KEYWORDS)
            print(f"        返回 {len(raw3)} 条")

            raw_pois = raw1 + raw2 + raw3
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

                if not point_in_polygon(lng, lat, polygon):
                    continue

                type_code = p.get("typecode") or ""
                if not keep_poi(name, addr, type_code):
                    stats["filtered_out"] += 1
                    continue

                amap_id = p.get("id") or f"{lng:.6f},{lat:.6f}"
                if amap_id in candidates:
                    continue

                candidates[amap_id] = {
                    "name": name,
                    "lng": round(lng, 6),
                    "lat": round(lat, 6),
                    "type_code": type_code,
                    "type_name": p.get("type", ""),
                    "address": addr,
                    "campus": campus_name,
                    "category": classify(name, type_code),
                    "amap_id": amap_id,
                }
                stats["kept"] += 1

    print(f"\n===== 拉取完成 =====")
    print(f"原始 {stats['raw']} 条 → 过滤 {stats['filtered_out']} 条 → 保留 {stats['kept']} 条")

    # 合并
    new_pois = []
    seen_names = set()
    for c in candidates.values():
        name = c["name"]
        if name in seen_names:
            continue
        seen_names.add(name)

        old = existing_by_name.get(name, {})
        poi = {
            "id": old.get("id", f"poi_{len(new_pois)+1:03d}"),
            "name": name,
            "aliases": old.get("aliases", []),
            "coordinates": {"lng": c["lng"], "lat": c["lat"]},
            "type": c["category"],
            "campus": c["campus"],
            "category": c["category"],
            "description": old.get("description", c["address"] or f"武汉大学校园内地点。"),
            "season_tags": old.get("season_tags", ["all"]),
            "scenery_score": old.get("scenery_score", 1),
            "source": f"amap:{c['amap_id']}",
        }
        new_pois.append(poi)

    campus_order = {"文理学部": 0, "工学部": 1, "信息学部": 2}
    type_order = {"study": 0, "dining": 1, "sports": 2, "dorm": 3, "scenery": 4, "gate": 5, "landmark": 6}
    new_pois.sort(key=lambda p: (campus_order.get(p["campus"], 9), type_order.get(p["type"], 9), p["name"]))

    for i, p in enumerate(new_pois, 1):
        p["id"] = f"poi_{i:03d}"

    output = {
        "source": f"高德全量拉取（类型过滤+关键词）{datetime.now().strftime('%Y-%m-%d')}，共{len(new_pois)}条",
        "count": len(new_pois),
        "pois": new_pois,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n合并后共 {len(new_pois)} 个POI")
    print(f"已写入: {OUT_PATH}")

    by_campus = {}
    by_type = {}
    for p in new_pois:
        by_campus[p["campus"]] = by_campus.get(p["campus"], 0) + 1
        by_type[p["type"]] = by_type.get(p["type"], 0) + 1
    print(f"\n按学部: {by_campus}")
    print(f"按类型: {by_type}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
