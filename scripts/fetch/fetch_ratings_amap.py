# -*- coding: utf-8 -*-
"""从高德拉取大众评分（biz_ext.rating）写入 pois.json。

匹配策略：对每个 POI 用 place/around（自身坐标 150m 内 + 名称关键词）搜索，
名称/别名与返回结果做归一化相似度匹配，取最优者的高德评分。
无匹配或无评分 -> rating=null。
"""
import json
import re
import time
import httpx
from dotenv import dotenv_values

ENV = dotenv_values(".env")
KEY = ENV.get("AMAP_WEB_KEY", "")
AROUND = "https://restapi.amap.com/v3/place/around"

NOISE_RE = re.compile(r"[\s()（）\-—·、，,]")


def norm(s: str) -> str:
    return NOISE_RE.sub("", (s or "").lower())


def name_sim(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # 去掉"武汉大学/武大"前缀后比较
    for pre in ("武汉大学", "武大"):
        if a.startswith(pre):
            a = a[len(pre):]
        if b.startswith(pre):
            b = b[len(pre):]
    if a and (a == b or a in b or b in a):
        return 0.95
    # 简单重合率
    common = len(set(a) & set(b))
    return common / max(len(set(a) | set(b)), 1)


def extract_rating(poi: dict):
    biz = poi.get("biz_ext") or {}
    r = biz.get("rating")
    if isinstance(r, str) and r:
        try:
            return round(float(r), 1)
        except ValueError:
            return None
    return None


def best_match(our: dict, lng: float, lat: float, c: httpx.Client):
    """用名称+别名依次搜索，返回 (rating, matched_name)。"""
    queries = [our["name"]] + [a for a in our.get("aliases", []) if len(a) >= 2][:3]
    seen_kw = set()
    for kw in queries:
        kw_clean = kw.strip()
        if not kw_clean or kw_clean in seen_kw:
            continue
        seen_kw.add(kw_clean)
        r = c.get(AROUND, params={
            "key": KEY, "location": f"{lng},{lat}", "radius": 150,
            "keywords": kw_clean, "offset": 8, "page": 1, "extensions": "all",
        })
        d = r.json()
        if d.get("status") != "1":
            time.sleep(0.3)
            continue
        best = None  # (sim, rating, name)
        for p in d.get("pois", []):
            sim = name_sim(our["name"], p.get("name", ""))
            for a in our.get("aliases", []):
                sim = max(sim, name_sim(a, p.get("name", "")))
            if sim < 0.6:
                continue
            rating = extract_rating(p)
            cand = (sim, rating or 0.0, p.get("name", ""))
            if best is None or cand[0] > best[0]:
                best = cand
        if best and best[0] >= 0.85:
            return (best[1] or None), best[2]
        # 相似度一般但确有匹配且带评分 -> 也接受
        if best and best[1] > 0:
            return best[1], best[2]
    return None, None


def main():
    with open("data/pois.json", encoding="utf-8") as f:
        data = json.load(f)
    pois = data["pois"]

    c = httpx.Client(timeout=20)
    rated = 0
    unmatched = []
    for i, p in enumerate(pois, 1):
        coords = p.get("coordinates", {})
        lng, lat = coords.get("lng"), coords.get("lat")
        if not lng or not lat:
            unmatched.append((p["id"], p["name"], "无坐标"))
            continue
        rating, matched = best_match(p, lng, lat, c)
        p["rating"] = rating
        if rating is not None:
            rated += 1
        else:
            unmatched.append((p["id"], p["name"], ""))
        if i % 50 == 0:
            print(f"  ... {i}/{len(pois)} 已评分 {rated}")
        time.sleep(0.12)

    data["count"] = len(pois)
    with open("data/pois.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    from collections import Counter
    by_type = Counter(p["type"] for p in pois if p.get("rating"))
    print(f"\n完成: {rated}/{len(pois)} 个 POI 拿到高德评分")
    print(f"评分覆盖按类型: {dict(by_type)}")
    print(f"\n无评分 {len(unmatched)} 个（不作途经点）:")
    for pid, name, why in unmatched[:30]:
        print(f"  {pid} {name} {why}")
    if len(unmatched) > 30:
        print(f"  ...等共 {len(unmatched)} 个")


if __name__ == "__main__":
    main()
