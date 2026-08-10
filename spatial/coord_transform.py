"""
GCJ-02 ⇄ WGS-84 坐标系转换模块

实现标准加密坐标转换算法（非近似）。
GCJ-02（火星坐标系）由中国国家测绘局制定，高德/腾讯地图使用此坐标系。
WGS-84 为国际标准，OSM/Google Earth 使用此坐标系。

精度：单次转换误差 ≤ 0.5 米（经纬度 ≤ 1e-6 度）。
"""

import math

_A = 6378245.0
_EE = 0.006693421622965823

_OUT_OF_CHINA_LNG = [73.66, 135.05]
_OUT_OF_CHINA_LAT = [3.86, 53.55]


def _out_of_china(lng: float, lat: float) -> bool:
    return not (_OUT_OF_CHINA_LNG[0] <= lng <= _OUT_OF_CHINA_LNG[1]
                and _OUT_OF_CHINA_LAT[0] <= lat <= _OUT_OF_CHINA_LAT[1])


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lng: float, lat: float) -> tuple:
    """
    WGS-84 坐标 → GCJ-02 坐标（标准加密算法）

    Args:
        lng: WGS-84 经度
        lat: WGS-84 纬度

    Returns:
        (gcj_lng, gcj_lat) 元组
    """
    if _out_of_china(lng, lat):
        return lng, lat

    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - _EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * math.pi)
    dlng = (dlng * 180.0) / (_A / sqrtmagic * math.cos(radlat) * math.pi)
    return lng + dlng, lat + dlat


def gcj02_to_wgs84(lng: float, lat: float) -> tuple:
    """
    GCJ-02 坐标 → WGS-84 坐标（迭代反算法，精度 ≤ 1e-7 度）

    原理：GCJ-02 是 WGS-84 的加密变换，通过迭代求解反函数。
    初始估计取 GCJ-02 值，计算其 GCJ-02 映射，用差值修正，
    通常 2-3 次迭代即可收敛到 1e-7 度精度。

    Args:
        lng: GCJ-02 经度
        lat: GCJ-02 纬度

    Returns:
        (wgs84_lng, wgs84_lat) 元组
    """
    if _out_of_china(lng, lat):
        return lng, lat

    clng, clat = lng, lat
    for _ in range(3):
        wlng, wlat = clng, clat
        glng, glat = wgs84_to_gcj02(wlng, wlat)
        clng += (lng - glng)
        clat += (lat - glat)

    return clng, clat


def batch_wgs84_to_gcj02(points: list) -> list:
    """
    批量 WGS-84 → GCJ-02

    Args:
        points: [(lng, lat), ...] 列表

    Returns:
        [(gcj_lng, gcj_lat), ...] 列表
    """
    return [wgs84_to_gcj02(lng, lat) for lng, lat in points]


def batch_gcj02_to_wgs84(points: list) -> list:
    """
    批量 GCJ-02 → WGS-84

    Args:
        points: [(lng, lat), ...] 列表

    Returns:
        [(wgs84_lng, wgs84_lat), ...] 列表
    """
    return [gcj02_to_wgs84(lng, lat) for lng, lat in points]