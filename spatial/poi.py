"""
POI 加载与匹配模块

从 data/pois.json 加载 POI 数据，支持模糊搜索和按类型过滤。
当 JSON 文件不可用时，回退到 config.WHU_POIS。
"""

import json
import os
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


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    a_lower = a.lower().strip()
    b_lower = b.lower().strip()
    if a_lower == b_lower:
        return 1.0
    if b_lower in a_lower:
        return 0.9
    if a_lower in b_lower:
        return 0.85
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

    if best_score >= min_score:
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
        "lat": coords.get("lat", 0),
        "lon": coords.get("lng", 0),
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


def search_pois(keyword: str, poi_type: str = None, season: str = None) -> list:
    candidates = find_poi_candidates(keyword, limit=50, min_score=0.3)
    results = []
    for poi, score in candidates:
        flat = _flatten_poi(poi)
        if poi_type and flat["type"] != poi_type:
            continue
        if season and season not in flat.get("season_tags", []):
            continue
        flat["_match_score"] = round(score, 3)
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