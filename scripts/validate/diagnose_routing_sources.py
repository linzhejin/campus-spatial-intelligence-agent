"""Read-only routing source and benchmark audit; writes a derived JSON report only.

Run with Python containing networkx, shapely, pyproj, and (optionally) osmium:
    python scripts/validate/diagnose_routing_sources.py --pairs 120

Reachability uses directed graph traversal, not 720 repeated route computations.
The six named reference OD pairs also exercise the actual route planner.
Commercial geometry is neither requested nor saved by this diagnostic.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import networkx as nx
from pyproj import Geod, Transformer
from shapely.geometry import LineString, Point
from shapely.ops import transform
from shapely.wkt import loads as wkt_loads

from spatial.coord_transform import gcj02_to_wgs84
from spatial.network import _merge_annotations, get_nearest_node, load_or_download_network
from spatial.poi import get_poi
from spatial import routing

GEOD = Geod(ellps="WGS84")
PROJECT = Transformer.from_crs(4326, 32650, always_xy=True).transform
TAGS = ("highway", "foot", "bicycle", "motor_vehicle", "motorcar", "vehicle",
        "access", "barrier", "indoor", "level", "covered", "tunnel", "bridge",
        "oneway", "oneway:bicycle", "oneway:foot", "service", "surface", "incline")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def ids(raw):
    if isinstance(raw, str) and raw.startswith("["):
        raw = ast.literal_eval(raw)
    return [int(x) for x in raw] if isinstance(raw, (list, tuple)) else [int(raw)]


def node_xy(graph, node):
    d = graph.nodes[node]
    return float(d["x"]), float(d["y"])


def dist(a, b):
    return abs(GEOD.inv(*a, *b)[2])


def geometry(graph, u, v, data):
    raw = data.get("geometry")
    return (wkt_loads(raw) if isinstance(raw, str) else raw) if raw else LineString(
        [node_xy(graph, u), node_xy(graph, v)])


def percentile(values, p=0.95):
    if not values:
        return None
    return round(sorted(values)[max(0, __import__("math").ceil(len(values) * p) - 1)], 2)


def graph_summary(graph):
    components = sorted((len(c) for c in nx.weakly_connected_components(graph)), reverse=True)
    return {"nodes": len(graph), "edges": graph.number_of_edges(),
            "weak_components": len(components), "largest_component_nodes": components[:8]}


def reachable(graph, start, end):
    if start not in graph or end not in graph:
        return "endpoint_removed"
    return "reachable" if nx.has_path(graph, start, end) else "disconnected"


def shortest_details(graph, start, end):
    state = reachable(graph, start, end)
    if state != "reachable":
        return {"status": state}
    path = nx.shortest_path(graph, start, end, weight="length")
    edges = [(u, v, min(graph[u][v], key=lambda k: float(graph[u][v][k]["length"])))
             for u, v in zip(path, path[1:])]
    return {"status": state, "length_m": round(sum(float(graph[u][v][k]["length"])
            for u, v, k in edges), 2), "edges": edges}


def matrix_audit(base, fixed, modes, count):
    pois = read_json(ROOT / "data/pois.json")["pois"]
    points = [(p["name"], gcj02_to_wgs84(float(p["coordinates"]["lng"]),
               float(p["coordinates"]["lat"]))) for p in pois]
    all_snap = [get_nearest_node(fixed, *p) for _, p in points]
    mode_snaps = {label: {m: [get_nearest_node(g, *p) for _, p in points]
                          for m, g in mode_graphs.items()}
                  for label, mode_graphs in modes.items()}
    rng = random.Random(20260916)
    pairs = [rng.sample(range(len(points)), 2) for _ in range(count)]
    report = {"seed": 20260916, "poi_count": len(points), "pair_count": count,
              "method": "directed nx.has_path on production mode filters, no weather or closures",
              "endpoint_note": "Mode snapping avoids removed endpoints but may snap far away or to a small component; reachable does not prove a legal last-mile connection.",
              "modes": {}}
    for mode in ("walk", "bike", "drive"):
        row = {"graphs": {label: graph_summary(gs[mode]) for label, gs in modes.items()},
               "legacy_all_graph_snap": {}, "production_mode_snap": {},
               "common_fixed_mode_endpoints": {}, "legacy_regressions": [],
               "production_regressions": [], "snap_distance_m": {}}
        statuses = {}
        for label in ("base", "fixed"):
            graph = modes[label][mode]
            snapped = mode_snaps[label][mode]
            distances = [dist(point, node_xy(graph, node))
                         for (_, point), node in zip(points, snapped)]
            row["snap_distance_m"][label] = {"p95": percentile(distances), "max": round(max(distances), 2),
                    "over_50m": sum(x > 50 for x in distances), "over_100m": sum(x > 100 for x in distances),
                    "changed_vs_all_graph": sum(a != b for a, b in zip(all_snap, snapped))}
            for policy, snaps in (("legacy_all_graph_snap", all_snap),
                                  ("production_mode_snap", snapped),
                                  ("common_fixed_mode_endpoints", mode_snaps["fixed"][mode])):
                state = [reachable(graph, snaps[i], snaps[j]) for i, j in pairs]
                statuses[label, policy] = state
                row[policy][label] = dict(Counter(state))
        for policy, output in (("legacy_all_graph_snap", "legacy_regressions"),
                               ("production_mode_snap", "production_regressions")):
            for n, (i, j) in enumerate(pairs):
                if statuses["base", policy][n] != "reachable" or statuses["fixed", policy][n] == "reachable":
                    continue
                old_snap = all_snap if policy == "legacy_all_graph_snap" else mode_snaps["base"][mode]
                new_snap = all_snap if policy == "legacy_all_graph_snap" else mode_snaps["fixed"][mode]
                baseline_path = shortest_details(modes["base"][mode], old_snap[i], old_snap[j])
                removed = [e for e in baseline_path.get("edges", []) if not modes["fixed"][mode].has_edge(*e)]
                row[output].append({"pair_index": n, "start": points[i][0], "end": points[j][0],
                    "base_nodes": [old_snap[i], old_snap[j]], "fixed_nodes": [new_snap[i], new_snap[j]],
                    "failure_type": statuses["fixed", policy][n], "base_route_removed_edges": removed})
        report["modes"][mode] = row
    return report


def geometry_audit(graph):
    summary = {"geometry_present": 0, "geometry_absent_straight_node_link": 0,
               "parse_errors": [], "endpoint_mismatch_over_1m": [],
               "stored_vs_geodesic_length_over_10pct": [], "parallel_uv_pairs": 0}
    errors = []
    ratios = []
    for u, v, k, data in graph.edges(keys=True, data=True):
        summary["geometry_present" if data.get("geometry") else "geometry_absent_straight_node_link"] += 1
        try:
            geom = geometry(graph, u, v, data)
            a, b = list(geom.coords)[0], list(geom.coords)[-1]
            mismatch = min(max(dist(a, node_xy(graph, u)), dist(b, node_xy(graph, v))),
                           max(dist(b, node_xy(graph, u)), dist(a, node_xy(graph, v))))
            errors.append(mismatch)
            if mismatch > 1:
                summary["endpoint_mismatch_over_1m"].append({"edge_id": [u, v, k], "meters": round(mismatch, 2)})
            actual = abs(GEOD.geometry_length(geom))
            stored = float(data["length"])
            ratio = stored / actual if actual else 1
            ratios.append(abs(ratio - 1))
            if abs(ratio - 1) > .1 and abs(stored - actual) > 1:
                summary["stored_vs_geodesic_length_over_10pct"].append({"edge_id": [u, v, k],
                    "stored_m": round(stored, 2), "geometry_m": round(actual, 2)})
        except Exception as exc:
            summary["parse_errors"].append({"edge_id": [u, v, k], "error": str(exc)})
    summary["endpoint_mismatch_p95_m"] = percentile(errors)
    summary["absolute_length_relative_error_p95"] = percentile(ratios)
    summary["parallel_uv_pairs"] = sum(len(vs) > 1 for u in graph for vs in graph[u].values())
    return summary


def reference_audit(graph, modes):
    samples = read_json(ROOT / "data/route_reference_samples.json")["samples"]
    prior_path = ROOT / "scripts/audit_output/amap_comparison_metrics.json"
    prior = read_json(prior_path) if prior_path.exists() else {}
    previous = {row["id"]: row for row in prior.get("samples", [])}
    # Isolate polygon removal while preserving all other current mode rules.
    unclipped_source = graph.copy()
    routing._outside_edge_cache[id(unclipped_source)] = set()
    unclip = routing.filter_graph_for_mode(unclipped_source, "walk")[0]
    routing._outside_edge_cache.pop(id(unclipped_source), None)
    result = []
    for sample in samples:
        a, b = [get_poi(sample[which], fuzzy=False) for which in ("start", "end")]
        points = [gcj02_to_wgs84(p["coordinates"]["lng"], p["coordinates"]["lat"]) for p in (a, b)]
        row = {**sample, "modes": {}}
        previous_row = previous.get(sample["id"], {})
        row["prior_commercial_comparison"] = {k: previous_row[k] for k in (
            "own_distance_m", "reference_distance_m", "geometry_deviation", "endpoint_offset_m") if k in previous_row}
        for mode in ("walk", "bike", "drive"):
            g = modes[mode]
            nodes = [get_nearest_node(g, *p) for p in points]
            pure = shortest_details(g, *nodes)
            details = {"nodes": nodes, "snap_distance_m": [round(dist(p, node_xy(g, n)), 2)
                       for p, n in zip(points, nodes)], "pure_length_route": pure}
            if mode == "walk":
                details["pure_length_without_polygon_filter_same_nodes"] = shortest_details(unclip, *nodes)
            try:
                route = routing.compute_route(graph, *nodes, mode=mode, road_conditions=[], weather_info=None)
                details.update({"planner_status": "success", "applied_weights": route["applied_weights"],
                    "recommended_length_m": route["recommended_length_m"],
                    "builtin_shortest_length_m": route["shortest_length_m"],
                    "recommended_edges": route["recommended_edges"], "builtin_shortest_edges": route["shortest_edges"],
                    "recommended_uses_parallel_uv": [e for e in route["recommended_edges"] if len(graph[e[0]][e[1]]) > 1],
                    "recommended_differs_from_pure_length": route["recommended_edges"] != pure.get("edges")})
            except Exception as exc:
                details.update({"planner_status": "failed", "reason": str(exc)})
            row["modes"][mode] = details
        result.append(row)
    return result


def pbf_audit(graph, reference, matrix, pbf):
    try:
        import osmium
    except ImportError:
        return {"status": "unavailable", "reason": "Python environment has no osmium"}
    osmids = {i for *_, d in graph.edges(data=True) for i in ids(d["osmid"])}
    class Handler(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.ways = {}
            self.barriers = {}
            self.restrictions = []
        def way(self, way):
            if way.id in osmids:
                self.ways[way.id] = {"tags": dict(way.tags), "nodes": [n.ref for n in way.nodes],
                                     "timestamp": str(way.timestamp), "version": way.version}
        def node(self, node):
            if not node.tags.get("barrier"):
                return
            if node.location.valid() and 114.34 <= node.location.lon <= 114.395 and 30.505 <= node.location.lat <= 30.56:
                self.barriers[node.id] = {"tags": dict(node.tags), "xy": [node.location.lon, node.location.lat],
                    "retained_as_graph_node": node.id in graph}
        def relation(self, relation):
            if relation.tags.get("type") == "restriction" and any(m.type == "w" and m.ref in osmids for m in relation.members):
                self.restrictions.append({"id": relation.id, "tags": dict(relation.tags),
                    "members": [{"type": m.type, "ref": m.ref, "role": m.role} for m in relation.members]})
    handler = Handler()
    handler.apply_file(str(pbf))
    way_counts = Counter(k for row in handler.ways.values() for k in row["tags"] if k in TAGS)
    cache_counts = Counter(k for *_, d in graph.edges(data=True) for k in d if k in TAGS)
    represented_nodes = {n for row in handler.ways.values() for n in row["nodes"]}
    barriers = [{"node_id": n, **row} for n, row in handler.barriers.items() if n in represented_nodes]
    risks = read_json(ROOT / "scripts/audit_output/audit_issues.json").get("cross_building_edges", [])
    risk_ids = {(x["u"], x["v"], x.get("k", 0)) for x in risks}
    selected = {tuple(e) for row in reference for d in row["modes"].values() for e in d.get("recommended_edges", [])}
    regressions = {tuple(e) for row in matrix["modes"].values() for policy in ("legacy_regressions", "production_regressions")
                   for r in row[policy] for e in r["base_route_removed_edges"]}
    risk_ids |= {e for e in selected if any(t in {"steps", "corridor"} for t in routing._edge_highway_tags(graph.edges[e]))}
    risk_ids |= regressions
    records = []
    for e in sorted(risk_ids):
        if not graph.has_edge(*e):
            continue
        d = graph.edges[e]
        source = [{"osmid": oid, **handler.ways[oid]} for oid in ids(d["osmid"]) if oid in handler.ways]
        for row in source:
            row.pop("nodes", None)
        records.append({"edge_id": e, "cache_tags": {k: d[k] for k in TAGS + ("name", "walk_penalty", "blocked_modes") if k in d},
            "selected_in_reference": e in selected, "removed_on_regression_baseline": e in regressions,
            "source_ways": source})
    oneway_records = []
    for oid, source in handler.ways.items():
        if source["tags"].get("oneway") not in {"yes", "1", "true", "-1"}:
            continue
        edges = [(u, v, k) for u, v, k, d in graph.edges(keys=True, data=True) if oid in ids(d["osmid"])]
        paired = [e for e in edges if any(ids(graph[e[1]][e[0]][key]["osmid"]) == ids(graph.edges[e]["osmid"])
                  for key in graph.get_edge_data(e[1], e[0], default={}))]
        if paired:
            oneway_records.append({"osmid": oid, "raw_tags": source["tags"], "bidirectional_cache_edges": paired})
    return {"status": "scanned", "cache_distinct_way_ids": len(osmids), "matched_pbf_ways": len(handler.ways),
            "missing_way_ids": sorted(osmids - handler.ways.keys()),
            "tag_counts_raw_ways": dict(way_counts), "tag_counts_cache_directed_edges": dict(cache_counts),
            "barrier_nodes_on_represented_ways": barriers, "restriction_relations": handler.restrictions,
            "oneway_ways_with_two_direction_cache_edges": oneway_records,
            "high_risk_edge_source_records": records,
            "interpretation": "PBF is newer than cache. Differences can include OSM edits; missing cached fields also match current OSMnx defaults. Tags alone do not establish present public access."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=int, default=120)
    parser.add_argument("--pbf", type=Path, default=ROOT / "scripts/audit_output/hubei-260924.osm.pbf")
    parser.add_argument("--output", type=Path, default=ROOT / "scripts/audit_output/routing_source_diagnosis.json")
    args = parser.parse_args()
    logging.getLogger("spatial.routing").setLevel(logging.CRITICAL)
    fixed = load_or_download_network()
    raw = nx.read_graphml(ROOT / "data/whu_road_network.graphml", node_type=int)
    base = raw.copy()
    _merge_annotations(base, str(ROOT / "data/road_annotations.json"))
    modes = {label: {mode: routing.filter_graph_for_mode(graph, mode)[0]
                    for mode in ("walk", "bike", "drive")} for label, graph in (("base", base), ("fixed", fixed))}
    print("Auditing mode-correct reachability", flush=True)
    matrix = matrix_audit(base, fixed, modes, args.pairs)
    print("Auditing six reference pairs and geometry", flush=True)
    references = reference_audit(fixed, modes["fixed"])
    geometry_report = geometry_audit(raw)
    poly = routing.get_campus_polygon()
    projected_poly = transform(PROJECT, poly)
    removed = routing._get_outside_edges(raw)
    removed_crossing = []
    for e in removed:
        overlap = transform(PROJECT, geometry(raw, e[0], e[1], raw.edges[e])).intersection(projected_poly).length
        if overlap > 1:
            removed_crossing.append({"edge_id": e, "inside_campus_geometry_m": round(overlap, 2), "osmid": raw.edges[e]["osmid"]})
    print("Reading original PBF tags", flush=True)
    pbf_report = pbf_audit(fixed, references, matrix, args.pbf) if args.pbf.exists() else {"status": "missing"}
    report = {"schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Read-only audit; no production data, route weights, or routing implementation modified.",
        "sources": {"graph_metadata": raw.graph, "files": {str(p.relative_to(ROOT)): {
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "size_bytes": p.stat().st_size}
            for p in (ROOT / "data/whu_road_network.graphml", ROOT / "data/edge_overrides.json", args.pbf) if p.exists()}},
        "benchmark_findings": [
            "Legacy matrix snaps every mode to the full graph. API first filters by mode, so endpoint_removed is not a production navigation failure.",
            "Commercial comparison sends original POI points, while local route starts/ends at snapped graph nodes; returned entrances are not held equal.",
            "Commercial first route has undocumented objective; local recommended has 0.90/0.05/0.05 plus infrastructure/outside penalties.",
            "Builtin shortest also penalizes steps and building candidates; pure_length_route is an explicit length-only diagnostic on the same legal-edge graph.",
            "Symmetric route-to-route P95 includes different corridors and endpoints. It cannot by itself estimate road-centerline positional error.",
            "Missing access metadata is unknown, not evidence that a road is blocked. Building overlap and nearby steps are candidate signals only."],
        "reachability": matrix, "reference_pairs": references, "geometry": geometry_report,
        "polygon_filter": {"removed_directed_edges": len(removed),
            "removed_edges_with_geometry_inside_polygon": removed_crossing,
            "interpretation": "Endpoint-only clipping may remove an edge that passes through campus. Positive intersection is a geometric fact, not proof the edge must be routable."},
        "original_osm": pbf_report,
        "fair_comparison_protocol": [
            "Freeze graph/data hashes, mode, exact WGS84 query coordinates, timestamp, and road-condition state.",
            "Report POI-to-entrance and entrance-to-network access legs separately; snap to each mode and record snap distance and component.",
            "For baseline comparisons use identical graph nodes from the common routable graph, then separately evaluate each production endpoint policy.",
            "Compare local pure distance, production commute recommendation and all available provider alternatives as separately named objectives.",
            "Measure shared-corridor centerline offsets after endpoint trimming separately from unmatched route length and corridor choice.",
            "Trace disagreements through exact (u,v,key), constituent OSM ways, access/barrier/oneway/indoor/level tags, and dated field or institutional evidence.",
            "Run ablations for polygon filtering, candidate penalties and metadata preservation without changing production or declaring unknown roads closed."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)
    for mode, row in matrix["modes"].items():
        print(mode, "legacy", row["legacy_all_graph_snap"], "mode_correct", row["production_mode_snap"])


if __name__ == "__main__":
    main()
