# -*- coding: utf-8 -*-
"""补充高价值缺失 POI 与俗称别名（2026-09-06 用户反馈）。

依据高德 Web 服务 API 逐点核实（place/text + 坐标比对）：
  新增:
    - 武汉大学工学部集贸市场 (114.356937, 30.540298) 文理学部 service（多边形判定与皇冠幸福里一致）
    - 武汉大学明珠园        (114.358940, 30.542002) 工学部  service
  别名追加:
    - 九一二运动场已有条目   += 912操场/九一二操场/9·12操场（俗称缺失导致查询 MISS）
    - 皇冠幸福里(工学部店)      += 皇冠蛋糕          （高德糕饼店，自强超市综合楼1层）
    - 工学部操场               += 奥场/奥林匹克运动场/工学部田径场（高德田径场同点 13m 内）
    - 国际教育学院(桂园楼)      += 教六/教6楼/6教/第六教学楼（高德教6号楼同点，与现有别名"文理学部第六教学楼"同物）
"""
import json
import re

PATH = 'data/pois.json'

with open(PATH, encoding='utf-8') as f:
    data = json.load(f)

pois = data['pois']

# ---------- 别名冲突预检 ----------
def find_alias_owner(name_or_alias):
    owners = []
    for p in pois:
        if name_or_alias == p['name'] or name_or_alias in (p.get('aliases') or []):
            owners.append(p['name'])
    return owners

ALIAS_APPEND = {
    '皇冠幸福里(工学部店)': ['皇冠蛋糕'],
    '工学部操场': ['奥场', '奥林匹克运动场', '工学部田径场'],
    '武汉大学国际教育学院(桂园楼)': ['教六', '教6楼', '6教', '第六教学楼'],
    '武汉大学九一二运动场': ['912操场', '九一二操场', '9·12操场', '912运动场'],
}

for target, adds in ALIAS_APPEND.items():
    entry = next((p for p in pois if p['name'] == target), None)
    assert entry, f'未找到目标 POI: {target}'
    for a in adds:
        owners = find_alias_owner(a)
        conflict = [o for o in owners if o != target]
        assert not conflict, f'别名冲突: {a!r} 已属于 {conflict}'
        if a not in (entry.get('aliases') or []):
            entry.setdefault('aliases', []).append(a)
    print(f'OK 别名追加 {target}: +{adds}')

# ---------- 新增 POI ----------
def next_id():
    nums = [int(re.findall(r'\d+', p['id'])[0]) for p in pois if re.findall(r'\d+', p['id'])]
    return max(nums) + 1

NEW_POIS = [
    {
        'name': '武汉大学工学部集贸市场',
        'aliases': ['工学部菜市场', '工学部菜场', '武大菜场', '集贸市场'],
        'coordinates': {'lng': 114.356937, 'lat': 30.540298},
        'type': 'service',
        'campus': '文理学部',
        'description': '文理学部校内地点。工学部集贸市场（工学部菜市场），茶港路计算机大楼后，生鲜副食综合市场（高德核实）。',
    },
    {
        'name': '武汉大学明珠园',
        'aliases': ['明珠园宾馆', '明珠园接待中心'],
        'coordinates': {'lng': 114.358940, 'lat': 30.542002},
        'type': 'service',
        'campus': '工学部',
        'description': '工学部校内地点。明珠园，东湖南路湖边接待中心/宾馆，2楼为国际教育中心（高德核实）。',
    },
]

for spec in NEW_POIS:
    if any(p['name'] == spec['name'] for p in pois):
        print(f"SKIP 已存在: {spec['name']}")
        continue
    owners = find_alias_owner(spec['name'])
    assert not owners, f"新名称冲突: {spec['name']} 已属于 {owners}"
    for a in spec['aliases']:
        owners = find_alias_owner(a)
        assert not owners, f"新别名冲突: {a!r} 已属于 {owners}"
    entry = {
        'id': f'poi_{next_id()}',
        'name': spec['name'],
        'aliases': spec['aliases'],
        'coordinates': spec['coordinates'],
        'type': spec['type'],
        'campus': spec['campus'],
        'category': spec['type'],
        'description': spec['description'],
        'season_tags': ['all'],
        'scenery_score': 1,
        'rating': None,
    }
    pois.append(entry)
    print(f"OK 新增 {entry['id']} {entry['name']} ({entry['campus']}/{entry['type']})")

data['count'] = len(pois)

with open(PATH, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f'\n完成，共 {len(pois)} 条 POI')
