"""
更新武汉大学校门 POI 数据：
1. 修正错误坐标（珞瑜门、牌坊）
2. 修正校区归属（文澜门：信息学部→工学部）
3. 重命名南门→珞南门（官方名），补充别名
4. 新增缺失校门：弘毅门(西门)、北门、扬波门(东门)、西南门
"""
import json

with open("data/pois.json", encoding="utf-8") as f:
    data = json.load(f)

pois = data["pois"]
by_id = {p["id"]: p for p in pois}

# ===== 1. 修正珞瑜门坐标 =====
luoyu = by_id["poi_297"]
old_luoyu = (luoyu["coordinates"]["lng"], luoyu["coordinates"]["lat"])
luoyu["coordinates"] = {"lng": 114.3611, "lat": 30.5254}
luoyu["description"] = "信息学部校内地点。信息学部南端正门，门内友谊广场，门外珞喻路，地铁2号线广埠屯站可达（坐标经高德核实修正）。"
print(f"[修正] 珞瑜门: {old_luoyu} -> (114.3611, 30.5254)")

# ===== 2. 修正牌坊坐标（高德国立武汉大学牌楼新）=====
paifang = by_id["poi_140"]
old_pf = (paifang["coordinates"]["lng"], paifang["coordinates"]["lat"])
paifang["coordinates"] = {"lng": 114.358238, "lat": 30.533340}
paifang["description"] = "文理学部校内地点。武大正门八一路老牌坊，国立武汉大学题字，校园主入口（高德坐标：国立武汉大学牌楼新）。"
print(f"[修正] 牌坊: {old_pf} -> (114.358238, 30.533340)")

# ===== 3. 修正文澜门校区归属 =====
wenlan = by_id["poi_295"]
old_campus = wenlan["campus"]
wenlan["campus"] = "工学部"
wenlan["description"] = "工学部校内地点。靠近东湖湖岸线，位于工学部，打车进校常用门（高德核实，原误标信息学部）。"
print(f"[修正] 文澜门 campus: {old_campus} -> 工学部")

# ===== 4. 南门→珞南门（官方名）=====
nanmen = by_id["poi_138"]
old_name = nanmen["name"]
nanmen["name"] = "珞南门"
# 保留旧别名，补充官方名和附中门
existing_aliases = set(nanmen.get("aliases", []))
existing_aliases.update(["武汉大学南门", "南门", "附中门", "武大附中门"])
nanmen["aliases"] = sorted(existing_aliases)
nanmen["description"] = "文理学部/信息学部交界处校内地点。八一路广八路附近，又称附中门，地铁2号线广埠屯站可达（官方名珞南门）。"
print(f"[重命名] {old_name} -> 珞南门 (aliases+附中门)")

# ===== 5. 新增校门 =====
new_gates = [
    {
        "id": "poi_301",
        "name": "弘毅门",
        "aliases": ["西门", "武汉大学西门", "武大西门", "弘毅门(西门)"],
        "coordinates": {"lng": 114.35671, "lat": 30.535388},
        "type": "gate",
        "campus": "文理学部",
        "category": "gate",
        "description": "文理学部校内地点。西门，卓尔体育馆附近，正对珞狮路，本部最大交通要塞之一（高德核实）。",
        "season_tags": ["all"],
        "scenery_score": 1,
    },
    {
        "id": "poi_302",
        "name": "北门",
        "aliases": ["武汉大学北门", "武大北门", "工学部北门"],
        "coordinates": {"lng": 114.362596, "lat": 30.544989},
        "type": "gate",
        "campus": "工学部",
        "category": "gate",
        "description": "工学部校内地点。校园北侧出入口，靠近工学部教学楼（高德核实）。",
        "season_tags": ["all"],
        "scenery_score": 1,
    },
    {
        "id": "poi_303",
        "name": "扬波门",
        "aliases": ["东门", "武汉大学东门", "武大东门", "扬波门(东门)"],
        "coordinates": {"lng": 114.377272, "lat": 30.535162},
        "type": "gate",
        "campus": "文理学部",
        "category": "gate",
        "description": "文理学部校内地点。东门，武大最东侧，珞珈山脚下东湖湖岸线，校外可达一棵树进入东湖绿道（高德核实）。",
        "season_tags": ["all"],
        "scenery_score": 1,
    },
    {
        "id": "poi_304",
        "name": "西南门",
        "aliases": ["武汉大学西南门", "武大西南门"],
        "coordinates": {"lng": 114.3565, "lat": 30.5325},
        "type": "gate",
        "campus": "文理学部",
        "category": "gate",
        "description": "文理学部校内地点。校园西南侧出入口（高德地图有此门，坐标为珞珈山路附近近似值，待精确核实）。",
        "season_tags": ["all"],
        "scenery_score": 1,
    },
]

for g in new_gates:
    pois.append(g)
    print(f"[新增] {g['name']} ({g['coordinates']['lng']}, {g['coordinates']['lat']}) {g['campus']}")

data["pois"] = pois

with open("data/pois.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"\n更新完成。总POI数: {len(pois)}, 校门数: {len([p for p in pois if p.get('type')=='gate'])}")
