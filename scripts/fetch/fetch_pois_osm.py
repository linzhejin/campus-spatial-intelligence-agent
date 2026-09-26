"""
OpenStreetMap POI 抓取脚本（POI 全覆盖 · 步骤 1 的免 key 数据源）

与高德抓取脚本并行的数据源：
  - OSM 数据免费、无需 API key，与项目路网同源（WGS-84），坐标用 wgs84_to_gcj02 转成 GCJ-02；
  - 通过 Overpass API 查询武大老校区三个学部范围内带名称的建筑/设施；
  - 输出 data/pois_osm_candidates.json，供人工核对后与高德候选合并。

用法：
  python scripts/fetch/fetch_pois_osm.py
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime

import httpx

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
from spatial.coord_transform import wgs84_to_gcj02  # noqa: E402

POIS_PATH = os.path.join(PROJECT_ROOT, "data", "pois.json")
OUT_PATH = os.path.join(PROJECT_ROOT, "data", "pois_osm_candidates.json")

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.nchc.org.tw/api/interpreter",
]

# 查询范围（WGS-84 bbox：south, west, north, east）
BBOX = (30.5150, 114.3460, 30.5510, 114.3900)

OVERPASS_QUERY = """
[out:json][timeout:90];
(
  nwr({south},{west},{north},{east})["name"]["amenity"];
  nwr({south},{west},{north},{east})["name"]["leisure"];
  nwr({south},{west},{north},{east})["name"]["tourism"];
  nwr({south},{west},{north},{east})["name"]["historic"];
  nwr({south},{west},{north},{east})["name"]["building"];
  nwr({south},{west},{north},{east})["name"]["barrier"="gate"];
  nwr({south},{west},{north},{east})["name"]["entrance"];
  nwr({south},{west},{north},{east})["name"]["shop"];
  nwr({south},{west},{north},{east})["name"]["office"];
  nwr({south},{west},{north},{east})["name"]["healthcare"];
);
out center tags;
""".format(south=BBOX[0], west=BBOX[1], north=BBOX[2], east=BBOX[3])

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


def classify(tags, name):
    a = tags.get("amenity", "")
    b = tags.get("building", "")
    leisure = tags.get("leisure", "")
    tourism = tags.get("tourism", "")
    barrier = tags.get("barrier", "")
    if tags.get("shop"):
        return "dining" if tags["shop"] in ("bakery", "pastry") else "landmark"
    if tags.get("healthcare") or a in ("clinic", "hospital", "pharmacy", "post_office", "parcel_locker"):
        return "landmark"
    if tags.get("office") in ("research", "educational_institution", "university"):
        return "study"
    if tourism in ("museum", "artwork"):
        return "scenery"
    if a == "library" or "图书馆" in name:
        return "study"
    if a in ("restaurant", "canteen", "fast_food", "cafe", "food_court") or "食堂" in name:
        return "dining"
    if a in ("sports_centre", "gym") or leisure in ("stadium", "pitch", "sports_centre", "sports_hall") \
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


class SourceElements(list):
    """List-compatible source data with the exact educational boundaries attached."""
    def __init__(self, evidence):
        super().__init__()
        self.education_areas = evidence.get("education_areas", [])
        self.source_file = evidence.get("source_file")
        for row in evidence["elements"]:
            kind, osm_id = row["osm_id"].split("/")
            element = {"type": kind, "id": int(osm_id), "tags": row["tags"]}
            if kind == "node":
                element.update(lon=row["lon"], lat=row["lat"])
            else:
                bounds = row.get("bounds")
                element["center"] = {"lon": (bounds[0]+bounds[2])/2 if bounds else row["lon"],
                                     "lat": (bounds[1]+bounds[3])/2 if bounds else row["lat"]}
            if row.get("geometry"):
                element["geometry"] = row["geometry"]
            self.append(element)


def fetch_pbf(pbf_path):
    """libosmium assembles complete multipolygons; incomplete relations are omitted."""
    from osm_pbf_evidence import extract_evidence
    return SourceElements(extract_evidence(pbf_path))


def load_boundaries(elements=None):
    from shapely.geometry import shape
    areas = getattr(elements, "education_areas", None)
    if areas:
        return [(row["osm_id"], row["tags"], shape(row["geometry"])) for row in areas]
    path = os.path.join(os.path.dirname(__file__), "osm_campus_boundaries.geojson")
    with open(path, encoding="utf-8") as handle:
        features = json.load(handle)["features"]
    return [(row["properties"]["osm_id"], row["properties"], shape(row["geometry"]))
            for row in features]


CAMPUS_BOUNDARY_NAMES = {
    "武汉大学文理学部": "文理学部", "武汉大学工学部": "工学部",
    "武汉大学信息学部": "信息学部",
}


def campus_evidence(lng, lat, tags, boundaries):
    """Use actual WGS84 campus areas, retaining named gates exactly on an edge."""
    from shapely.geometry import Point
    point = Point(lng, lat)
    inside = [(oid, bt, geom) for oid, bt, geom in boundaries if geom.covers(point)]
    campuses = [(oid, bt) for oid, bt, _ in inside if bt.get("name") in CAMPUS_BOUNDARY_NAMES]
    other = [(oid, bt) for oid, bt, _ in inside
             if bt.get("name") not in CAMPUS_BOUNDARY_NAMES and bt.get("name") != "武汉大学"]
    operator = tags.get("operator", "")
    if any(word in operator for word in ("华中师范大学", "武汉体育学院", "武汉理工大学", "武汉电力职业技术学院")):
        return None, [], "other_institution_operator"
    shared_edge = any(bt.get("name") in CAMPUS_BOUNDARY_NAMES and geom.boundary.distance(point) <= 0.00003
                      for _, bt, geom in inside)
    if other and campuses and shared_edge and (tags.get("barrier") == "gate" or tags.get("entrance")):
        oid, bt = campuses[0]
        return CAMPUS_BOUNDARY_NAMES[bt["name"]], [oid, *[item[0] for item in other]], "shared_boundary_gate"
    if other:
        return None, [oid for oid, _ in other], "other_education_area"
    if campuses:
        oid, bt = campuses[0]
        return CAMPUS_BOUNDARY_NAMES[bt["name"]], [oid], None
    # Surveyed gates can be a few metres outside the area trace; 12m is a review
    # tolerance only for explicitly tagged entrances, never for general POIs.
    if tags.get("barrier") == "gate" or tags.get("entrance"):
        near = [(geom.distance(point), oid, bt) for oid, bt, geom in boundaries
                if bt.get("name") in CAMPUS_BOUNDARY_NAMES]
        if near:
            distance, oid, bt = min(near, key=lambda item: item[0])
            if distance <= 0.0001:
                return CAMPUS_BOUNDARY_NAMES[bt["name"]], [oid], "boundary_gate_review"
    return None, [], "outside_osm_campus"


def source_aliases(tags, name):
    aliases = []
    for key in ("name:zh", "official_name", "short_name", "alt_name", "loc_name"):
        for label in re.split(r"[;；]", tags.get(key, "")):
            label = label.strip()
            if label and label != name and label not in aliases:
                aliases.append(label)
    return aliases


def source_name_matches(labels, existing, lng, lat):
    # Name equality is evidence only when coordinates agree. Common gate/shop
    # labels recur across campus; distant matches stay visible as conflicts.
    nearby, distant = {}, {}
    for poi in existing:
        if set(labels).intersection([poi["name"], *poi.get("aliases", [])]):
            distance = haversine_m(lng, lat, poi["coordinates"]["lng"], poi["coordinates"]["lat"])
            (nearby if distance <= 80 else distant)[poi["id"]] = poi
    return nearby, distant


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pbf", help="OSM 区域 PBF 快照；读取时需安装 osmium")
    parser.add_argument("--evidence", help="osm_pbf_evidence.py 提取的完整要素 JSON，避免重复扫描省级快照")
    args = parser.parse_args()
    with open(POIS_PATH, encoding="utf-8") as f:
        existing = json.load(f)["pois"]
    existing_by_name = {}
    for poi in existing:
        for label in [poi["name"], *poi.get("aliases", [])]:
            if label and label.strip():
                existing_by_name.setdefault(label.strip(), []).append(poi)

    if args.evidence:
        with open(args.evidence, encoding="utf-8") as handle:
            elements = SourceElements(json.load(handle))
    else:
        elements = fetch_pbf(args.pbf) if args.pbf else fetch_overpass()
    boundaries = load_boundaries(elements)

    records = []
    excluded = []
    stats = {"total": 0, "named": 0, "kept": 0}
    for el in elements:
        stats["total"] += 1
        tags = el.get("tags", {}) or {}
        name = (tags.get("name") or tags.get("name:zh") or "").strip()
        if not name:
            excluded.append({"osm_id": f"{el['type']}/{el['id']}", "reason": "unnamed"})
            continue
        stats["named"] += 1
        source_id = f"{el['type']}/{el['id']}"
        # 坐标：node 用 lat/lon；way/relation 用 center
        if el["type"] == "node":
            wlat, wlng = el.get("lat"), el.get("lon")
        else:
            c = el.get("center") or {}
            wlat, wlng = c.get("lat"), c.get("lon")
        if wlat is None or wlng is None:
            excluded.append({"osm_id": source_id, "name": name, "reason": "no_geometry"})
            continue

        if not any(key in tags for key in ("amenity", "leisure", "tourism", "historic", "building", "entrance", "shop", "office", "healthcare")) and tags.get("barrier") != "gate":
            excluded.append({"osm_id": source_id, "name": name, "reason": "not_poi_feature"})
            continue
        if name in CAMPUS_BOUNDARY_NAMES or name == "武汉大学":
            excluded.append({"osm_id": source_id, "name": name, "reason": "campus_area_not_destination"})
            continue
        if _GENERIC_DORM.match(name) or _TRAILING_JUNK.search(name):
            excluded.append({"osm_id": source_id, "name": name, "reason": "generic_or_dirty_name"})
            continue
        campus, boundary_refs, boundary_flag = campus_evidence(wlng, wlat, tags, boundaries)
        if campus is None:
            excluded.append({"osm_id": source_id, "name": name, "reason": boundary_flag,
                             "boundary_refs": boundary_refs, "osm_tags": tags})
            continue
        if any(b in name for b in getattr(__import__("config"), "POI_NAME_BLACKLIST", ())):
            excluded.append({"osm_id": source_id, "name": name, "reason": "name_blacklist_requires_review"})
            continue
        glng, glat = wgs84_to_gcj02(wlng, wlat)
        aliases = source_aliases(tags, name)
        category = classify(tags, name)
        matched, distant = source_name_matches((name, *aliases), existing, glng, glat)
        match_distance = min((
            haversine_m(glng, glat, poi["coordinates"]["lng"], poi["coordinates"]["lat"])
            for poi in matched.values()
        ), default=None)
        nearest_same_category = min((
            (haversine_m(glng, glat, poi["coordinates"]["lng"],
                         poi["coordinates"]["lat"]), poi["id"])
            for poi in existing if poi.get("category", poi.get("type")) == category
        ), default=None)
        review_flags = [boundary_flag] if boundary_flag else []
        if distant:
            review_flags.append("same_name_distant_existing")
        if any(r["name"] == name and haversine_m(glng, glat, r["lng"], r["lat"]) < 200 for r in records):
            review_flags.append("same_name_nearby_source_object")
        if match_distance is not None and match_distance > 50:
            review_flags.append("coordinate_conflict")
        if not matched and nearest_same_category and nearest_same_category[0] < 30:
            review_flags.append("possible_alias_of_nearby_poi")
        records.append({
            "osm_id": f"{el['type']}/{el['id']}",
            "name": name,
            "aliases": list(dict.fromkeys(aliases)),
            "name_en": tags.get("name:en", ""),
            "lng": round(glng, 6),
            "lat": round(glat, 6),
            "campus": campus,
            "category": category,
            "osm_tags": tags,
            "campus_boundary_refs": boundary_refs,
            "coordinate_system": "GCJ-02",
            "geometry_method": "complete_area_bbox_center" if el.get("geometry") else "source_point_or_way_center",
            "already_exists": bool(matched),
            "matched_poi_ids": sorted(matched),
            "distant_name_match_poi_ids": sorted(distant),
            "distance_to_existing_m": round(match_distance, 1) if match_distance is not None else None,
            "nearby_same_category_poi_id": (
                nearest_same_category[1] if nearest_same_category and nearest_same_category[0] < 30
                else None
            ),
            "review_flags": review_flags,
            "source_refs": [{"source": "OpenStreetMap", "id": source_id,
                             "license": "ODbL-1.0"}],
            "verification_status": "source_only",
        })
        stats["kept"] += 1

    records.sort(key=lambda r: (r["campus"], r["category"], r["name"]))
    out = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "source": (f"OpenStreetMap complete evidence: {os.path.basename(args.evidence)}" if args.evidence
                   else f"OpenStreetMap PBF extract: {os.path.basename(args.pbf)}"
                   if args.pbf else "OpenStreetMap via Overpass API")
                  + "（WGS-84 已转 GCJ-02）",
        "count": len(records),
        "source_snapshot": getattr(elements, "source_file", None),
        "boundary_source": "OpenStreetMap assembled campus multipolygons; WGS84",
        "source_only_meaning": "来源已记录，尚无独立现场核验；不是质量评分",
        "candidates": records,
        "missing_from_production": [
            {"osm_id": r["osm_id"], "name": r["name"],
             "campus": r["campus"], "category": r["category"],
             "lng": r["lng"], "lat": r["lat"],
             "reason": "source_present_production_name_missing",
             "review_flags": r["review_flags"],
             "nearby_same_category_poi_id": r["nearby_same_category_poi_id"]}
            for r in records if not r["already_exists"]
        ],
        "excluded": excluded,
        "review_policy": "候选不自动进入正式 POI；需核对学部、名称、坐标及可导航目的地",
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"\n===== 抓取完成 =====")
    print(f"要素 {stats['total']} → 有名称 {stats['named']} → 校内保留 {stats['kept']}")
    print(f"与正式库名称/别名及位置匹配: {sum(1 for r in records if r['already_exists'])} 条")
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
