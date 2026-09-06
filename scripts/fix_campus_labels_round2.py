# -*- coding: utf-8 -*-
"""第二批校区标签修正：工学部楼宇群翻转、创业俱乐部归信部、删除 3 个校外点。"""
import json, io, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

path = "data/pois.json"
data = json.load(io.open(path, encoding="utf-8"))
pois = data["pois"] if isinstance(data, dict) and "pois" in data else data

TO_GONGXUE = {
    "poi_002": "动力与机械学院机械工程系（工学部教楼群）",
    "poi_008": "力学实验教学中心（工学部）",
    "poi_009": "智慧能源研究中心（工学部）",
    "poi_016": "图书馆工学分馆（工学部）",
    "poi_017": "土木建筑工程学院结构实验中心（工学部）",
    "poi_019": "城市设计学院（工学部）",
    "poi_023": "智能电网研究中心（工学部）",
    "poi_026": "教学楼9号楼（工学部）",
    "poi_027": "教育科学学院（工学部）",
    "poi_040": "桃园教8楼（工学部）",
    "poi_041": "水利水电学院（工学部）",
    "poi_042": "水力机械过渡过程重点实验室（工学部弘毅大道）",
    "poi_043": "水工程科学研究院（工学部）",
    "poi_059": "资源与环境科学学院环境工程系（工学部）",
    "poi_091": "学生14舍（工学部）",
    "poi_104": "枫园留学生宿舍点位于工学部松园片区（近工学部体育馆）",
}
TO_XINXI = {
    "poi_020": "大学生创业俱乐部（高德 regeo 返回武汉大学信息学部）",
}
DELETE = {
    "poi_063": "武汉数据智能研究院：regeo 为珞狮路阳光大厦，在校外",
    "poi_065": "湖北经济学院四眼井：regeo 为珞南街四眼井社区，校外住宅小区",
    "poi_066": "湖北经济学院洪山校区学生宿舍：regeo 为湖北经济学院教职公寓，校外",
}

by_id = {p["id"]: p for p in pois}
flipped, deleted = [], []
for pid, why in TO_GONGXUE.items():
    p = by_id[pid]
    old = p.get("campus")
    p["campus"] = "工学部"
    flipped.append((pid, p["name"], old, "工学部"))
for pid, why in TO_XINXI.items():
    p = by_id[pid]
    old = p.get("campus")
    p["campus"] = "信息学部"
    flipped.append((pid, p["name"], old, "信息学部"))

keep = []
for p in pois:
    if p["id"] in DELETE:
        deleted.append((p["id"], p["name"]))
    else:
        keep.append(p)

out = data
if isinstance(data, dict) and "pois" in data:
    out["pois"] = keep
else:
    out = keep
json.dump(out, io.open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print("翻转 %d 个：" % len(flipped))
for pid, name, o, n in flipped:
    print(f"  {pid} {name}: {o} -> {n}")
print("删除 %d 个：" % len(deleted))
for pid, name in deleted:
    print(f"  {pid} {name}")
print("POI 总数：%d" % len(keep))
