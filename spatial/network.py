"""
OSMnx 路网加载与缓存模块

负责下载/加载武大校园路网（步行道/小路），支持 GraphML 缓存。
首次启动时从 OSM 下载，后续直接加载缓存文件（< 1 秒）。
"""

import logging
import os
from typing import Optional

import networkx as nx

logger = logging.getLogger(__name__)

_G: Optional[nx.MultiDiGraph] = None


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

    cache = _cache_path()

    if os.path.exists(cache):
        logger.info("从缓存加载路网: %s", cache)
        _G = _load_graphml(cache)
        return _G

    logger.info("缓存不存在，从 OSM 下载路网...")
    bbox = bbox or _bbox()
    _G = _download_network(bbox)

    try:
        _save_graphml(_G, cache)
        logger.info("路网已缓存: %s", cache)
    except IOError as e:
        logger.warning("路网缓存失败（不影响运行）: %s", e)

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
    nx.write_graphml(G, path)


def _download_network(bbox: dict) -> nx.MultiDiGraph:
    try:
        import osmnx as ox
    except ImportError:
        raise RuntimeError("osmnx 未安装，无法下载路网。请执行: pip install osmnx")

    north, south = bbox["north"], bbox["south"]
    east, west = bbox["east"], bbox["west"]

    try:
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

    G = ox.add_edge_speeds(G)
    G = ox.add_edge_travel_times(G)

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