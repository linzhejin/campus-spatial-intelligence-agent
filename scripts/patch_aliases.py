# -*- coding: utf-8 -*-
"""给关键 POI 补充用户常用简称别名（与 clean_aliases 配合：删宽泛、补精准）"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

POIS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "pois.json")

# 主名 → 新增别名列表（这些是精准简称，不会跨 POI 冲突）
ALIAS_PATCH = {
    "总图书馆": ["图书馆"],
    "信息学部7舍": ["信息学部七舍", "信七舍", "信部七舍"],
    "信息学部7号教学楼": ["信息学部七教", "信七教", "信部七教", "信七"],
    "信息学部1号教学楼": ["信息学部一教", "信一教", "信部一教"],
    "信息学部2号教学楼": ["信息学部二教", "信二教", "信部二教"],
    "信息学部3号教学楼": ["信息学部三教", "信三教", "信部三教"],
    "信息学部4号教学楼": ["信息学部四教", "信四教", "信部四教"],
    "信息学部5号教学楼": ["信息学部五教", "信五教", "信部五教"],
    "桂园二舍": ["桂二舍", "桂二"],
    "桂园二食堂": ["桂二食堂"],
    "梅园": ["梅操"],  # 梅园小操场常用简称
    "教五": ["第五教学楼", "文理学部五教"],
}

data = json.load(open(POIS_PATH, encoding="utf-8"))
name_to_poi = {p["name"]: p for p in data["pois"]}

added = 0
for name, new_aliases in ALIAS_PATCH.items():
    p = name_to_poi.get(name)
    if not p:
        print("跳过（POI不存在）:", name)
        continue
    existing = set(p.get("aliases") or [])
    for a in new_aliases:
        if a not in existing and a != name:
            existing.add(a)
            added += 1
    p["aliases"] = sorted(existing)

print(f"新增别名 {added} 条")
json.dump(data, open(POIS_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("已写回 pois.json")
