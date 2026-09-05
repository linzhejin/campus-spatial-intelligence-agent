"""
严格清洗高德全量 POI（data/pois_new.json + 旧手工数据），只保留：
校门 / 教学科研 / 学生寝室 / 食堂 / 景点 / 校内中大型生活服务设施。
剔除：居民区（教工宿舍/家属区）、校外单位、校外小商铺、存疑编号点。
输出: data/pois_v2.json  + 人工核查清单 data/pois_review.json
"""
import json, re, math, os, time, urllib.request, urllib.parse
from pathlib import Path

OUT = Path('data/pois_v2.json')
REVIEW = Path('data/pois_review.json')


def _amap_key():
    env = Path('.env').read_text(encoding='utf-8') if Path('.env').exists() else ''
    for line in env.splitlines():
        if line.startswith('AMAP_WEB_KEY'):
            return line.split('=', 1)[1].strip()
    return ''


def amap_text(keywords, key, city='武汉'):
    params = {'key': key, 'keywords': keywords, 'city': city, 'citylimit': 'true',
              'offset': 25, 'page': 1, 'extensions': 'base'}
    url = 'https://restapi.amap.com/v3/place/text?' + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read().decode('utf-8'))

# ---------- 数学工具 ----------
def haversine(lng1, lat1, lng2, lat2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2-lat1), math.radians(lng2-lng1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(a))

CAMPUS_ANCHORS = {
    '文理学部': (114.3630, 30.5365),
    '工学部':   (114.3725, 30.5405),
    '信息学部': (114.3725, 30.5255),
}

# ---------- 学部归属 ----------
WLX_WORDS = ['枫园','梅园','桂园','樱园','湖滨','狮子山','珞珈山','老斋舍','樱顶',
             '樱花大道','牌坊','凌波门','半侧山','侧船山','情人坡','文理学部',
             '教五楼','第五教学楼','万林','珞珈门','珞珈广场']
XX_WORDS = ['星湖','信息学部','信部','武测','珞瑜门','珞瑜二门','广埠屯']
GX_WORDS = ['工学部','工部','武水','水院','洪波门']

def assign_campus(name, lng, lat):
    if any(w in name for w in XX_WORDS): return '信息学部'
    if any(w in name for w in GX_WORDS): return '工学部'
    if any(w in name for w in WLX_WORDS): return '文理学部'
    d = {c: haversine(lng,lat,a[0],a[1]) for c,a in CAMPUS_ANCHORS.items()}
    return min(d, key=d.get)

# ---------- 剔除规则 ----------
# 1. 名称命中即剔除
HARD_EXCLUDE = [
    # 校外单位
    r'华中师范大学', r'华师', r'武汉理工', r'武汉体育学院', r'体育学院', r'武体',
    r'职业技术学院', r'电力职', r'中国科学院', r'中科院', r'水生所', r'斑马鱼',
    r'卓刀泉中学', r'附属小学', r'附属中学', r'附属幼儿园', r'广埠屯小学',
    r'国家网络安全学院',  # 东西湖校区
    r'亚朵', r'街道口', r'劝业场',
    # 居民区/家属区
    r'教工宿舍', r'教职工宿舍', r'教师公寓', r'青年教师公寓', r'家属',
    r'\d+区\d+栋', r'\d+区$', r'单元', r'\(1门\)', r'（1门）',
    r'宿舍区.*(公寓|宿舍)$', r'^[^武]*(公寓|旅舍|旅馆|民宿|宾馆|酒店)',  # 私人公寓/旅舍
    r'疾控', r'民政局', r'司法局', r'环保局', r'水务', r'假肢', r'人防',
    r'社区', r'小区', r'村\d', r'东湖村', r'七二二', r'七环科技', r'融科',
    r'弘博公寓', r'三环学生公寓', r'卓遇公寓', r'华本公寓', r'初阳公寓',
    r'宏盛公寓', r'金鑫源', r'华图', r'考研', r'自习室',
    # 小商铺
    r'咖啡', r'奶茶', r'茶姬', r'茶饮', r'餐厅', r'饭馆', r'火锅', r'烧烤',
    r'米粉', r'面馆', r'小吃', r'快餐', r'便当', r'披萨', r'汉堡', r'寿司',
    r'料理', r'甜品', r'蛋糕', r'面包', r'烘焙', r'冰淇淋', r'酒吧', r'酒馆',
    r'美食(?!城)', r'大排档', r'餐饮', r'bar', r'棋牌', r'ktv', r'KTV',
    r'快递', r'丰巢', r'菜鸟', r'外卖', r'打印', r'洗衣', r'理发', r'美发',
    r'美容', r'美甲', r'按摩', r'足浴', r'眼镜', r'烟酒', r'彩票', r'网吧',
    r'影院', r'健身房(?!.*学校)', r'健身中心',
    r'停车场', r'车位', r'充电站', r'加油站',
    r'公交站', r'地铁站', r'交叉口', r'路口', r'^\S{2,6}路$',
    r'超市\(.*店', r'超市（.*店',  # 校外连锁超市分店
    r'群光', r'阜华', r'维多利', r'楚天府', r'天宝佳苑',
    # 无意义/整体点
    r'^武汉大学$', r'院士楼', r'外宾客舍',
    r'拱顶下',  # 老斋舍重复标注点（保留"拱门"主点，输出时清理名称）
    # 民宿/商业/居民区噪音
    r'湖景房', r'公寓\(.*店', r'公寓（.*店', r'高层',
    r'珞珈山店$', r'新村', r'银海', r'休养所', r'帆船基地', r'驿站(?!.*武大)',
    # 拍照点/子点噪音
    r'书架背景墙', r'拍日出', r'栏杆边', r'标志建筑$', r'大门和牌匾',
    r'^东湖南路武大', r'书香道', r'风光', r'北坡',
    r'7-ELEVEn|7-11|便利店|罗森', r'园\d+栋',
    r'^\S{0,6}路武大',
]

# 2. 地址含校外标志 -> 剔除
ADDR_EXCLUDE = ['华中师范', '华师', '卓刀泉', '体育学院', '职业技术', '中国科学院',
                '伏泉', '虎泉', '融智', '广卓', '卓刀泉南路', '卓刀泉北路',
                '珞瑜路152', '珞喻路152', '珞瑜路189', '珞喻路189',  # 华师/武体/电力职院门牌
                '珞瑜路20号', '珞喻路20', '群光', '阜华', '维多利',
                '桂子山']

# 3. 学生宿舍判定：必须明确是学生宿舍/学生公寓
STUDENT_DORM = [
    r'学生宿舍.*\d+\s*舍', r'学生\d+舍', r'学生宿舍\d+舍',
    r'信息学部\d+舍', r'工学部\d+舍',
    r'[梅桂枫樱]园\d+舍', r'[梅桂枫樱]园[一二三四五六七八九十]+舍',
    r'湖滨.*\d+\s*舍', r'湖滨[一二三四五六七八九十]+舍',
    r'枫园[一二三四五六七八九十百]+舍',
    r'国软C\d+舍', r'国际软件学院C\d+舍',
    r'国际园区.*学生宿舍', r'国际园区\d+栋',
    r'学生公寓\d+栋', r'留学生宿舍\d+号楼', r'东湖宿舍区男生',
    r'^[^教]*(学生)?宿舍[一二三四五六七八九十\d]+\s*舍',
    r'青年楼宿舍\d+栋',
]

# 4. 教学科研
ACADEMIC = ['教学楼','教楼','号教','学院','实验室','实验中心','实验教学','图书馆',
            '研究中心','研究院','科学会堂','重点实验室','文博花园','综合楼',
            '大学生活动中心','大创','音乐厅','会议中心','出版社','档案馆']

# 5. 校内生活服务（中大型，保留）
CAMPUS_SERVICE = ['自强超市','超市','校医院','医院(?!.*口腔)','门诊部','银行','ATM',
                  '邮局','邮政','体育馆','体育场','操场','运动场','游泳馆','游泳池',
                  '篮球场','足球场','网球场','羽毛球','体育中心','书店','新华书店',
                  '大学生服务','学生服务','食堂','风味','美食城']

# 6. 景点
SCENERY = ['樱顶','樱花大道','老斋舍','珞珈山','狮子山','火石山','牌坊','星湖',
           '落英湖','鲲鹏广场','珞珈广场','电力广场','半山庐','十八栋','李四光',
           '闻一多','六一纪念','凌波门','栈桥','码头','观景','东湖','山庄',
           '枫园','梅园','桂园','樱园','湖滨','情人坡','日字斋','老图书馆',
           '早期建筑','名人','故居','纪念馆','校史馆','博物馆']

# 校门
GATE = ['门$']

def is_gate(name):
    return bool(re.search(r'(珞珈门|凌波门|洪波门|珞瑜门|珞瑜二门|科技门|南三门|南二门|南门|北门|东门|西门|正门|牌坊|附中门)$', name)) \
        or name.endswith('校门')

def keep_poi(name, address):
    for pat in HARD_EXCLUDE:
        if re.search(pat, name):
            return False, f'名称剔除:{pat}'
    for a in ADDR_EXCLUDE:
        if a in (address or '') or a in name:
            return False, f'校外关键词:{a}'
    # 存疑：编号教学楼但地址不含武大/校内门牌号
    if re.search(r'(\d+|[一二三四五六七八九十]+)\s*(号)?教学楼', name) or '教楼' in name:
        if not re.search(r'武汉大学|珞瑜路129|珞喻路129|八一路299|珞珈山16|星湖|信息学部内|学校', address or ''):
            if '华中师范' in (address or '') or '华师' in (address or '') or '正门对面' in (address or ''):
                return False, '存疑编号教学楼(地址挂校外)'
    # 保留判定
    if is_gate(name):
        return True, '校门'
    if any(w in name for w in ACADEMIC) or re.search(r'[0-9一二三四五六七八九十]教$', name):
        return True, '教学科研'
    if any(re.search(p, name) for p in STUDENT_DORM):
        return True, '学生寝室'
    if re.search(r'食堂|风味|美食城', name) and re.search(r'枫园|梅园|桂园|樱园|湖滨|田园|工学部|信息学部|文理学部|武大', name):
        return True, '食堂'
    if any(w in name for w in SCENERY):
        return True, '景点'
    # 校内服务：中大型生活设施（自强超市为武大校内连锁；校医院/银行/邮政/书店/体育场馆/活动中心）
    if re.search(r'自强', name) and re.search(r'超市|商店|商场', name):
        return True, '校内服务'
    if re.search(r'武汉大学[-—]?(校医院|医院|门诊部)', name):
        return True, '校内服务'
    if re.search(r'武汉大学.*(超市|校医院|医院|银行|ATM|邮局|邮政|书店|体育馆|体育场|操场|运动场|游泳|篮球|足球|网球|羽毛球|活动中心|大学生)', name):
        return True, '校内服务'
    if re.search(r'^(枫园|梅园|桂园|樱园|湖滨|信息学部|工学部|文理学部).*(超市|体育馆|体育场|操场|运动场|游泳|篮球|足球|网球|活动中心)', name):
        return True, '校内服务'
    return False, '不在保留范围'

# ---------- 别名规则 ----------
BROAD = {'工学部','文理学部','信息学部','湖滨','枫园','梅园','桂园','樱园','星湖',
         '国际园区','图书馆','食堂','教学楼','宿舍'}
_CN = '零一二三四五六七八九'

def num_to_cn(n):
    n = int(n)
    if n < 10: return _CN[n]
    if n < 20: return '十' + (_CN[n-10] if n > 10 else '')
    if n < 100:
        t,o = divmod(n,10); return _CN[t]+'十'+(_CN[o] if o else '')
    h,r = divmod(n,100); return _CN[h]+'百'+(num_to_cn(r) if r else '')

def cn_to_num(s):
    if s in _CN: return str(_CN.index(s))
    if s.startswith('十'): return str(10 + (_CN.index(s[1]) if len(s)>1 else 0))
    if '十' in s:
        t,o = s.split('十'); return str(_CN.index(t)*10 + (_CN.index(o) if o else 0))
    if '百' in s:
        h,r = s.split('百'); return str(_CN.index(h)*100 + (int(cn_to_num(r)) if r else 0))
    return s

def num_variants(tok):
    if tok.isdigit():
        return list(dict.fromkeys([tok, num_to_cn(tok)]))
    if all(c in '零一二三四五六七八九十百' for c in tok) and tok:
        ar = cn_to_num(tok)
        return list(dict.fromkeys([ar, tok])) if ar.isdigit() else [tok]
    return [tok]

def gen_aliases(name, category):
    al = set()
    n = name.replace('武汉大学','').replace('武大','').strip().strip('-—')
    if n and n != name and len(n) >= 2:
        al.add(n)
    m = re.search(r'(\d+|[一二三四五六七八九十]{1,3})', n)
    if m:
        vt = m.group(1)
        vs = num_variants(vt)
        # 学生宿舍别名：信七=信息学部7舍 / 桂二=桂园2舍
        if '舍' in n or '宿舍' in n:
            for park,short in [('桂园','桂'),('梅园','梅'),('枫园','枫'),('樱园','樱')]:
                if park in n:
                    for v in vs:
                        al |= {f'{short}{v}舍', f'{short}{v}', f'{park}{v}舍'}
            if '湖滨' in n:
                for v in vs: al.add(f'湖滨{v}舍')
            if '信息学部' in n or '信部' in n:
                for v in vs:
                    al |= {f'信{v}舍', f'信{v}', f'信息学部{v}舍', f'信部{v}舍'}
            if '工学部' in n:
                for v in vs:
                    al |= {f'工{v}舍', f'工{v}', f'工学部{v}舍'}
        # 教学楼（含"工学部一教"这种X教简称）
        if '教学楼' in n or '教楼' in n or re.search(r'[0-9一二三四五六七八九十]教(?:学楼|楼)?$', n):
            if '信息学部' in n:
                for v in vs:
                    al |= {f'信{v}教', f'信部{v}教', f'信息学部{v}教', f'信息学部{v}教学楼', f'信息学部{v}号教学楼'}
            elif '工学部' in n:
                for v in vs:
                    al |= {f'工{v}教', f'工部{v}教', f'工学部{v}教', f'工学部{v}教学楼'}
            elif re.search(r'第\s*(\d+|[一二三四五六七八九十]+)\s*教学楼', name) or re.match(r'^教\s*\d+', n):
                for v in vs:
                    al |= {f'教{v}', f'{v}教', f'第{v}教学楼'}
        # 食堂
        if '食堂' in n or '风味' in n:
            for park,short in [('桂园','桂'),('梅园','梅'),('枫园','枫')]:
                if park in n:
                    for v in vs: al.add(f'{short}{v}食堂')
            if '信息学部' in n:
                for v in vs: al |= {f'信息学部{v}食堂', f'信部{v}食堂'}
            if '工学部' in n:
                for v in vs: al.add(f'工学部{v}食堂')
    # 图书馆
    if '图书馆' in n:
        if '总馆' in name:
            al |= {'总图书馆','总图','图书馆','文理图书馆','新图书馆'}
        if '信息学部' in n:
            al |= {'信图','信息图书馆','信息学部图书馆'}
        if '工学分馆' in n or ('工学部' in name and '图书馆' in name):
            al |= {'工学分馆','工图','工学部图书馆','工学部图'}
        if '老图书馆' in name:
            al |= {'老图','樱顶老图','校史馆'}
    # 校门
    gate_map = {'珞珈门':['正门','武大正门','国立武汉大学牌坊'],
                '珞瑜门':['信息学部正门','广埠屯校门','信息学部南门'],
                '珞瑜二门':['南二门','信息学部南二门'],
                '洪波门':['洪波门','工学部正门'],
                '凌波门':['凌波门','武大凌波门']}
    for g,a in gate_map.items():
        if g in name: al |= set(a)
    # 校内服务设施
    if '医院' in n or '校医院' in n:
        al |= {'校医院', '武大医院', '武汉大学医院'}
    if '自强' in n and ('超市' in n or '商店' in n):
        al |= {'自强超市', '武大超市', '校内超市'}
    if '邮政' in n or '邮局' in n:
        al |= {'武大邮政', '邮政所', '邮局'}
    if '银行' in n or 'ATM' in n:
        al.add('银行')
    return {a for a in al if a and a not in BROAD and len(a) <= 12 and a != name}

def category_of(name, why):
    if why == '校门' or is_gate(name): return 'gate'
    if '食堂' in name or '风味' in name or '美食城' in name: return 'dining'
    # 学生寝室：含"X舍"（阿拉伯/中文数字）或学生宿舍/公寓，优先于景点判定
    if re.search(r'[0-9一二三四五六七八九十百]+\s*舍', name) or re.search(r'学生宿舍|学生\d*宿舍|学生公寓|留学生宿舍|青年楼宿舍|国软C\d+|国际园区\d+栋|学生公寓\d+栋|东湖宿舍区', name):
        if not re.search(r'教学楼|学院', name):
            return 'dorm'
    # 体育设施（排球场/高尔夫等也归体育）
    if re.search(r'体育馆|体育场|操场|运动场|游泳|篮球|足球|网球|羽毛球|排球|高尔夫|体育中心|风雨操场', name):
        return 'sports'
    # 教学科研：教学楼/学院/阅览室/综合楼/实验大楼/阅览室等建筑，优先于景点
    if re.search(r'教学楼|教楼|学院|实验室|实验大楼|实验中心|实验教学|图书馆|研究中心|研究院|科学会堂|综合楼|阅览室|音乐厅|会议中心|出版社|档案馆|博物馆|教\d楼|教\d?$|大学生创业', name):
        return 'study'
    if any(w in name for w in ('自强','超市','校医院','医院','门诊部','银行','ATM','邮局','邮政','书店','活动中心')): return 'service'
    if is_gate(name): return 'gate'
    if any(w in name for w in SCENERY): return 'scenery'
    return 'study'


# ---------- 语义归一（用于跨数据源判重与别名冲突归属）----------
_PARK_PAT = re.compile(r'(枫园|梅园|桂园|樱园|湖滨|信息学部|信部|工学部|工部|文理学部)')
_TYPE_PAT = re.compile(r'(教学楼|教楼|食堂|风味|图书馆|体育馆|体育场|操场|舍|公寓|宿舍|门|超市|医院|银行)')
_NUM_PAT = re.compile(r'(\d+|[一二三四五六七八九十百]{1,3})')
_NUM_CN = '零一二三四五六七八九'

def _norm_num(tok):
    if tok.isdigit():
        return int(tok)
    if tok in _NUM_CN:
        return _NUM_CN.index(tok)
    if tok.startswith('十'):
        return 10 + (_NUM_CN.index(tok[1]) if len(tok) > 1 else 0)
    if '十' in tok:
        t, o = tok.split('十')
        return _NUM_CN.index(t) * 10 + (_NUM_CN.index(o) if o else 0)
    if '百' in tok:
        h, r = tok.split('百')
        return _NUM_CN.index(h) * 100 + (_norm_num(r) if r else 0)
    return None

def name_key(name):
    """返回 (园区/学部, 类型, 编号集合)，用于判定两个名称是否同一地点。"""
    park_m = _PARK_PAT.search(name)
    park = park_m.group(1) if park_m else ''
    type_m = _TYPE_PAT.search(name)
    ptype = type_m.group(1) if type_m else ''
    if ptype in ('公寓', '宿舍'):
        ptype = '舍'
    nums = set()
    for tok in _NUM_PAT.findall(name):
        v = _norm_num(tok)
        if v is not None:
            nums.add(v)
    return (park, ptype, frozenset(nums))


def alias_score(alias, poi_name):
    """别名与POI名称的相关度得分（用于冲突时选主）。"""
    s = 0
    ak = name_key(alias)
    pk = name_key(poi_name)
    if ak[0] and ak[0] == pk[0]:
        s += 2
    if ak[1] and ak[1] == pk[1]:
        s += 2
    if ak[2] and pk[2] and ak[2] & pk[2]:
        s += 3  # 编号一致（信七→7舍）
    if alias in poi_name or poi_name.replace('武汉大学', '') in alias:
        s += 1
    if '打卡点' in poi_name or '拱' in poi_name and '拱门' not in alias:
        s -= 1
    return s

def fetch_service_pois(key):
    """高德 text 搜索补抓校内中大型生活服务 POI（polygon 检索易漏）。"""
    if not key:
        return []
    queries = ['武汉大学自强超市', '武汉大学医院', '武汉大学校医院',
               '武汉大学银行', '武汉大学邮政', '武汉大学邮局',
               '武汉大学书店', '武汉大学超市', '武汉大学体育部']
    INTERNAL_ADDR = ['武汉大学', '珞瑜路129', '珞喻路129', '八一路299', '珞珈山16',
                     '东湖南路8', '星湖', '求是', '梅园', '枫园', '桂园', '樱园', '湖滨']
    out, seen_ids = [], set()
    for q in queries:
        try:
            res = amap_text(q, key)
            for p in (res.get('pois') or []):
                pid = p.get('id')
                name = p.get('name', '')
                addr = p.get('address', '') or ''
                if pid in seen_ids:
                    continue
                if not any(k in name for k in ('自强', '武汉大学', '武大')) and not any(a in addr for a in INTERNAL_ADDR):
                    continue
                if re.search(r'华中师范|华师|体育学院|职业技术|卓刀泉|伏泉|虎泉', name + addr):
                    continue
                if re.search(r'咖啡|奶茶|餐厅|小吃|快餐|公寓|酒店|宾馆|旅舍', name):
                    continue
                loc = p.get('location', '').split(',')
                if len(loc) != 2:
                    continue
                seen_ids.add(pid)
                out.append({'name': name,
                            'coordinates': {'lng': float(loc[0]), 'lat': float(loc[1])},
                            'description': addr, 'source': f'amap-text:{pid}', 'aliases': []})
        except Exception as e:
            print(f'  text搜索失败 {q}: {e}')
        time.sleep(0.2)
    return out


def clean_name(name):
    """清理高德名称中的噪音后缀（不动括号）。"""
    name = re.sub(r'\(打卡点\)|（打卡点）', '', name)
    name = name.replace('武汉大学-', '武汉大学').strip()
    return name


def _point_in_polygon(lng, lat, poly):
    inside = False
    n = len(poly); j = n - 1
    for i in range(n):
        xi, yi = poly[i]; xj, yj = poly[j]
        if ((yi > lat) != (yj > lat)) and (lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def in_campus(lng, lat):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import CAMPUS_POLYS_GCJ
    return any(_point_in_polygon(lng, lat, poly) for poly in CAMPUS_POLYS_GCJ.values())


def main():
    key = _amap_key()
    new = json.loads(Path('data/pois_new.json').read_text(encoding='utf-8'))['pois']
    svc = fetch_service_pois(key)
    print(f'text补抓服务POI候选: {len(svc)}')
    new = new + svc

    kept, dropped = [], []
    seen = set()
    for p in new:
        name = clean_name(p['name'])
        c = p.get('coordinates', {})
        lng, lat = c.get('lng'), c.get('lat')
        if not lng or name in seen:
            continue
        ok, why = keep_poi(name, p.get('description',''))
        if not ok:
            dropped.append({'name': name, 'reason': why, 'lng': lng, 'lat': lat,
                            'address': p.get('description',''), 'source': p.get('source','')})
            continue
        seen.add(name)
        kept.append({'name': name, 'lng': lng, 'lat': lat,
                     'address': p.get('description',''), 'why': why,
                     'source': p.get('source',''), 'aliases_old': list(p.get('aliases', []))})

    # 新高德点语义键索引（园区+类型+编号）
    def sem_key_of(k):
        pk, tp, nums = name_key(k['name'])
        # 学部/园区归一
        park = {'信部': '信息学部', '工部': '工学部'}.get(pk, pk)
        return (park, tp, tuple(sorted(nums)))
    sem_index = {}
    for k in kept:
        pk, tp, nums = sem_key_of(k)
        if nums:  # 只有带编号的点才用语义判重
            sem_index.setdefault((pk, tp, tuple(sorted(nums))), k)

    # 合并旧手工数据：坐标30m内或语义相同（同园区+同类型+同编号）则并入别名，不独立保留
    old_path = Path('data/legacy_pois_manual.json')
    if not old_path.exists():
        old_path = Path('data/pois.json')
    old = json.loads(old_path.read_text(encoding='utf-8'))['pois']
    # 旧手工点中已知坐标错误/高德无对应点的，直接弃用（避免2km偏差点混入）
    OLD_SKIP = {'科学会堂'}
    new_coords = [(k['lng'], k['lat']) for k in kept]
    new_names = {k['name'] for k in kept}
    merged = 0
    for op in old:
        name = clean_name(op['name'])
        cc = op.get('coordinates') or {}
        lng, lat = cc.get('lng') or op.get('lon'), cc.get('lat') or op.get('lat')
        if not lng or name in new_names or name in OLD_SKIP:
            continue
        ok, why = keep_poi(name, op.get('description',''))
        target = None
        is_old_gate = is_gate(name) or (op.get('type') in ('gate', 'landmark') and bool(re.search(r'门$|牌坊', name)))
        if not is_old_gate:
            hit = next((j for j,(x,y) in enumerate(new_coords) if haversine(lng,lat,x,y) < 30), None)
            if hit is not None:
                target = kept[hit]
            else:
                # 语义判重：旧点"信七"（错误坐标）应归属新高德7舍
                pk, tp, nums = name_key(name)
                park = {'信部': '信息学部', '工部': '工学部'}.get(pk, pk)
                if nums and (park, tp, tuple(sorted(nums))) in sem_index:
                    target = sem_index[(park, tp, tuple(sorted(nums)))]
        if target is not None:
            for a in [name] + op.get('aliases', []):
                a = clean_name(a) if a else a
                if a and a != target['name'] and a not in target['aliases_old'] and a not in BROAD:
                    target['aliases_old'].append(a)
            continue
        if not ok:
            continue
        kept.append({'name': name, 'lng': round(lng,6), 'lat': round(lat,6),
                     'address': op.get('description',''), 'why': why,
                     'source': 'manual', 'aliases_old': list(op.get('aliases', []))})
        new_names.add(name)
        merged += 1

    # 最终硬过滤：所有POI必须落在校园多边形内（GCJ-02坐标直接判定）
    before = len(kept)
    kept = [k for k in kept if in_campus(k['lng'], k['lat'])]
    print(f'校园多边形过滤: {before} -> {len(kept)}')

    # 生成别名；冲突时按语义相关度选唯一主点
    alias_owners = {}
    for i, k in enumerate(kept):
        k['campus'] = assign_campus(k['name'], k['lng'], k['lat'])
        k['category'] = category_of(k['name'], k['why'])
        for a in gen_aliases(k['name'], k['category']) | set(k['aliases_old']):
            if a and a not in BROAD and a != k['name']:
                alias_owners.setdefault(a, []).append(i)
    for a, owners in alias_owners.items():
        if len(owners) == 1:
            kept[owners[0]].setdefault('aliases', []).append(a)
        else:
            scores = sorted(((alias_score(a, kept[o]['name']), o) for o in owners),
                            key=lambda x: -x[0])
            if scores[0][0] > 0 and scores[0][0] > scores[1][0]:
                kept[scores[0][1]].setdefault('aliases', []).append(a)

    # 同点去重：30m内同类型点合并，但若编号不同（相邻楼栋/教学楼）则保留
    def name_quality(n):
        q = 0
        if n.startswith('武汉大学'): q += 2
        if re.search(r'拱门|拱顶|打卡点|雯雯|旗舰店|屋顶|建筑群', n): q -= 3  # 基础名优先于派生名
        return q
    kept.sort(key=lambda k: (name_quality(k['name']), -len(k['name'])), reverse=True)
    deduped = []
    merged_away = 0
    for k in kept:
        dup = None
        for d in deduped:
            if d['category'] != k['category']:
                continue
            dist = haversine(k['lng'], k['lat'], d['lng'], d['lat'])
            # 编号集合必须一致（防止"X学院"吞掉"X学院7舍"、"X宿舍"吞掉编号楼栋）
            kn = name_key(k['name'])[2]
            dn = name_key(d['name'])[2]
            diff_number = kn != dn
            if dist < 30:
                if diff_number and dist > 8:
                    continue
                dup = d
                break
            # 严格前缀同名（泛化点 vs 注释点，如"武汉大学图书馆" vs "武汉大学图书馆(总馆)"）100m内合并：
            # 编号一致 + 前缀后为括号注释（同点注释）或前缀本身>=5字（同单位附楼）
            if dist < 100 and not diff_number:
                a = k['name'].replace('武汉大学', '')
                b = d['name'].replace('武汉大学', '')
                if len(a) >= 3 and len(b) >= 3 and (a.startswith(b) or b.startswith(a)):
                    shorter, longer = sorted([a, b], key=len)
                    extra_ok = (len(longer) == len(shorter) or longer[len(shorter)] in '(（') or len(shorter) >= 5
                    if extra_ok:
                        # 保留更长更具体的名称作为主名（如"图书馆(总馆)"覆盖"图书馆"）
                        if len(k['name']) > len(d['name']):
                            old_name = d['name']
                            d.setdefault('aliases_old', []).append(old_name)
                            for aa in k.get('aliases_old', []):
                                d['aliases_old'].append(aa)
                            d['name'] = k['name']
                        dup = d
                        break
        if dup:
            merged_away += 1
            for a in [k['name']] + k.get('aliases', []) + k.get('aliases_old', []):
                if a and a != dup['name'] and a not in BROAD:
                    dup.setdefault('aliases', []).append(a)
        else:
            deduped.append(k)
    print(f'同点去重: {len(kept)} -> {len(deduped)}（合并{merged_away}个重复命名）')
    kept = deduped

    # 景点/园区语义合并：核心名相同（如"枫园"与"武汉大学枫园"、"珞珈山"与"武汉大学珞珈山"）；
    # 园区/山体是面状概念，众包点坐标常有较大偏差，放宽至3000m并保留"武汉大学"版
    PARK_CORE = ['樱花大道','枫园','梅园','桂园','樱园','珞珈山','狮子山','火石山','珞珈广场','鲲鹏广场']
    def core_of(n):
        base = re.sub(r'^武汉大学', '', n)
        base = re.sub(r'[（(].*$', '', base)
        for c in PARK_CORE:
            if base == c:
                return c
        return None
    kept.sort(key=lambda k: (k['name'].startswith('武汉大学'), -len(k['name'])), reverse=True)
    sem_merged = []
    for k in kept:
        c1 = core_of(k['name'])
        dup = None
        if c1:
            for d in sem_merged:
                if d['category'] == k['category'] and core_of(d['name']) == c1:
                    if haversine(k['lng'], k['lat'], d['lng'], d['lat']) < 3000:
                        dup = d
                        break
        if dup:
            for a in [k['name']] + k.get('aliases', []):
                if a and a != dup['name'] and a not in BROAD:
                    dup.setdefault('aliases', []).append(a)
        else:
            sem_merged.append(k)
    if len(sem_merged) != len(kept):
        print(f'景点语义合并: {len(kept)} -> {len(sem_merged)}')
    kept = sem_merged

    # 人工确认合并：高德"珞珈门"（八一路正门门洞，别名"正门/武大正门"）与"牌坊"
    # （牌坊广场）相距约64m，为同一入口；主名沿用师生习惯称谓"牌坊"，门名与别名并入
    def _manual_merge(src_name, target_name, max_dist=300):
        nonlocal kept
        ti = next((i for i, k in enumerate(kept) if k['name'] == target_name), None)
        si = next((i for i, k in enumerate(kept) if k['name'] == src_name), None)
        if ti is None or si is None:
            return
        t, s = kept[ti], kept[si]
        if haversine(t['lng'], t['lat'], s['lng'], s['lat']) > max_dist:
            return
        exist = set(t.get('aliases', []))
        for a in [s['name']] + s.get('aliases', []) + s.get('aliases_old', []):
            if a and a != t['name'] and a not in BROAD and a not in exist:
                t.setdefault('aliases', []).append(a)
                exist.add(a)
        kept = [k for j, k in enumerate(kept) if j != si]
        print(f'人工合并: {src_name} -> {target_name}（相距{haversine(t["lng"],t["lat"],s["lng"],s["lat"]):.0f}m）')
    _manual_merge('珞珈门', '牌坊')

    # 别名冲突清洗：别名若是另一个现存POI的名称核心（如旧手工点误挂的"国际教育学院"），删除
    all_names = [k['name'] for k in kept]
    stripped = 0
    for k in kept:
        clean = []
        for a in k.get('aliases', []):
            if len(a) >= 4 and any(a in n and a not in k['name'] for n in all_names):
                stripped += 1
                continue
            clean.append(a)
        k['aliases'] = clean
    if stripped:
        print(f'别名冲突清洗: 删除{stripped}个误挂别名')

    # 编号
    campus_ord = {'文理学部':0,'工学部':1,'信息学部':2}
    type_ord = {'study':0,'dining':1,'sports':2,'dorm':3,'gate':4,'scenery':5,'service':6}
    kept.sort(key=lambda k: (campus_ord[k['campus']], type_ord.get(k['category'],9), k['name']))
    for i, k in enumerate(kept, 1):
        k['id'] = f'poi_{i:03d}'

    out_pois = [{
        'id': k['id'], 'name': k['name'],
        'aliases': sorted(set(k.get('aliases', []))),
        'coordinates': {'lng': round(k['lng'],6), 'lat': round(k['lat'],6)},
        'type': k['category'], 'campus': k['campus'], 'category': k['category'],
        'description': f"{k['campus']}校内地点。{k['address']}",
        'season_tags': ['all'], 'scenery_score': 3 if k['category']=='scenery' else 1,
    } for k in kept]
    out = {
        'source': f'高德Web服务API严格清洗版：校门/教学/寝室/食堂/景点/校内服务（{len(kept)}条）',
        'count': len(kept),
        'pois': out_pois,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    REVIEW.write_text(json.dumps({'dropped_count': len(dropped), 'dropped': dropped},
                                 ensure_ascii=False, indent=2), encoding='utf-8')

    # 人工核查清单 CSV（编号教学楼/宿舍为重点核对对象）
    import csv
    with open('data/pois_review_list.csv', 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['id', '学部', '类型', '名称', '经度', '纬度', '别名', '高德地址', '需重点核对'])
        type_cn = {'study':'教学科研','dining':'食堂','sports':'体育','dorm':'寝室',
                   'gate':'校门','scenery':'景点','service':'校内服务'}
        for k, po in zip(kept, out_pois):
            focus = '★' if re.search(r'[0-9一二三四五六七八九十]+(号)?(教学楼|教|舍|栋)', k['name']) else ''
            w.writerow([k['id'], k['campus'], type_cn.get(k['category'], k['category']),
                        k['name'], round(k['lng'],6), round(k['lat'],6),
                        ' / '.join(po['aliases'][:6]), k['address'], focus])

    from collections import Counter
    print(f"保留 {len(kept)} | 旧手工独立补入 {merged} | 剔除 {len(dropped)}")
    print('按学部:', dict(Counter(k['campus'] for k in kept)))
    print('按类型:', dict(Counter(k['category'] for k in kept)))

if __name__ == '__main__':
    main()
