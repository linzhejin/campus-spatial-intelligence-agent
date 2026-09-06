# -*- coding: utf-8 -*-
"""分析并清理过于宽泛的 POI 别名（园区/学部名不能作为单个建筑别名）"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

POIS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "pois.json")

# 过于宽泛的别名黑名单：园区/学部/通用词，不能作为单个 POI 的别名
# 这些词会导致任何以该词开头的查询都误匹配到第一个含此别名的 POI
TOO_GENERIC = {
    "信息学部", "工学部", "文理学部",
    "桂园", "梅园", "枫园", "樱园", "湖滨",
    "武大", "武汉大学", "珞珈",
    "教学楼", "宿舍楼", "食堂", "体育馆", "操场",
    "图书馆", "行政楼",
    "一教", "二教", "三教", "四教", "五教",
    "一舍", "二舍", "三舍", "四舍", "五舍", "六舍", "七舍", "八舍", "九舍",
    "1教", "2教", "3教", "4教", "5教",
    "1舍", "2舍", "3舍", "4舍", "5舍", "6舍", "7舍", "8舍", "9舍",
}

data = json.load(open(POIS_PATH, encoding="utf-8"))
removed = []
for p in data["pois"]:
    aliases = p.get("aliases") or []
    cleaned = []
    for a in aliases:
        a_strip = a.strip()
        if not a_strip:
            continue
        # 跳过：空、过于宽泛、与主名相同、长度1的单字
        if a_strip in TOO_GENERIC:
            removed.append((p["name"], a_strip))
            continue
        if a_strip == p["name"]:
            continue
        if len(a_strip) <= 1:
            removed.append((p["name"], a_strip))
            continue
        cleaned.append(a_strip)
    p["aliases"] = cleaned

print(f"清理别名: 删除 {len(removed)} 条宽泛别名")
for name, alias in removed:
    print(f"  {name}: 去掉 '{alias}'")

json.dump(data, open(POIS_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"\n已写回 pois.json（{data['count']} 个 POI）")
