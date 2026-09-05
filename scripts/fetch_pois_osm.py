"""
OpenStreetMap POI 抓取脚本（POI 全覆盖 · 步骤 1 的免 key 数据源）

与高德抓取脚本并行的数据源：
  - OSM 数据免费、无需 API key，与项目路网同源（WGS-84），坐标用 wgs84_to_gcj02 转成 GCJ-02；
  - 通过 Overpass API 查询武大老校区三个学部范围内带名称的建筑/设施；
  - 输出 data/pois_osm_candidates.json，供人工核对后与高德候选合并。

用法：
  python scripts/fetch_pois_osm.py
"""

import json
import math
import os
import sys
from datetime import datetime

import httpx

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
from spatial.coord_transform import wgs84_to_gcj02, gcj02_to_wgs84  # noqa: E402

POIS_PATH = os.path.join(PROJECT_ROOT, "data", "pois.json")
OUT_PATH = os.path.join(PROJECT_ROOT, "data", "pois_osm_candidates.json")

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.nchc.org.tw/api/interpreter",
]

# 三学部锚点（WGS-84；信息学部锚点东移到珞瑜路南校区腹地）
CAMPUSES = [
    ("文理学部", 114.3610, 30.5375),
    ("工学部",   114.3715, 30.5410),
    ("信息学部", 114.3755, 30.5240),
]

# 校园多边形统一以 config.CAMPUS_POLYS_GCJ 为准（GCJ-02），
# 与 scripts/clip_to_campus.py 的路网/POI 裁剪共用同一边界
from config import CAMPUS_POLYS_GCJ as _CAMPUS_POLYS_GCJ  # noqa: E402

# 查询范围（WGS-84 bbox：south, west, north, east）
BBOX = (30.5150, 114.3460, 30.5510, 114.3900)

OVERPASS_QUERY = """
[out:json][timeout:90];
(
  nwr({south},{west},{north},{east})["name"]["amenity"];
  nwr({south},{west},{north},{east})["name"]["leisure"];
  nwr({south},{west},{north},{east})["name"]["tourism"];
  nwr({south},{west},{north},{east})["name"]["historic"];
  nwr({south},{west},{north},{east})["name"]["building"]["building"~"dormitory|university|college|apartments"];
  nwr({south},{west},{north},{east})["name"]["barrier"="gate"];
  nwr({south},{west},{north},{east})["name"]["entrance"];
);
out center tags;
""".format(south=BBOX[0], west=BBOX[1], north=BBOX[2], east=BBOX[3])

WHU_NAME_HINTS = ("武汉大学", "武大", "珞珈", "樱", "桂园", "梅园", "枫园", "湖滨",
                  "教", "食堂", "图书馆", "斋舍", "操场", "体育馆", "凌波门",
                  "牌坊", "珞瑜", "洪波门", "珞珈门", "信息学部", "工学部", "文理学部")

# 明确排除的校外/无关名称（导航场景用不到）
EXCLUDE_HINTS = ("华中", "华师", "理工大", "武汉理工", "街道口", "广埠屯", "中商",
                 "银行", "酒店", "宾馆", "大厦", "小区", "公寓管理",
                 "派出所", "居委会", "街道办", "街道办事", "养老院", "加油站",
                 "小学", "中学", "幼儿园", "附中", "附属外语", "口腔",
                 "招待所", "警务室", "警务", "中科院", "水生生物研究所",
                 "劝业场", "场站", "停车场", "驾校", "地震局", "社区",
                 "委员会", "门诊部", "料理", "教职工",
                 "口院", "水生所", "面馆", "集贸市场", "出版社书库")

# 泛化宿舍名（无学部/园区上下文，不可作为导航目标）
import re  # noqa: E402
_GENERIC_DORM = re.compile(r"^[A-Za-z0-9]{1,3}[栋舍#]?$|^[Ee]\d{1,2}$")
# 末尾带游离字母的脏数据名（如 "教工食堂w"）
_TRAILING_JUNK = re.compile(r"[一-龥][A-Za-z]$")


def haversine_m(lng1, lat1, lng2, lat2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


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


def campus_by_name(name):
    """名称里带明确学部词的，直接按名称归属（最高优先级）。"""
    if "信息学部" in name:
        return "信息学部"
    if "工学部" in name:
        return "工学部"
    # 枫园/湖滨地理上在工学部一侧
    if name.startswith(("枫园", "湖滨")):
        return "工学部"
    # 樱/桂/梅园在文理学部
    if name.startswith(("桂园", "梅园", "樱园", "樱花")):
        return "文理学部"
    return None


def nearest_campus(lng, lat):
    """最近学部锚点归属（多边形重叠会造成误判，锚点最近最稳）；超 2km 返回 None。"""
    best, best_d = None, 2e6
    for name, clng, clat in CAMPUSES:
        d = haversine_m(lng, lat, clng, clat)
        if d < best_d:
            best, best_d = name, d
    return best if best_d <= 2000 else None


def classify(tags, name):
    a = tags.get("amenity", "")
    b = tags.get("building", "")
    leisure = tags.get("leisure", "")
    tourism = tags.get("tourism", "")
    barrier = tags.get("barrier", "")
    if a == "library" or "图书馆" in name:
        return "study"
    if a in ("restaurant", "canteen", "fast_food", "cafe", "food_court") or "食堂" in name:
        return "dining"
    if a in ("sports_centre", "gym") or leisure in ("stadium", "pitch", "sports_centre") \
            or "体育馆" in name or "操场" in name or "体育场" in name \
            or any(k in name for k in ("游泳", "排球", "篮球", "网球", "羽毛球")):
        return "sports"
    if b == "dormitory" or "宿舍" in name or "公寓" in name or name.endswith("舍"):
        return "dorm"
    if tourism in ("attraction", "viewpoint") or "historic" in tags \
            or any(k in name for k in ("樱花大道", "樱顶", "樱园", "斋舍",
                                       "牌楼", "早期建筑", "珞珈山", "观湖", "广场")):
        return "scenery"
    if (barrier == "gate" or tags.get("entrance") or "校门" in name) \
            and not any(k in name for k in ("游泳", "码头")):
        return "gate"
    if "门" in name and any(k in name for k in ("大门", "凌波门", "珞瑜", "洪波", "牌坊", "珞珈门", "科技门", "南门")):
        return "gate"
    if a in ("university", "college") or b in ("university", "college", "classrooms") \
            or a in ("school", "research_institute") or "教学楼" in name \
            or "学院" in name or "实验" in name or "楼" in name:
        return "study"
    return "landmark"


def fetch_overpass():
    last_err = None
    headers = {"User-Agent": "WHU-Walker/1.0 (campus-spatial research; edu project)"}
    with httpx.Client(headers=headers, timeout=120) as client:
        for url in OVERPASS_ENDPOINTS:
            try:
                print(f"  尝试 Overpass 节点: {url}")
                r = client.post(url, data={"data": OVERPASS_QUERY})
                if r.status_code != 200:
                    last_err = f"HTTP {r.status_code}: {r.text[:150]}"
                    print(f"  失败: {last_err}")
                    continue
                d = r.json()
                if "elements" in d:
                    print(f"  请求成功，返回 {len(d['elements'])} 个要素")
                    return d["elements"]
                last_err = d.get("remark", str(d)[:200])
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
                print(f"  失败: {e}")
    raise RuntimeError(f"所有 Overpass 节点均失败: {last_err}")


def main():
    with open(POIS_PATH, encoding="utf-8") as f:
        existing = json.load(f)["pois"]
    existing_names = {p["name"] for p in existing}

    elements = fetch_overpass()

    records = []
    seen = set()
    stats = {"total": 0, "named": 0, "kept": 0}
    for el in elements:
        stats["total"] += 1
        tags = el.get("tags", {}) or {}
        name = (tags.get("name") or "").strip()
        if not name:
            continue
        stats["named"] += 1
        # 坐标：node 用 lat/lon；way/relation 用 center
        if el["type"] == "node":
            wlat, wlng = el.get("lat"), el.get("lon")
        else:
            c = el.get("center") or {}
            wlat, wlng = c.get("lat"), c.get("lon")
        if wlat is None or wlng is None:
            continue

        # 过滤：排除明确校外词
        if any(x in name for x in EXCLUDE_HINTS):
            continue
        # 泛化宿舍编号（A栋 / E1 等）不可作导航目标
        if _GENERIC_DORM.match(name) or _TRAILING_JUNK.search(name):
            continue
        # 归属：名称学部词优先，否则最近锚点
        campus = campus_by_name(name) or nearest_campus(wlng, wlat)
        if campus is None:
            continue
        whu_tag = tags.get("operator", "") in ("武汉大学", "Wuhan University") or \
            tags.get("amenity") == "university" or tags.get("building") in \
            ("dormitory", "university", "college")
        if not (whu_tag or any(h in name for h in WHU_NAME_HINTS)):
            continue

        glng, glat = wgs84_to_gcj02(wlng, wlat)
        # 硬过滤：点必须落在三学部校园多边形内（GCJ-02），杜绝街道口/广埠屯等校外点
        if not any(point_in_polygon(glng, glat, poly) for poly in _CAMPUS_POLYS_GCJ.values()):
            continue
        # 名称黑名单（OSM 误 tag，如其他校区建筑）
        if any(b in name for b in getattr(__import__("config"), "POI_NAME_BLACKLIST", ())):
            continue
        # 同名点 200m 内视为同一 POI（重复校门/食堂节点聚合）
        duplicate = None
        for r in records:
            if r["name"] == name and haversine_m(glng, glat, r["lng"], r["lat"]) < 200:
                duplicate = r
                break
        if duplicate is not None:
            # 已有：若新点带更全的标签则跳过，保留先入点
            continue

        records.append({
            "osm_id": f"{el['type']}/{el['id']}",
            "name": name,
            "name_en": tags.get("name:en", ""),
            "lng": round(glng, 6),
            "lat": round(glat, 6),
            "campus": campus,
            "category": classify(tags, name),
            "osm_tags": {k: tags[k] for k in
                         ("amenity", "building", "leisure", "tourism", "historic",
                          "barrier", "operator") if tags.get(k)},
            "already_exists": name in existing_names,
        })
        stats["kept"] += 1

    records.sort(key=lambda r: (r["campus"], r["category"], r["name"]))
    out = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "source": "OpenStreetMap via Overpass API（WGS-84 已转 GCJ-02）",
        "count": len(records),
        "candidates": records,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"\n===== 抓取完成 =====")
    print(f"要素 {stats['total']} → 有名称 {stats['named']} → 校内保留 {stats['kept']}")
    print(f"与现有手工 POI 同名: {sum(1 for r in records if r['already_exists'])} 条")
    print(f"已写入: {OUT_PATH}")
    print_report(records)
    return 0


def print_report(records):
    by = {}
    for r in records:
        by.setdefault(r["campus"], {}).setdefault(r["category"], []).append(r["name"])
    print("\n----- 分类统计 -----")
    for campus in ("文理学部", "工学部", "信息学部"):
        cats = by.get(campus)
        if not cats:
            continue
        print(f"\n【{campus}】")
        for cat in ("study", "dining", "sports", "dorm", "gate", "scenery", "landmark"):
            names = cats.get(cat)
            if names:
                print(f"  {cat:<9} ({len(names):>2}): {', '.join(names[:14])}"
                      + (f" …(+{len(names)-14})" if len(names) > 14 else ""))


if __name__ == "__main__":
    sys.exit(main())
