"""
POI 合并脚本：现有 24 个手工 POI（权威语义）+ OSM 候选 300 条（坐标+类别+学部）

合并规则：
  1. 同名且坐标接近（<200m）：视为同一地，保留现有语义字段，坐标取 OSM（更准）；
  2. 同名但坐标差 >500m：改名区分（OSM 新点加学部后缀），各自保留；
  3. 新点：自动补别名、描述、季节标签、风景评分；
  4. 线性地标（樱花大道等）：保留现有手工坐标。

输出：data/pois.json（新结构，新增 campus / category 字段）
"""

import json
import math
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POIS_PATH = os.path.join(ROOT, "data", "pois.json")
OSM_PATH = os.path.join(ROOT, "data", "pois_osm_candidates.json")
OUT_PATH = POIS_PATH  # 直接覆盖

# 线性地标（保留现有坐标，不用 OSM 点覆盖）
LINEAR_LANDMARKS = {"樱花大道", "珞珈山", "自强大道", "凌波门"}


def hav(p1, p2):
    R = 6371000.0
    p1a, p2a = math.radians(p1[1]), math.radians(p2[1])
    dp = math.radians(p2[1] - p1[1])
    dl = math.radians(p2[0] - p1[0])
    a = math.sin(dp / 2) ** 2 + math.cos(p1a) * math.cos(p2a) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def gen_aliases(name, campus, category):
    """根据名称规则生成别名列表。"""
    aliases = []
    # 去"武汉大学"前缀
    stripped = re.sub(r"^武汉大学", "", name).strip()
    if stripped and stripped != name:
        aliases.append(stripped)
    # 信息学部教学楼简称
    m = re.match(r"信息学部(\d+)号教学楼", name)
    if m:
        n = m.group(1)
        cn = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 7: "七"}.get(int(n), n)
        aliases.extend([f"信部{n}教", f"信部{cn}教", f"{n}号教学楼"])
    # 工学部教学楼
    m = re.match(r"工学部(\d+)号教学楼", name)
    if m:
        n = m.group(1)
        aliases.extend([f"工学部{n}教", f"工{n}教"])
    # 文理学部X号教学楼
    m = re.match(r"(\d+)号教学楼", name)
    if m and campus == "文理学部":
        n = m.group(1)
        aliases.append(f"{n}教")
    # 食堂简称
    if category == "dining":
        aliases.append(re.sub(r"食堂$", "", name))
    # 宿舍简称
    if category == "dorm":
        short = re.sub(r"学生宿舍|学生公寓", "", name).strip()
        if short:
            aliases.append(short)
    # 图书馆简称
    if "图书馆" in name and name != "图书馆":
        aliases.append(re.sub(r"图书馆$", "图", name))
    return [a for a in aliases if a and a != name]


def gen_description(name, campus, category):
    """生成一句话描述。"""
    if category == "study" and "教学楼" in name:
        return f"武汉大学{campus}教学楼，位于{campus}核心教学区。"
    if category == "study" and "图书馆" in name:
        return f"武汉大学{campus}图书馆，提供借阅和自习服务。"
    if category == "study" and "学院" in name:
        return f"武汉大学{name}，位于{campus}。"
    if category == "dining":
        return f"武汉大学{campus}食堂，提供日常餐饮。"
    if category == "sports":
        return f"武汉大学{campus}体育设施。"
    if category == "dorm":
        return f"武汉大学{campus}学生宿舍区。"
    if category == "gate":
        return f"武汉大学{name}，校园出入口之一。"
    if category == "scenery":
        return f"武汉大学校园景点，位于{campus}。"
    return f"武汉大学{campus}地标。"


def default_scenery(category):
    return {
        "scenery": 4, "gate": 2, "landmark": 2,
        "study": 1, "dining": 1, "sports": 1, "dorm": 1,
    }.get(category, 1)


def main():
    pois = json.load(open(POIS_PATH, encoding="utf-8"))["pois"]
    osm = json.load(open(OSM_PATH, encoding="utf-8"))["candidates"]

    existing = {p["name"]: p for p in pois}
    osm_by_name = {}
    for o in osm:
        osm_by_name.setdefault(o["name"], []).append(o)

    result = []
    used_osm = set()

    # ---- 第一遍：处理现有 24 个手工 POI ----
    for p in pois:
        name = p["name"]
        coord = (p["coordinates"]["lng"], p["coordinates"]["lat"])
        campus = p.get("campus") or "文理学部"
        category = p.get("category") or p.get("type", "landmark")

        # 找 OSM 中同名点
        candidates = osm_by_name.get(name, [])
        best = None
        best_d = 1e9
        for o in candidates:
            d = hav(coord, (o["lng"], o["lat"]))
            if d < best_d:
                best_d, best = d, o

        new_p = dict(p)
        # 加新字段（兼容旧数据没有 category/campus 的情况）
        new_p["campus"] = campus
        new_p["category"] = category
        new_p.setdefault("season_tags", ["all"])
        new_p.setdefault("scenery_score", default_scenery(category))

        # 线性地标保留现有坐标；其他若 OSM 有近点且更准，用 OSM 坐标
        if name not in LINEAR_LANDMARKS and best is not None and best_d < 200:
            new_p["coordinates"] = {"lng": best["lng"], "lat": best["lat"]}
            used_osm.add(id(best))

        result.append(new_p)

    # ---- 第二遍：处理 OSM 中新点 ----
    existing_names = {p["name"] for p in result}
    for o in osm:
        if id(o) in used_osm:
            continue
        name = o["name"]
        campus = o["campus"]
        category = o["category"]

        # 同名已存在（坐标接近的已合并，坐标远的是歧义点）
        if name in existing_names:
            # 改名区分：加学部后缀
            new_name = f"{name}（{campus}）"
            if new_name in existing_names:
                continue  # 已存在，跳过
            final_name = new_name
        else:
            final_name = name

        new_poi = {
            "id": f"poi_{len(result)+1:03d}",
            "name": final_name,
            "aliases": gen_aliases(name, campus, category),
            "coordinates": {"lng": o["lng"], "lat": o["lat"]},
            "type": "landmark" if category == "landmark" else category,
            "campus": campus,
            "category": category,
            "description": gen_description(final_name, campus, category),
            "season_tags": ["all"],
            "scenery_score": default_scenery(category),
        }
        result.append(new_poi)
        existing_names.add(final_name)

    # 重新编号
    for i, p in enumerate(result, 1):
        p["id"] = f"poi_{i:03d}"

    out = {
        "version": "2.0",
        "source": "高德API(原24条) + OpenStreetMap(300条，经GCJ-02转换)",
        "campuses": ["文理学部", "工学部", "信息学部"],
        "count": len(result),
        "pois": result,
    }

    # 备份原文件
    bak = POIS_PATH + ".bak"
    with open(POIS_PATH, encoding="utf-8") as f:
        open(bak, "w", encoding="utf-8").write(f.read())

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # 统计
    by_campus = {}
    by_cat = {}
    for p in result:
        by_campus[p["campus"]] = by_campus.get(p["campus"], 0) + 1
        by_cat[p["category"]] = by_cat.get(p["category"], 0) + 1
    print(f"合并完成：{len(result)} 个 POI（原 {len(pois)} 条 + 新增 {len(result)-len(pois)} 条）")
    print(f"备份 → {os.path.basename(bak)}")
    print(f"按学部: {by_campus}")
    print(f"按类别: {by_cat}")


if __name__ == "__main__":
    main()
