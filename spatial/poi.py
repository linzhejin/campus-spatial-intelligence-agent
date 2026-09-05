"""
POI 加载与匹配模块

从 data/pois.json 加载 POI 数据，支持模糊搜索和按类型过滤。
当 JSON 文件不可用时，回退到 config.WHU_POIS。
"""

import json
import os
import re
from difflib import SequenceMatcher
from typing import Optional

_POIS_CACHE: list = []
_LOADED = False


def _pois_file_path() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "data", "pois.json")


def _load_from_json() -> list:
    path = _pois_file_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("pois", [])
    except (json.JSONDecodeError, IOError):
        return []


def _load_from_config() -> list:
    try:
        from config import WHU_POIS
    except ImportError:
        return []

    pois = []
    for poi_id, info in WHU_POIS.items():
        pois.append({
            "id": poi_id,
            "name": info.get("name", poi_id),
            "aliases": [poi_id],
            "coordinates": {"lng": info["lon"], "lat": info["lat"]},
            "type": info.get("type", "landmark"),
            "description": info.get("desc", ""),
            "scenery_score": info.get("scenery_score", 3),
        })
    return pois


def load_pois() -> list:
    """
    加载 POI 数据（带缓存，仅首次调用读文件）。

    优先从 data/pois.json 加载，失败则回退到 config.WHU_POIS。

    Returns:
        POI 对象列表，每个 POI 包含 id, name, aliases, coordinates, type, description 等字段
    """
    global _POIS_CACHE, _LOADED
    if _LOADED:
        return _POIS_CACHE

    pois = _load_from_json()
    if not pois:
        pois = _load_from_config()
    _POIS_CACHE = pois
    _LOADED = True
    return pois


def reload_pois() -> list:
    """强制重新加载 POI 数据（忽略缓存）。"""
    global _LOADED
    _LOADED = False
    return load_pois()


def list_pois() -> list:
    """
    返回所有 POI 列表。

    Returns:
        POI 对象列表
    """
    return load_pois()


def get_pois_by_type(poi_type: str) -> list:
    """
    按类型过滤 POI。

    Args:
        poi_type: POI 类型，如 "scenery", "study", "landmark"

    Returns:
        匹配该类型的 POI 列表
    """
    pois = load_pois()
    return [p for p in pois if p.get("type") == poi_type]


_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_NUM_RUN_RE = re.compile(r"[零一二两三四五六七八九十]+")


def _cn_run_to_int(tok: str):
    """中文数字串转整数：'七'->7、'十四'->14、'二十三'->23、'十'->10。"""
    if "十" in tok:
        left, _, right = tok.partition("十")
        tens = _CN_DIGITS[left] if left in _CN_DIGITS else 1
        ones = _CN_DIGITS[right] if right in _CN_DIGITS else 0
        return tens * 10 + ones
    if len(tok) == 1:
        return _CN_DIGITS.get(tok)
    return None


def _normalize_num(s: str) -> str:
    """中文数字→阿拉伯数字归一化（组合数词正确处理），让「十四舍」和「14舍」能匹配上。"""
    def repl(m):
        v = _cn_run_to_int(m.group(0))
        return str(v) if v is not None else m.group(0)
    return _CN_NUM_RUN_RE.sub(repl, s)

# 宽泛区域词：这些词是学部/园区名，不应作为子串匹配到具体教学楼/食堂
# 例如"信息学部"不应匹配到"信息学部第一教学楼"
_BROAD_AREA_TERMS = {
    "工学部", "文理学部", "信息学部", "医学部",
    "湖滨", "枫园", "梅园", "桂园", "樱园", "星湖",
    "国际园区", "西区", "东区", "南区", "北区",
}


def _is_broad_area_term(name: str) -> bool:
    """判断输入是否是宽泛区域词（学部名/园区名）。"""
    return name.strip() in _BROAD_AREA_TERMS


# 外校/外部单位标志词：用户查询指向校外单位时，不应匹配任何武大POI
_EXTERNAL_TERMS_RE = re.compile(
    r"华中师范|华师|师范大学|理工大学|理工大|华中科技|华科|"
    r"武汉体育学院|体育学院|武体|"
    r"职业技术|电力职|中国科学院|中科院|卓刀泉中学|附属中学|附属小学"
)


def _is_external_query(name: str) -> bool:
    """查询是否指向校外单位（含外校标志且不是在问武大）。"""
    if _EXTERNAL_TERMS_RE.search(name):
        return "武汉大学" not in name and "武大" not in name
    return False


def _digits_aligned(short: str, long: str) -> bool:
    """short 是 long 的子串时，检查数字边界：
    若 short 首/尾为数字，long 中对应位置的前/后字符不能也是数字
    （防止"1教"误匹配"11教学楼"、"7舍"误匹配"17舍"）。"""
    idx = long.find(short)
    if idx < 0:
        return False
    if short[0].isdigit() and idx > 0 and long[idx - 1].isdigit():
        return False
    if short[-1].isdigit() and idx + len(short) < len(long) and long[idx + len(short)].isdigit():
        return False
    return True


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    a_lower = a.lower().strip()
    b_lower = b.lower().strip()
    if a_lower == b_lower:
        return 1.0
    # 宽泛词保护：输入是学部/园区名时，不允许通过子串匹配到更长的POI名
    # 例如"信息学部"不应子串匹配到"信息学部第一教学楼"
    if _is_broad_area_term(a_lower) and len(b_lower) > len(a_lower):
        return 0.0
    if b_lower in a_lower and _digits_aligned(b_lower, a_lower):
        return 0.9
    if a_lower in b_lower and _digits_aligned(a_lower, b_lower):
        return 0.85
    # 数字归一化后再比一次（中文数字 vs 阿拉伯数字）
    a_n = _normalize_num(a_lower)
    b_n = _normalize_num(b_lower)
    if a_n != a_lower or b_n != b_lower:
        if a_n == b_n:
            return 1.0
        if b_n in a_n and _digits_aligned(b_n, a_n):
            return 0.9
        if a_n in b_n and _digits_aligned(a_n, b_n):
            return 0.85
        r = SequenceMatcher(None, a_n, b_n).ratio()
        if r > 0:
            return r
    return SequenceMatcher(None, a_lower, b_lower).ratio()


def find_poi(name: str, min_score: float = 0.6) -> Optional[dict]:
    """
    根据名称模糊匹配单个 POI。

    依次匹配：精确名称 > 别名精确 > 模糊匹配（名称+别名）。
    返回相似度最高且超过阈值的 POI。

    Args:
        name: 用户输入的 POI 名称
        min_score: 最低相似度阈值（0-1），默认 0.6

    Returns:
        匹配到的 POI 对象，未匹配返回 None
    """
    pois = load_pois()
    if _is_external_query(name) or not name.strip():
        return None
    # 过短输入（≤2字）提高阈值：精确名/别名命中为1.0不受影响，模糊近似（如"扬波门"误中"凌波门"）被拦
    threshold = max(min_score, 0.75) if len(name.strip()) <= 2 else min_score
    best_poi = None
    best_score = 0.0

    for poi in pois:
        name_score = _similarity(name, poi["name"])
        if name_score > best_score:
            best_score = name_score
            best_poi = poi

        for alias in poi.get("aliases", []):
            alias_score = _similarity(name, alias)
            if alias_score > best_score:
                best_score = alias_score
                best_poi = poi

    if best_score >= threshold:
        return best_poi
    return None


def find_poi_candidates(name: str, limit: int = 5, min_score: float = 0.3) -> list:
    """
    根据名称模糊匹配多个候选 POI（用于消歧）。

    Args:
        name: 用户输入的 POI 名称
        limit: 最多返回候选数
        min_score: 最低相似度阈值

    Returns:
        [(poi, score), ...] 按相似度降序排列
    """
    pois = load_pois()
    if _is_external_query(name) or not name.strip():
        return []
    scored = []

    for poi in pois:
        score = max(
            _similarity(name, poi["name"]),
            max((_similarity(name, a) for a in poi.get("aliases", [])), default=0.0),
        )
        if score >= min_score:
            scored.append((poi, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]


def _flatten_poi(poi: dict) -> dict:
    coords = poi.get("coordinates", {})
    return {
        "id": poi.get("id", ""),
        "name": poi.get("name", ""),
        "type": poi.get("type", "landmark"),
        "campus": poi.get("campus", ""),
        "lat": coords.get("lat", 0),
        "lon": coords.get("lng", 0),
        "coordinates": {"lat": coords.get("lat", 0), "lng": coords.get("lng", 0)},
        "description": poi.get("description", ""),
        "aliases": poi.get("aliases", []),
        "season_tags": poi.get("season_tags", []),
        "scenery_score": poi.get("scenery_score", 3),
    }


def get_poi(name: str, fuzzy: bool = True) -> Optional[dict]:
    if fuzzy:
        poi = find_poi(name)
    else:
        pois = load_pois()
        poi = None
        for p in pois:
            if p.get("name", "") == name or name in p.get("aliases", []):
                poi = p
                break

    if poi is None:
        return None
    return _flatten_poi(poi)


def find_poi_ambiguous(name: str, min_score: float = 0.6):
    """歧义感知的 POI 匹配（对话入口用）。

    返回 (poi_or_None, alternatives)：
      - 精确/唯一最高命中 → (poi, [])
      - 多个不同 POI 模糊同分（如「三教」在文理学部/工学部/信息学部各有一个）
        → (None, [poi, ...])，供对话层请用户消歧，避免静默错配
      - 无命中 / 校外查询 → (None, [])
    返回的 POI 均为 _flatten_poi 后的对外结构。
    """
    if _is_external_query(name) or not name.strip():
        return None, []
    threshold = max(min_score, 0.75) if len(name.strip()) <= 2 else min_score
    scored = find_poi_candidates(name, limit=20, min_score=min(0.3, threshold))
    if not scored:
        return None, []
    top_score = scored[0][1]
    if top_score < threshold:
        return None, []
    if top_score >= 0.99:
        return _flatten_poi(scored[0][0]), []
    ties = [p for p, s in scored if s >= top_score - 0.005]
    if len(ties) > 1:
        return None, [_flatten_poi(p) for p in ties]
    return _flatten_poi(scored[0][0]), []


def search_pois(keyword: str, poi_type: str = None, season: str = None) -> list:
    candidates = find_poi_candidates(keyword, limit=50, min_score=0.3)
    results = []
    for poi, score in candidates:
        flat = _flatten_poi(poi)
        if poi_type and flat["type"] != poi_type:
            continue
        if season and season not in flat.get("season_tags", []):
            continue
        results.append(flat)
    return results


def list_all_pois(poi_type: str = None, season: str = None) -> list:
    pois = load_pois()
    results = []
    for poi in pois:
        flat = _flatten_poi(poi)
        if poi_type and flat["type"] != poi_type:
            continue
        if season and season not in flat.get("season_tags", []):
            continue
        results.append(flat)
    return results