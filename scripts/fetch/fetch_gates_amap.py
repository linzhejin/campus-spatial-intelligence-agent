"""
精准拉取武大校门（高德 Web 服务 v2）

过滤策略：
  - 名称必须以"门"结尾 或 匹配"X门"模式（X为1-4字）
  - 排除：停车场/外卖柜/便利店/公交站/市场/纪念馆/宿舍/栋/舍/楼/客栈/维修/餐饮/牛羊肉等
  - 类型优先：风景名胜(11) / 科教文化(14) / 出入口
  - 必须与武大相关（名称/地址含武大标识 或 在校内锚点范围内）
"""
import json
import os
import re
import time
import math
from datetime import datetime

import httpx
from dotenv import dotenv_values

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = dotenv_values(os.path.join(PROJECT_ROOT, ".env"))
KEY = ENV.get("AMAP_WEB_KEY", "")

TEXT_API = "https://restapi.amap.com/v3/place/text"
AROUND_API = "https://restapi.amap.com/v3/place/around"

CAMPUSES = [
    {"name": "文理学部", "anchor": (114.3630, 30.5365), "radius": 1700},
    {"name": "工学部", "anchor": (114.3695, 30.5430), "radius": 1400},
    {"name": "信息学部", "anchor": (114.3725, 30.5255), "radius": 1500},
]

# 定向搜索的校门名（含可能遗漏的）
GATE_NAMES = [
    "武汉大学 扬波门", "武汉大学 西北门", "武汉大学 东北门", "武汉大学 小观园",
    "武汉大学 附中门", "武汉大学 三环门", "武汉大学 东门", "武汉大学 西门",
    "武汉大学 北门", "武汉大学 茶港门", "武汉大学 洪波门", "武汉大学 文澜门",
    "武汉大学 珞瑜门", "武汉大学 珞瑜二门", "武汉大学 南门", "武汉大学 科技门",
    "武汉大学 牌坊", "武汉大学 凌波门", "武汉大学 正门", "武汉大学 大门",
    "武汉大学 校门", "武大 扬波门", "武大 西北门", "武大 东门", "武大 西门",
    "武大 北门", "武大 小观园", "武大 附中门", "武大 三环",
]

# 通用搜索词
GENERIC_KW = ["武汉大学 门", "武大 门", "武汉大学 校门", "武汉大学 大门"]

WHU_HINTS = ("武汉大学", "武大", "珞珈", "whu", "WHU")

# 排除关键词（非校门）
EXCLUDE = ("停车场", "外卖柜", "便利店", "公交站", "市场", "纪念馆", "宿舍",
           "栋", "舍", "楼", "客栈", "维修", "餐饮", "牛羊肉", "超市", "药店",
           "门诊", "门口", "门市", "门卫", "门牌", "门店", "门窗", "门套", "门锁",
           "门槛", "门票", "单元", "出入口", "广场", "景区", "公园")

# 校门名称正则：以"门"结尾，前面是1-6个汉字/字母
GATE_PATTERN = re.compile(r'^[\u4e00-\u9fa5A-Za-z0-9]{1,8}门$')


def parse_loc(loc):
    if not loc or "," not in loc:
        return None
    lng_s, lat_s = loc.split(",")
    try:
        return float(lng_s), float(lat_s)
    except ValueError:
        return None


def is_real_gate(name):
    """判断是否是真正的校门名称。"""
    if not name:
        return False
    if any(b in name for b in EXCLUDE):
        return False
    # 以"门"结尾
    if not name.endswith("门"):
        # 也接受 "武汉大学XX门" 这种中间形式
        if not re.search(r'门$', name):
            return False
    # 必须以"门"结尾
    if not name.endswith("门"):
        return False
    # 排除太短的
    if len(name) < 2:
        return False
    return True


def fetch_text(client, keywords, city="武汉"):
    out = []
    for page in range(1, 21):
        params = {
            "key": KEY, "keywords": keywords, "city": city, "citylimit": "true",
            "offset": 25, "page": page, "extensions": "all",
        }
        try:
            r = client.get(TEXT_API, params=params, timeout=20)
            d = r.json()
        except Exception as e:
            print(f"  ! text异常 '{keywords}': {e}")
            break
        if d.get("status") != "1":
            print(f"  ! text错误 '{keywords}': {d.get('info')}")
            break
        pois = d.get("pois") or []
        out.extend(pois)
        if len(pois) < 25:
            break
        time.sleep(0.12)
    return out


def fetch_around(client, anchor, radius, keywords):
    out = []
    for page in range(1, 21):
        params = {
            "key": KEY, "location": f"{anchor[0]},{anchor[1]}", "radius": radius,
            "keywords": keywords, "offset": 25, "page": page, "extensions": "all",
        }
        try:
            r = client.get(AROUND_API, params=params, timeout=20)
            d = r.json()
        except Exception as e:
            print(f"  ! around异常: {e}")
            break
        if d.get("status") != "1":
            print(f"  ! around错误: {d.get('info')}")
            break
        pois = d.get("pois") or []
        out.extend(pois)
        if len(pois) < 25:
            break
        time.sleep(0.12)
    return out


def dist_m(lng1, lat1, lng2, lat2):
    return math.sqrt((lng1 - lng2) ** 2 + (lat1 - lat2) ** 2) * 111000


def main():
    if not KEY or KEY.startswith("your-"):
        print("缺少 AMAP_WEB_KEY")
        return 1

    raw = []
    with httpx.Client() as client:
        for kw in GATE_NAMES:
            pois = fetch_text(client, kw)
            if pois:
                print(f"[text] {kw:<20} -> {len(pois)} 条")
            raw.extend(pois)
            time.sleep(0.1)

        for kw in GENERIC_KW:
            pois = fetch_text(client, kw)
            print(f"[text] {kw:<20} -> {len(pois)} 条")
            raw.extend(pois)
            time.sleep(0.1)

        for camp in CAMPUSES:
            for kw in ["门", "校门", "大门"]:
                pois = fetch_around(client, camp["anchor"], camp["radius"], kw)
                if pois:
                    print(f"[around] {camp['name']}+{kw:<4} -> {len(pois)} 条")
                raw.extend(pois)
                time.sleep(0.1)

    print(f"\n原始合计 {len(raw)} 条")

    by_id = {}
    for p in raw:
        loc = parse_loc(p.get("location", ""))
        if not loc:
            continue
        lng, lat = loc
        name = (p.get("name") or "").strip()
        addr = (p.get("address") or "").strip()
        if not is_real_gate(name):
            continue
        full = name + " " + addr
        whu = any(h in full for h in WHU_HINTS)
        if not whu:
            near = any(dist_m(lng, lat, c["anchor"][0], c["anchor"][1]) < c["radius"]
                       for c in CAMPUSES)
            if not near:
                continue
        did = p.get("id") or f"{lng:.6f}_{lat:.6f}"
        if did in by_id:
            continue
        by_id[did] = {
            "amap_id": p.get("id", ""),
            "name": name,
            "lng": round(lng, 6),
            "lat": round(lat, 6),
            "type_code": p.get("typecode", ""),
            "type_name": p.get("type", ""),
            "address": addr,
        }

    records = sorted(by_id.values(), key=lambda r: (r["lat"], r["lng"]))
    out_path = os.path.join(PROJECT_ROOT, "data", "gates_amap_raw.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": datetime.now().isoformat(timespec="seconds"),
                   "count": len(records), "gates": records}, f, ensure_ascii=False, indent=2)

    print(f"\n===== 校门候选 {len(records)} 个 =====")
    for r in records:
        print(f"  {r['name']:<24} | {r['lng']:.6f}, {r['lat']:.6f} | {r['type_name']} | {r['address']}")
    print(f"\n写入 {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
