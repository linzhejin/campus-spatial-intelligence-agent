"""
OSMnx 路网加载与缓存模块

负责下载/加载武大校园路网（步行道/小路），支持 GraphML 缓存。
首次启动时从 OSM 下载，后续直接加载缓存文件（< 1 秒）。
"""

import json
import logging
import os
from typing import Optional, Tuple

import networkx as nx

logger = logging.getLogger(__name__)

_G: Optional[nx.MultiDiGraph] = None
_annotation_coverage_rate: Optional[float] = None


def _cache_path() -> str:
    try:
        from config import ROAD_NETWORK_CACHE
        return ROAD_NETWORK_CACHE
    except ImportError:
        return os.path.join(os.path.dirname(__file__), "..", "data", "whu_road_network.graphml")


def _bbox() -> dict:
    try:
        from config import WHU_BBOX
        return WHU_BBOX
    except ImportError:
        return {"north": 30.5480, "south": 30.5280, "east": 114.3750, "west": 114.3500}


def get_network() -> Optional[nx.MultiDiGraph]:
    """
    获取已加载的路网图。

    Returns:
        networkx MultiDiGraph 或 None（未加载）
    """
    return _G


def load_or_download_network(bbox: Optional[dict] = None) -> nx.MultiDiGraph:
    """
    加载缓存路网或从 OSM 下载。

    优先加载 GraphML 缓存；缓存不存在时下载并保存。

    Args:
        bbox: 边界框 dict(north, south, east, west)，默认从 config 读取

    Returns:
        networkx MultiDiGraph（有向图，含步行道/小路）

    Raises:
        RuntimeError: 下载失败且无缓存时抛出
    """
    global _G

    if _G is not None:
        return _G

    global _annotation_coverage_rate

    cache = _cache_path()

    if os.path.exists(cache):
        logger.info("从缓存加载路网: %s", cache)
        _G = _load_graphml(cache)
    else:
        logger.info("缓存不存在，从 OSM 下载路网...")
        bbox = bbox or _bbox()
        _G = _download_network(bbox)

        try:
            _save_graphml(_G, cache)
            logger.info("路网已缓存: %s", cache)
        except IOError as e:
            logger.warning("路网缓存失败（不影响运行）: %s", e)

    ann_path = _annotations_path()
    if os.path.exists(ann_path):
        try:
            _annotation_coverage_rate = _merge_annotations(_G, ann_path)
            logger.info(
                "路段标注 merge 完成: coverage_rate=%.3f (%s)",
                _annotation_coverage_rate, ann_path
            )
        except Exception as e:
            logger.warning("路段标注 merge 失败（不影响运行）: %s", e)
            _annotation_coverage_rate = 0.0
    else:
        _annotation_coverage_rate = 0.0
        logger.info("未找到路段标注文件: %s (覆盖率=0.0)", ann_path)

    return _G


def _load_graphml(path: str) -> nx.MultiDiGraph:
    try:
        G = nx.read_graphml(path)
        logger.info("路网加载成功: %d 节点, %d 边", G.number_of_nodes(), G.number_of_edges())
        return G
    except Exception as e:
        raise RuntimeError(f"路网缓存加载失败: {e}")


def _save_graphml(G: nx.MultiDiGraph, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 清洗非原始类型属性值（osmnx 2.x 可能产生 list/dict 属性，GraphML 不支持）
    G_clean = G.copy()
    for _, _, data in G_clean.edges(data=True):
        for key in list(data.keys()):
            val = data[key]
            if isinstance(val, (list, dict, set, tuple)):
                data[key] = str(val)
    for _, data in G_clean.nodes(data=True):
        for key in list(data.keys()):
            val = data[key]
            if isinstance(val, (list, dict, set, tuple)):
                data[key] = str(val)
    nx.write_graphml(G_clean, path)


def _download_network(bbox: dict) -> nx.MultiDiGraph:
    try:
        import osmnx as ox
    except ImportError:
        raise RuntimeError("osmnx 未安装，无法下载路网。请执行: pip install osmnx")

    north, south = bbox["north"], bbox["south"]
    east, west = bbox["east"], bbox["west"]

    try:
        # osmnx >= 2.0: bbox 元组参数 (west, south, east, north)
        G = ox.graph_from_bbox(
            bbox=(west, south, east, north),
            network_type="walk",
            simplify=True,
            retain_all=False,
            truncate_by_edge=True,
        )
    except TypeError:
        # osmnx < 2.0: 独立关键字参数 (fallback)
        G = ox.graph_from_bbox(
            north=north,
            south=south,
            east=east,
            west=west,
            network_type="walk",
            simplify=True,
            retain_all=False,
            truncate_by_edge=True,
        )
    except Exception as e:
        raise RuntimeError(f"OSM 路网下载失败: {e}")

    if G.number_of_nodes() == 0:
        raise RuntimeError("OSM 路网为空，请检查 bbox 范围或网络连接")

    logger.info("路网下载成功: %d 节点, %d 边", G.number_of_nodes(), G.number_of_edges())
    return G


def reload_network(bbox: Optional[dict] = None) -> nx.MultiDiGraph:
    """
    强制重新下载路网（忽略缓存）。

    Args:
        bbox: 边界框 dict

    Returns:
        重新下载的 MultiDiGraph
    """
    global _G
    _G = None

    cache = _cache_path()
    if os.path.exists(cache):
        os.remove(cache)
        logger.info("已删除旧缓存: %s", cache)

    return load_or_download_network(bbox)


def clear_cache() -> None:
    """删除路网缓存文件。"""
    cache = _cache_path()
    if os.path.exists(cache):
        os.remove(cache)
        logger.info("路网缓存已清除: %s", cache)


def get_nearest_node(G: nx.MultiDiGraph, lng: float, lat: float) -> int:
    try:
        import osmnx as ox
    except ImportError:
        raise RuntimeError("osmnx 未安装，无法查找最近节点。请执行: pip install osmnx")

    node_id = ox.nearest_nodes(G, lng, lat)
    return int(node_id)


def get_node_coords(G: nx.MultiDiGraph, node_id: int) -> tuple:
    node_data = G.nodes[node_id]
    lng = float(node_data.get("x", 0))
    lat = float(node_data.get("y", 0))
    return (lng, lat)


def _annotations_path() -> str:
    try:
        from config import ROAD_ANNOTATIONS_PATH
        return ROAD_ANNOTATIONS_PATH
    except ImportError:
        return os.path.join(os.path.dirname(__file__), "..", "data", "road_annotations.json")


def _parse_edge_id(edge_id_raw) -> Optional[Tuple]:
    if edge_id_raw is None:
        return None
    if isinstance(edge_id_raw, list) or isinstance(edge_id_raw, tuple):
        if len(edge_id_raw) >= 3:
            return (str(edge_id_raw[0]), str(edge_id_raw[1]), int(edge_id_raw[2]))
        elif len(edge_id_raw) == 2:
            return (str(edge_id_raw[0]), str(edge_id_raw[1]), 0)
    if isinstance(edge_id_raw, str):
        try:
            parts = edge_id_raw.strip("[]()").split(",")
            parts = [p.strip() for p in parts]
            if len(parts) >= 3:
                return (parts[0], parts[1], int(parts[2]))
            elif len(parts) == 2:
                return (parts[0], parts[1], 0)
        except (ValueError, IndexError):
            pass
    return None


def _merge_annotations(G: nx.MultiDiGraph, annotations_path: str) -> float:
    with open(annotations_path, "r", encoding="utf-8") as f:
        ann_data = json.load(f)

    edges_ann = ann_data.get("edges", []) or []
    total_edges = G.number_of_edges()

    if total_edges == 0:
        return 0.0

    graph_keys = set()
    graph_uv_to_keys = {}
    key_to_orig = {}  # (str_u, str_v, int_k) → (orig_u, orig_v, orig_k)  O(1) 查找
    for u, v, k in G.edges(keys=True):
        u_s, v_s = str(u), str(v)
        k_int = int(k)
        graph_keys.add((u_s, v_s, k_int))
        graph_uv_to_keys.setdefault((u_s, v_s), []).append(k_int)
        key_to_orig[(u_s, v_s, k_int)] = (u, v, k)

    annotated_set = set()

    for ann in edges_ann:
        edge_id = _parse_edge_id(ann.get("edge_id"))
        u_raw = ann.get("u")
        v_raw = ann.get("v")

        target_key = None
        if edge_id is not None:
            if edge_id in graph_keys:
                target_key = edge_id
        if target_key is None and u_raw is not None and v_raw is not None:
            u_s, v_s = str(u_raw), str(v_raw)
            candidates = graph_uv_to_keys.get((u_s, v_s), [])
            if candidates:
                target_key = (u_s, v_s, candidates[0])

        if target_key is None:
            continue

        orig = key_to_orig.get(target_key)
        if orig is None:
            continue
        orig_u, orig_v, orig_k = orig

        edge_data = G[orig_u][orig_v][orig_k]
        if "slope_level" in ann and ann["slope_level"] is not None:
            try:
                edge_data["slope_level"] = int(ann["slope_level"])
            except (ValueError, TypeError):
                pass
        if "scenery_level" in ann and ann["scenery_level"] is not None:
            try:
                edge_data["scenery_level"] = int(ann["scenery_level"])
            except (ValueError, TypeError):
                pass
        if "name" in ann and ann["name"]:
            edge_data["name"] = str(ann["name"])

        annotated_set.add((u_t, v_t, k_t))

    return len(annotated_set) / total_edges if total_edges > 0 else 0.0


def get_annotation_coverage_rate() -> float:
    global _annotation_coverage_rate
    if _annotation_coverage_rate is None:
        return 0.0
    return float(_annotation_coverage_rate)