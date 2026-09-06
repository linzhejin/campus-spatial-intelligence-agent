# -*- coding: utf-8 -*-
"""第三轮校区标签修正：工学部教楼群标签翻转 + 删除校外点 poi_003。

依据：
- v6 校区多边形（36 个实测锚点全部唯一归属）+ 高德 regeo 证据；
- poi_018/021/033/055/084/167/190 名称均为工学部教学/科研单位，
  坐标经 PIP 判定落入工学部多边形；
- poi_003 武昌区防震减灾和信息综合楼 regeo 为"湖北省地震监测中心/银海山庄"，
  属校外住宅小区与省直单位，删除。
"""
import io, json, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

path = "data/pois.json"
data = json.load(open(path, encoding="utf-8"))
pois = data["pois"]

FLIP_TO_GONG = [
    "poi_018",  # 土木建筑工程学院结构楼
    "poi_021",  # 大学生工程训练与创新实践中心
    "poi_033",  # 新型电力系统与国际标准研究院
    "poi_055",  # 科学技术发展研究院
    "poi_084",  # 网球场（工学部片区）
    "poi_167",  # 中国邮政樱花邮政所（田园食堂旁）
    "poi_190",  # 工学部高电压绝缘监测实验室
]
DELETE = ["poi_003"]  # 防震减灾和信息综合楼（校外：银海山庄/省地震监测中心）

flipped, deleted = [], []
for p in pois:
    if p["id"] in FLIP_TO_GONG:
        old = p.get("campus")
        p["campus"] = "工学部"
        flipped.append((p["id"], p["name"], old))
    if p["id"] in DELETE:
        deleted.append((p["id"], p["name"]))

pois[:] = [p for p in pois if p["id"] not in DELETE]

json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print(f"翻转 → 工学部 {len(flipped)} 个：")
for pid, name, old in flipped:
    print(f"  {pid} {name}（原 {old}）")
print(f"删除校外点 {len(deleted)} 个：")
for pid, name in deleted:
    print(f"  {pid} {name}")
print(f"POI 总数: {len(pois)}")
