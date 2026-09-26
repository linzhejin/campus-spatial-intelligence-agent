"""生成校园 POI 与道路主数据的可复核缺口清单。

只读取本地开放数据和既有审计结果；疑似穿楼、贴台阶不能据此判定不可通行。
用法：python scripts/validate/audit_campus_master.py [--output 路径]
"""

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path
from shapely.geometry import Point, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from spatial.coord_transform import gcj02_to_wgs84  # noqa: E402
from spatial.network import get_nearest_node  # noqa: E402

WHU_BOUNDARY_REFS = {"relation/7728026", "relation/20349098",
                     "relation/20349097", "relation/10717504"}


def read_json(path):
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def fingerprint(path):
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path.relative_to(ROOT)), "sha256": digest.hexdigest()}


def edge_ref(item):
    return [item["u"], item["v"], item.get("k", 0)]


def candidate_snap_regressions(pois, production, candidate, threshold_m=50.0):
    """Find destinations a proposed graph would move beyond navigation range."""
    regressions = []
    for poi in pois:
        coord = poi.get("coordinates") or {}
        if coord.get("lng") is None or coord.get("lat") is None:
            continue
        lng, lat = gcj02_to_wgs84(float(coord["lng"]), float(coord["lat"]))
        distances = []
        for graph in (production, candidate):
            node = get_nearest_node(graph, lng, lat)
            x, y = float(graph.nodes[node]["x"]), float(graph.nodes[node]["y"])
            distances.append(math.hypot(
                (x - lng) * math.cos(math.radians(lat)) * 111320,
                (y - lat) * 110540,
            ))
        if distances[0] <= threshold_m < distances[1]:
            regressions.append({
                "poi_id": poi.get("id"), "name": poi.get("name"),
                "campus": poi.get("campus"),
                "production_snap_m": round(distances[0], 1),
                "candidate_snap_m": round(distances[1], 1),
            })
    return sorted(regressions, key=lambda item: -item["candidate_snap_m"])


def foreign_campus_conflicts(pois, features, whu_refs):
    """Flag formal POIs inside a different mapped institution for review."""
    polygons = {feature["properties"]["osm_id"]: shape(feature["geometry"])
                for feature in features}
    whu = unary_union([geometry for ref, geometry in polygons.items()
                       if ref in whu_refs])
    conflicts = []
    for poi in pois:
        coord = poi.get("coordinates") or {}
        if coord.get("lng") is None or coord.get("lat") is None:
            continue
        point = Point(*gcj02_to_wgs84(float(coord["lng"]), float(coord["lat"])))
        if whu.covers(point):
            continue
        refs = set(poi.get("campus_boundary_refs") or [])
        for feature in features:
            foreign_ref = feature["properties"]["osm_id"]
            if foreign_ref in whu_refs or not polygons[foreign_ref].covers(point):
                continue
            if poi.get("type") == "gate" and foreign_ref in refs and refs & whu_refs:
                continue
            conflicts.append({"poi_id": poi.get("id"), "name": poi.get("name"),
                              "foreign_boundary_ref": foreign_ref,
                              "foreign_boundary_name": feature["properties"].get("name")})
    return conflicts


def make_report():
    poi_path = ROOT / "data" / "pois.json"
    ann_path = ROOT / "data" / "road_annotations.json"
    override_path = ROOT / "data" / "edge_overrides.json"
    graph_path = ROOT / "data" / "whu_road_network.graphml"
    candidate_graph_path = ROOT / "scripts" / "audit_output" / "whu_road_network_candidate.graphml"
    issue_path = ROOT / "scripts" / "audit_output" / "audit_issues.json"
    candidate_path = ROOT / "data" / "pois_osm_candidates.json"
    review_path = ROOT / "data" / "campus_review_decisions.json"
    boundary_path = ROOT / "scripts" / "fetch" / "osm_campus_boundaries.geojson"

    poi_data = read_json(poi_path) or {}
    pois = poi_data.get("pois", [])
    annotations = (read_json(ann_path) or {}).get("edges", [])
    overrides = (read_json(override_path) or {}).get("edges", [])
    issues = read_json(issue_path)
    candidates = read_json(candidate_path)
    reviews = read_json(review_path) or {}
    road_decisions = {
        tuple(item["edge_id"]): item for item in reviews.get("road_decisions", [])
        if isinstance(item.get("edge_id"), list) and len(item["edge_id"]) == 3
    }
    poi_decisions = {
        (item.get("source"), item.get("source_id")): item
        for item in reviews.get("poi_decisions", [])
    }

    status_default = poi_data.get("provenance_defaults", {}).get(
        "verification_status", "legacy_unverified"
    )
    poi_statuses = Counter(p.get("verification_status", status_default) for p in pois)
    poi_campuses = Counter(p.get("campus") or "未归属" for p in pois)
    placeholders = [a for a in annotations if "占位" in str(a.get("note", ""))]
    boundary_features = (read_json(boundary_path) or {}).get("features", [])
    foreign_conflicts = (foreign_campus_conflicts(pois, boundary_features,
                                                  WHU_BOUNDARY_REFS)
                         if boundary_features else [])

    graph_summary = {"cache_available": graph_path.exists()}
    if graph_path.exists():
        import networkx as nx

        G = nx.read_graphml(graph_path, node_type=int)
        graph_summary.update({
            "node_count": G.number_of_nodes(),
            "edge_count": G.number_of_edges(),
            "parallel_node_pairs": sum(len(keys) > 1 for _, nbrs in G.adj.items()
                                       for keys in nbrs.values()),
        })
        snap_risks = []
        snap_distances = []
        named_snap_distances = {}
        for poi in pois:
            coord = poi.get("coordinates") or {}
            if coord.get("lng") is None or coord.get("lat") is None:
                continue
            lng, lat = gcj02_to_wgs84(float(coord["lng"]), float(coord["lat"]))
            node = get_nearest_node(G, lng, lat)
            x, y = float(G.nodes[node]["x"]), float(G.nodes[node]["y"])
            dx = (x - lng) * math.cos(math.radians(lat)) * 111320
            dy = (y - lat) * 110540
            distance = math.hypot(dx, dy)
            snap_distances.append(distance)
            if poi.get("name") == "卓尔体育馆":
                named_snap_distances["卓尔体育馆"] = round(distance, 1)
            if distance > 50:
                snap_risks.append({"poi_id": poi.get("id"), "name": poi.get("name"),
                                   "campus": poi.get("campus"),
                                   "nearest_node": node,
                                   "snap_distance_m": round(distance, 1),
                                   "verification_status": "unverified_candidate"})
        snap_risks.sort(key=lambda item: -item["snap_distance_m"])
        snap_distances.sort()
        graph_summary["poi_snap_max_m"] = round(snap_distances[-1], 1) if snap_distances else None
        graph_summary["poi_snap_p95_m"] = round(
            snap_distances[math.ceil(len(snap_distances) * 0.95) - 1], 1
        ) if snap_distances else None
        graph_summary["named_poi_snap_m"] = named_snap_distances
        graph_summary["poi_snap_over_50m"] = snap_risks
        if candidate_graph_path.exists():
            candidate_graph = nx.read_graphml(candidate_graph_path, node_type=int)
            graph_summary["candidate_graph_available"] = True
            graph_summary["candidate_graph_node_count"] = candidate_graph.number_of_nodes()
            graph_summary["candidate_graph_edge_count"] = candidate_graph.number_of_edges()
            graph_summary["candidate_graph_regressions"] = candidate_snap_regressions(
                pois, G, candidate_graph)

    queue = []
    seen = set()
    for kind, priority, records in (
        ("suspected_building_crossing", 1,
         (issues or {}).get("cross_building_edges", [])),
        ("near_steps", 2, (issues or {}).get("near_steps_edges", [])),
    ):
        for item in records:
            ref = edge_ref(item)
            key = tuple(ref)
            if key in seen:
                continue
            seen.add(key)
            review = road_decisions.get(key) or {}
            verified = (review.get("verification_status") in
                        {"field_verified", "institution_verified"}
                        and bool(review.get("evidence"))
                        and bool(review.get("verified_at")))
            queue.append({
                "edge_id": ref,
                "risk": kind,
                "priority": priority,
                "verification_status": (review["verification_status"] if verified
                                        else "unverified_candidate"),
                "review_decision": {
                    "blocked_modes": review.get("blocked_modes", []),
                    "passable_modes": review.get("passable_modes", []),
                } if review else None,
                "evidence": review.get("evidence"),
            })

    queue.sort(key=lambda row: (row["priority"], tuple(map(str, row["edge_id"]))))
    return {
        "schema_version": 1,
        "coordinate_reference": {"road_network": "WGS84", "poi_catalog": "GCJ02"},
        "source_fingerprints": [
            value for path in (poi_path, ann_path, override_path, graph_path, issue_path,
                               candidate_path, review_path)
            if (value := fingerprint(path)) is not None
        ],
        "poi": {
            "declared_count": poi_data.get("count"),
            "actual_count": len(pois),
            "by_campus": dict(poi_campuses),
            "by_verification_status": dict(poi_statuses),
            "foreign_campus_conflicts": foreign_conflicts,
            "osm_candidate_source_available": candidates is not None,
            "osm_candidate_count": len((candidates or {}).get("candidates", [])),
            "missing_open_candidates": [
                dict(item, review_decision=poi_decisions.get(
                    ("OpenStreetMap", item["osm_id"]), {}).get("decision"))
                for item in (candidates or {}).get("missing_from_production", [])
            ],
            "excluded_open_candidates": (candidates or {}).get("excluded", []),
            "review_decision_count": len(poi_decisions),
        },
        "road": {
            "graph": graph_summary,
            "annotation_count": len(annotations),
            "placeholder_annotation_count": len(placeholders),
            "override_count": len(overrides),
            "suspected_crossing_count": len((issues or {}).get("cross_building_edges", [])),
            "near_steps_count": len((issues or {}).get("near_steps_edges", [])),
            "historical_audit_available": issues is not None,
            "review_queue": queue,
            "reviewed_risk_edge_count": sum(
                item["verification_status"] in {"field_verified", "institution_verified"}
                for item in queue
            ),
        },
        "interpretation": (
            "来源覆盖、几何相交与路段标注均不等于现场通行核实。"
            "review_queue 是核查任务；确认不可通行后才写入路网封禁。"
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "scripts" / "audit_output" / "campus_spatial_quality.json",
    )
    args = parser.parse_args()
    report = make_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(f"审计清单: {args.output}")
    print(f"POI {report['poi']['actual_count']} 条；路段占位标注 "
          f"{report['road']['placeholder_annotation_count']} 条；"
          f"待核查风险边 {len(report['road']['review_queue'])} 条")


if __name__ == "__main__":
    main()
