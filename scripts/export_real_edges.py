"""Export every edge of the current campus road network for manual annotation.

Outputs:
    data/edges_to_annotate.csv -- one row per graph edge
    output/edges_map.html      -- Folium map with tooltip/popup per edge
"""

import csv
import html
import os
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import folium

from spatial.network import get_node_coords, load_or_download_network

CSV_PATH = os.path.join(PROJECT_ROOT, "data", "edges_to_annotate.csv")
HTML_PATH = os.path.join(PROJECT_ROOT, "output", "edges_map.html")

FIELD_NAMES = [
    "edge_id",
    "u",
    "v",
    "name",
    "length_m",
    "highway",
    "slope_level",
    "scenery_level",
    "note",
]

_PAIR_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)")


def _normalize_name(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)


def _normalize_level(value):
    if value is None or value == "":
        return ""
    return value


def _haversine_m(lat1, lon1, lat2, lon2):
    import math

    radius = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _edge_length_m(G, u, v, data):
    length = data.get("length")
    try:
        return float(length)
    except (TypeError, ValueError):
        pass
    try:
        lon1, lat1 = get_node_coords(G, u)
        lon2, lat2 = get_node_coords(G, v)
        return _haversine_m(lat1, lon1, lat2, lon2)
    except Exception:
        return 0.0


def _collect_rows(G):
    rows = []
    for u, v, k, data in G.edges(keys=True, data=True):
        rows.append({
            "edge_id": f"{u},{v},{k}",
            "u": int(u),
            "v": int(v),
            "name": _normalize_name(data.get("name")),
            "length_m": round(_edge_length_m(G, u, v, data), 1),
            "highway": _normalize_name(data.get("highway")),
            "slope_level": _normalize_level(data.get("slope_level")),
            "scenery_level": _normalize_level(data.get("scenery_level")),
            "note": "",
        })
    return rows


def _wkt_coords(geometry):
    if not isinstance(geometry, str) or not geometry.startswith("LINESTRING"):
        return None
    coords = []
    for lon_text, lat_text in _PAIR_RE.findall(geometry):
        try:
            coords.append((float(lat_text), float(lon_text)))
        except ValueError:
            continue
    return coords if len(coords) >= 2 else None


def _edge_locations(G, u, v, data):
    coords = _wkt_coords(data.get("geometry"))
    if coords:
        return coords
    lon1, lat1 = get_node_coords(G, u)
    lon2, lat2 = get_node_coords(G, v)
    return [[lat1, lon1], [lat2, lon2]]


def _make_map(G, rows):
    try:
        from config import MAP_CENTER
        center = [MAP_CENTER["lat"], MAP_CENTER["lng"]]
    except ImportError:
        center = [30.5365, 114.3630]

    m = folium.Map(
        location=center,
        zoom_start=16,
        tiles="https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}",
        attr="© 高德地图",
        subdomains="1234",
    )

    bounds = []
    for idx, row in enumerate(rows):
        u, v = row["u"], row["v"]
        locations = _edge_locations(G, u, v, G[u][v][0] if False else None)
        # _edge_locations needs the edge data dict; look it up explicitly.
        edge_data = G[u][v][0]
        locations = _edge_locations(G, u, v, edge_data)
        bounds.extend(locations)

        name = row["name"] or "未命名"
        tooltip = f"#{idx} | {name} | {row['length_m']}m"
        popup = html.escape(f"#{idx}<br>路名: {name}<br>长度: {row['length_m']}m")
        folium.PolyLine(
            locations=locations,
            color="#1f77b4",
            weight=3,
            opacity=0.8,
            tooltip=tooltip,
            popup=popup,
        ).add_to(m)

    if bounds:
        lats = [point[0] for point in bounds]
        lons = [point[1] for point in bounds]
        m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])

    return m


def _export_csv(rows, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELD_NAMES)
        writer.writeheader()
        writer.writerows(rows)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("加载当前校园路网 GraphML ...")
    G = load_or_download_network()
    print(f"节点: {G.number_of_nodes()}, 边: {G.number_of_edges()}")

    rows = _collect_rows(G)
    if len(rows) != G.number_of_edges():
        raise RuntimeError(
            f"边数不一致: 收集 {len(rows)}, 图中 {G.number_of_edges()}"
        )

    _export_csv(rows, CSV_PATH)
    print(f"CSV 已写出: {CSV_PATH} (数据行 {len(rows)})")

    os.makedirs(os.path.dirname(HTML_PATH), exist_ok=True)
    m = _make_map(G, rows)
    m.save(HTML_PATH)
    print(f"HTML 已写出: {HTML_PATH}")

    html_size = os.path.getsize(HTML_PATH)
    print(f"HTML 大小: {html_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
