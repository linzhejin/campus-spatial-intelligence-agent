# -*- coding: utf-8 -*-
"""根据高德逆地理证据修正校区标签 + 别名冲突。"""
import json

with open("data/pois.json", encoding="utf-8") as f:
    data = json.load(f)
pois = data["pois"]
by_name = {p["name"]: p for p in pois}

# 高德逆地理/地址证据支持翻转到「文理学部」的点（原标工学部）
FLIP_TO_WENLI = [
    # 7 个高德明确返回"文理学部"
    "武汉大学中国边界与海洋研究院",
    "武汉大学区域经济研究中心",
    "武汉大学工学部高电压绝缘监测实验室",
    "武汉大学机械工程实验教学中心现代加工部",
    "武汉大学机械工程实验教学中心车削加工部",
    "武汉大学爱平音乐厅",
    # 人文社科楼群（枫园/珞珈山北麓，老武大校区）
    "武汉大学法学院",
    "武汉大学法学图书馆",
    "武汉大学经济与管理学院",
    "武汉大学商学院EMBA中心",
    "武汉大学外国语言文学学院",
    "武汉大学新闻与传播学院",
    "武汉大学社会学院",
    "武汉大学艺术学院",
    "武汉大学信息管理学院",
    "武汉大学发展研究院",
    "武汉大学国家文化发展研究院",
    "前沿交叉学科研究院",
    "武汉大学留学生教育学院",
    "武汉大学留学生宿舍1号楼",
    "武汉大学留学生宿舍2号楼",
    "留学生宿舍3号楼",
    # 服务/景点
    "珞珈自强超市",
    "武汉大学周恩来故居",
    "闻一多纪念馆",
    # 餐饮：桂园/集贸市场片区（桂园=文理学部）
    "花焙格各音乐餐吧",
    "小食坊粉丝煲(工学部集贸市场)",
    "桂圆馄饨(工学部集贸市场)",
    "恩施炕小土豆(武大店)",
    "不颠哥港式甜品店(武大店)",
    "武大第一炒酸奶店",
    "蔡明纬(工学部店)",
    "小伙计(工学部店)",
    "土黄牛肉面(工学部店)",
    "皇冠幸福里(工学部店)",
    "湘菜馆(武汉大学内店)",
    "川味坊(桂园一路店)",
    "武汉大学家常菜(桂园一路)",
    "大囍螺蛳粉(桂园一路)",
    "农家小炒(桂园一路)",
]
# 文理学部 -> 工学部（原武水电力文化广场，周围为工学部宿舍群）
FLIP_TO_GONG = ["武汉大学电力广场"]

import re
flipped = 0
for name in FLIP_TO_WENLI:
    p = by_name.get(name)
    if p is None:
        print(f"[缺失] {name}")
        continue
    if p["campus"] != "文理学部":
        p["campus"] = "文理学部"
        p["description"] = re.sub(r"^(文理学部|工学部|信息学部)校内地点", "文理学部校内地点",
                                  p.get("description", ""))
        flipped += 1
for name in FLIP_TO_GONG:
    p = by_name.get(name)
    if p and p["campus"] != "工学部":
        p["campus"] = "工学部"
        p["description"] = re.sub(r"^(文理学部|工学部|信息学部)校内地点", "工学部校内地点",
                                  p.get("description", ""))
        flipped += 1

# 别名冲突：'计算机学院大楼' 仅属计算机学院 poi_058
p052 = by_name["武汉大学电子信息学院海态实验室"]
p052["aliases"] = [a for a in p052["aliases"] if a != "计算机学院大楼"]
print("poi_052 移除错误别名 '计算机学院大楼'")

with open("data/pois.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f"校区标签翻转 {flipped} 个，已写回")
