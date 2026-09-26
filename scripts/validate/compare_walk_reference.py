"""按同一批校内 OD 对比自有路网与高德步行路线。

只保存派生误差统计，不保存或再发布高德返回的路线坐标。高德仅作独立参照，
有分歧的路段仍须现场核实。需配置 AMAP_WEB_KEY 或 AMAP_KEY。
用法：python scripts/validate/compare_walk_reference.py [--output 路径]
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from numbers import Integral

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import networkx as nx  # noqa: E402
from pyproj import Transformer  # noqa: E402
from shapely.geometry import LineString, Point, shape  # noqa: E402
from shapely.ops import transform as shapely_transform  # noqa: E402
from shapely.strtree import STRtree  # noqa: E402
from shapely.wkt import loads as wkt_loads  # noqa: E402

from spatial.coord_transform import gcj02_to_wgs84, wgs84_to_gcj02  # noqa: E402
from spatial.amap_poi import lookup  # noqa: E402
from spatial.network import get_nearest_node, load_or_download_network  # noqa: E402
from spatial.poi import get_poi  # noqa: E402
from spatial.routing import compute_route, filter_graph_for_mode  # noqa: E402

AMAP_WALK_URL = "https://restapi.amap.com/v5/direction/walking"
TO_METERS = Transformer.from_crs(4326, 32650, always_xy=True).transform


def _navigation_anchor(poi, client):
    """Use the API's route anchor policy without persisting provider coordinates."""
    center = poi["coordinates"]
    match = lookup(poi.get("name", ""), known=poi, client=client)
    entrance = match.get("navigation_coordinates") if match else None
    gcj = entrance or center
    return gcj02_to_wgs84(gcj["lng"], gcj["lat"]), gcj, (
        "amap_entr_location" if entrance else "poi_center")


def _key():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    value = os.getenv("AMAP_WEB_KEY") or os.getenv("AMAP_KEY")
    if not value:
        raise RuntimeError("缺少 AMAP_WEB_KEY / AMAP_KEY")
    return value


def _own_polyline(G, selected_edges):
    coords = []
    for u, v, key in selected_edges:
        raw = G[u][v][key].get("geometry")
        if raw:
            geom = wkt_loads(raw) if isinstance(raw, str) else raw
            points = list(geom.coords)
        else:
            points = [
                (float(G.nodes[n]["x"]), float(G.nodes[n]["y"])) for n in (u, v)
            ]
        u_point = (float(G.nodes[u]["x"]), float(G.nodes[u]["y"]))
        if points and ((points[0][0] - u_point[0]) ** 2 +
                       (points[0][1] - u_point[1]) ** 2 >
                       (points[-1][0] - u_point[0]) ** 2 +
                       (points[-1][1] - u_point[1]) ** 2):
            points.reverse()
        if coords and points and coords[-1] == points[0]:
            points = points[1:]
        coords.extend(wgs84_to_gcj02(x, y) for x, y in points)
    return coords


def _reference_walk(client, key, start, end):
    params = {
        "key": key,
        "origin": f"{start[0]:.6f},{start[1]:.6f}",
        "destination": f"{end[0]:.6f},{end[1]:.6f}",
        "show_fields": "polyline",
        "output": "json",
    }
    response = client.get(AMAP_WALK_URL, params=params)
    response.raise_for_status()
    body = response.json()
    if str(body.get("status")) != "1":
        raise RuntimeError(f"高德步行接口失败: {body.get('info', 'unknown')}")
    paths = (body.get("route") or {}).get("paths") or []
    if not paths:
        raise RuntimeError("高德未返回步行路线")
    path = paths[0]
    points = []
    for step in path.get("steps") or []:
        for pair in (step.get("polyline") or "").split(";"):
            if not pair:
                continue
            lng, lat = map(float, pair.split(","))
            if not points or points[-1] != (lng, lat):
                points.append((lng, lat))
    if len(points) < 2:
        raise RuntimeError("高德未返回足够的步行折线点")
    return float(path["distance"]), points


def _project_pair(point, origin):
    lng, lat = point
    return ((lng - origin[0]) * 111320 * math.cos(math.radians(origin[1])),
            (lat - origin[1]) * 110540)


def _deviation_m(own_points, ref_points):
    origin = own_points[0]
    own = LineString([_project_pair(p, origin) for p in own_points])
    ref = LineString([_project_pair(p, origin) for p in ref_points])
    gaps = []
    for source, target in ((own, ref), (ref, own)):
        n = max(1, math.ceil(source.length / 10))
        gaps.extend(target.distance(Point(source.interpolate(i / n, normalized=True)))
                    for i in range(n + 1))
    gaps.sort()
    return {"p95_m": round(gaps[math.ceil(len(gaps) * 0.95) - 1], 1),
            "max_m": round(gaps[-1], 1)}


def _candidate_hazards(G, selected_edges, buildings_path):
    """Check dense source edges against OSM footprints; geometry is a flag, not a verdict."""
    if not buildings_path.is_file():
        return {"status": "building_layer_missing", "suspected_crossing_edge_ids": [],
                "step_tagged_edge_ids": []}
    try:
        document = json.loads(buildings_path.read_text(encoding="utf-8"))
        features = document.get("features", [])
        building_geometries = [shapely_transform(TO_METERS, shape(feature["geometry"]))
                               for feature in features if feature.get("geometry")]
        building_ids = [feature.get("id") for feature in features if feature.get("geometry")]
        tree = STRtree(building_geometries) if building_geometries else None
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return {"status": "building_layer_invalid", "suspected_crossing_edge_ids": [],
                "step_tagged_edge_ids": []}

    crossings, step_edges = [], []
    for u, v, key in selected_edges:
        data = G[u][v][key]
        highway = str(data.get("highway", ""))
        if "steps" in highway:
            step_edges.append([u, v, key])
        if tree is None:
            continue
        line = shapely_transform(TO_METERS, LineString([
            (float(G.nodes[u]["x"]), float(G.nodes[u]["y"])),
            (float(G.nodes[v]["x"]), float(G.nodes[v]["y"])),
        ]))
        for match in tree.query(line):
            index = int(match) if isinstance(match, Integral) else next(
                (i for i, geom in enumerate(building_geometries) if geom.equals(match)), -1
            )
            if index >= 0 and line.intersection(building_geometries[index]).length > 1.0:
                crossings.append({"edge_id": [u, v, key],
                                  "building_source_id": building_ids[index],
                                  "way_tags": {name: data.get(name) for name in
                                               ("highway", "covered", "bridge", "tunnel", "indoor")
                                               if data.get(name) is not None}})
    return {"status": "checked", "building_footprint_count": len(building_geometries),
            "suspected_crossing_edge_ids": crossings,
            "step_tagged_edge_ids": step_edges}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "scripts" / "audit_output" / "amap_comparison_metrics.json",
    )
    parser.add_argument("--limit", type=int, default=None,
                        help="仅处理前 N 个样本，便于核查接口可用性")
    parser.add_argument("--graph", type=Path, default=None,
                        help="可选 GraphML 路网；不指定时使用线上正式路网缓存")
    args = parser.parse_args()
    key = _key()
    G = (nx.read_graphml(args.graph, node_type=int) if args.graph
         else load_or_download_network())
    G_walk, _, _ = filter_graph_for_mode(G, "walk")
    samples_path = ROOT / "data" / "route_reference_samples.json"
    samples = json.loads(samples_path.read_text(encoding="utf-8"))["samples"]
    if args.limit is not None:
        samples = samples[:max(args.limit, 0)]
    issues_path = ROOT / "scripts" / "audit_output" / "audit_issues.json"
    issues = json.loads(issues_path.read_text(encoding="utf-8")) if issues_path.exists() else {}
    crossing_edges = {
        (item["u"], item["v"], item.get("k", 0))
        for item in issues.get("cross_building_edges", [])
    }
    near_steps_edges = {
        (item["u"], item["v"], item.get("k", 0))
        for item in issues.get("near_steps_edges", [])
    }
    candidate_source = G.graph.get("annotation_policy") == "source_scoped_only"
    buildings_path = ROOT / "scripts" / "audit_output" / "osm_buildings.geojson"
    results = []

    with httpx.Client(timeout=20) as client:
        for sample in samples:
            row = {"id": sample["id"], "start": sample["start"],
                   "end": sample["end"], "scenario": sample["scenario"]}
            try:
                start_poi = get_poi(sample["start"], fuzzy=False)
                end_poi = get_poi(sample["end"], fuzzy=False)
                if not start_poi or not end_poi:
                    raise ValueError("样本 POI 未入正式库")
                start_gcj = (start_poi["coordinates"]["lng"], start_poi["coordinates"]["lat"])
                end_gcj = (end_poi["coordinates"]["lng"], end_poi["coordinates"]["lat"])
                start_wgs, start_anchor, start_anchor_source = _navigation_anchor(
                    start_poi, client)
                end_wgs, end_anchor, end_anchor_source = _navigation_anchor(
                    end_poi, client)
                start_node = get_nearest_node(G_walk, *start_wgs)
                end_node = get_nearest_node(G_walk, *end_wgs)
                own_route = compute_route(
                    G, start_node, end_node,
                    weights={"distance": 0.90, "slope": 0.05, "scenery": 0.05},
                    mode="walk",
                )
                selected = own_route["recommended_edges"]
                candidate_hazards = (_candidate_hazards(G, selected, buildings_path)
                                     if candidate_source else None)
                if candidate_source and candidate_hazards:
                    crossing_ids = [row["edge_id"] for row in
                                    candidate_hazards["suspected_crossing_edge_ids"]]
                    step_ids = candidate_hazards["step_tagged_edge_ids"]
                else:
                    crossing_ids = [list(edge) for edge in selected if edge in crossing_edges]
                    step_ids = [list(edge) for edge in selected if edge in near_steps_edges]
                own_points = _own_polyline(G, selected)
                ref_distance, ref_points = _reference_walk(
                    client, key, start_gcj, end_gcj,
                )
                own_start = _project_pair(own_points[0], start_gcj)
                own_end = _project_pair(own_points[-1], end_gcj)
                own_start_anchor = _project_pair(own_points[0], (
                    start_anchor["lng"], start_anchor["lat"]))
                own_end_anchor = _project_pair(own_points[-1], (
                    end_anchor["lng"], end_anchor["lat"]))
                ref_start = _project_pair(ref_points[0], start_gcj)
                ref_end = _project_pair(ref_points[-1], end_gcj)
                row.update({
                    "status": "compared",
                    "own_distance_m": own_route["recommended_length_m"],
                    "reference_distance_m": round(ref_distance, 1),
                    "distance_difference_m": round(
                        own_route["recommended_length_m"] - ref_distance, 1),
                    "geometry_deviation": _deviation_m(own_points, ref_points),
                    "endpoint_offset_m": {
                        "own_start": round(math.hypot(*own_start), 1),
                        "own_end": round(math.hypot(*own_end), 1),
                        "reference_start": round(math.hypot(*ref_start), 1),
                        "reference_end": round(math.hypot(*ref_end), 1),
                    },
                    "navigation_anchor_source": {
                        "start": start_anchor_source, "end": end_anchor_source,
                    },
                    "navigation_snap_offset_m": {
                        "start": round(math.hypot(*own_start_anchor), 1),
                        "end": round(math.hypot(*own_end_anchor), 1),
                    },
                    "suspected_crossing_edge_ids": crossing_ids,
                    "near_steps_edge_ids": step_ids,
                    "hazard_assessment": (candidate_hazards if candidate_source else {
                        "status": "legacy_issue_ids_applied",
                        "interpretation": "历史风险边编号只适用于同一版正式路网。"}),
                    "recommended_edge_ids": selected,
                    "field_verification": sample.get("field_verification"),
                })
            except Exception as exc:
                reason = (f"provider_request_failed:{type(exc).__name__}"
                          if isinstance(exc, httpx.HTTPError) else str(exc))
                row.update({"status": "unavailable", "reason": reason})
            results.append(row)

    report = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "own_network": {
            "path": str(args.graph.relative_to(ROOT)) if args.graph and args.graph.is_relative_to(ROOT)
                   else str(args.graph) if args.graph else "production_cache",
            "source_sha256": G.graph.get("source_sha256"),
            "source": G.graph.get("source", "unknown"),
        },
        "reference": "高德 v5 步行路线，仅统计对照，不保存 API 折线",
        "interpretation": "几何差异是待核查线索，不代表任一来源已被现场证实。",
        "samples": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"对照指标: {args.output}；成功 {sum(x['status'] == 'compared' for x in results)}/{len(results)}")


if __name__ == "__main__":
    main()
