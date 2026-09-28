#!/usr/bin/env python3
"""Build a reviewable, reversible course-data overlay release bundle."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

import networkx as nx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.network.match_course_network import match_files  # noqa: E402
from spatial.course_data import import_course_data  # noqa: E402
from spatial.course_fusion import fuse_network  # noqa: E402
from spatial.course_poi_fusion import fuse_course_pois, write_json_atomic  # noqa: E402
from spatial.course_topology_policy import validate_topology_exceptions  # noqa: E402
from spatial.network import (  # noqa: E402
    _annotations_path,
    _mark_osm_provenance,
    _merge_annotations,
    _merge_overrides,
    _merge_verified_road_reviews,
    _overrides_path,
    _save_graphml,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


_GRAPHML_KEY_RE = re.compile(
    r'<key\s+id="(?P<id>[^"]+)"\s+for="(?P<scope>[^"]+)"\s+'
    r'attr\.name="(?P<name>[^"]+)"\s+attr\.type="(?P<type>[^"]+)"\s*/>'
)
_GRAPHML_DATA_KEY_RE = re.compile(r'<data\s+key="(?P<id>[^"]+)"')


def _stabilize_graphml_key_ids(path: Path, base_graph_path: Path) -> None:
    """Keep existing GraphML key IDs stable so diffs show data changes, not renumbering."""
    base_xml = base_graph_path.read_bytes().decode("utf-8")
    release_xml = path.read_bytes().decode("utf-8")

    def key_rows(content: str) -> list[dict[str, str]]:
        return [match.groupdict() for match in _GRAPHML_KEY_RE.finditer(content)]

    base_rows = key_rows(base_xml)
    release_rows = key_rows(release_xml)
    base_by_signature = {
        (row["scope"], row["name"], row["type"]): row["id"]
        for row in base_rows
    }
    numeric_ids = [int(row["id"][1:]) for row in base_rows
                   if row["id"].startswith("d") and row["id"][1:].isdigit()]
    next_id = max(numeric_ids, default=-1) + 1
    new_signatures = sorted({
        (row["scope"], row["name"], row["type"])
        for row in release_rows
        if (row["scope"], row["name"], row["type"]) not in base_by_signature
    })
    target_by_signature = dict(base_by_signature)
    for signature in new_signatures:
        target_by_signature[signature] = f"d{next_id}"
        next_id += 1

    id_map = {
        row["id"]: target_by_signature[(row["scope"], row["name"], row["type"])]
        for row in release_rows
    }
    if len(id_map) != len(release_rows):
        raise ValueError("GraphML release has duplicate key IDs")
    release_xml = _GRAPHML_KEY_RE.sub(
        lambda match: match.group(0).replace(
            'id="' + match.group("id") + '"',
            'id="' + id_map[match.group("id")] + '"', 1,
        ),
        release_xml,
    )
    release_xml = _GRAPHML_DATA_KEY_RE.sub(
        lambda match: match.group(0).replace(
            'key="' + match.group("id") + '"',
            'key="' + id_map[match.group("id")] + '"', 1,
        ),
        release_xml,
    )
    temporary = path.with_suffix(path.suffix + ".keys.tmp")
    temporary.write_bytes(release_xml.encode("utf-8"))
    temporary.replace(path)


def _write_graphml_atomic(graph: nx.MultiDiGraph, path: Path,
                          base_graph_path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    _save_graphml(graph, str(temporary))
    temporary.replace(path)
    _stabilize_graphml_key_ids(path, base_graph_path)


def _source_edge_ids(graph: nx.MultiDiGraph, source_ids: set[str]) -> list[tuple]:
    from ast import literal_eval

    selected = []
    for u, v, key, data in graph.edges(keys=True, data=True):
        ids = data.get("course_source_ids") or []
        if isinstance(ids, str):
            try:
                ids = json.loads(ids)
            except json.JSONDecodeError:
                try:
                    ids = literal_eval(ids)
                except (ValueError, SyntaxError):
                    ids = [ids]
        if not isinstance(ids, (list, tuple, set)):
            ids = [ids]
        one = data.get("course_source_id")
        if one:
            ids = [*ids, one]
        if source_ids.intersection(map(str, ids)):
            selected.append((u, v, key))
    return selected


def _validate_component_routes(graph: nx.MultiDiGraph, migration: dict) -> list[dict]:
    from spatial.routing import filter_graph_for_mode

    walk_graph, _, _ = filter_graph_for_mode(graph, "walk")
    bike_graph, _, _ = filter_graph_for_mode(graph, "bike")
    drive_graph, _, _ = filter_graph_for_mode(graph, "drive")
    component_ids = {}
    for row in migration["course_feature_dispositions"]:
        if row.get("active_release") and row.get("component_source_ids"):
            key = tuple(row["component_source_ids"])
            component_ids[key] = key

    results = []
    for ids in component_ids.values():
        source_set = set(ids)
        edge_ids = _source_edge_ids(graph, source_set)
        matching_dispositions = [row for row in migration["course_feature_dispositions"]
                                 if row.get("active_release")
                                 and set(row.get("component_source_ids") or []) == source_set]
        anchor_node_ids = sorted({
            str(node_id)
            for row in matching_dispositions
            for anchor in row.get("component_anchors", [])
            for node_id in anchor.get("release_node_ids", [])
        })
        if len(anchor_node_ids) < 2:
            raise ValueError(f"active course component has fewer than two materialized anchors: {ids}")
        node_lookup = {str(node): node for node in walk_graph.nodes}
        missing_anchors = set(anchor_node_ids) - set(node_lookup)
        if missing_anchors:
            raise ValueError(f"course anchor node is absent from walking graph: {sorted(missing_anchors)}")
        anchor_nodes = [node_lookup[node_id] for node_id in anchor_node_ids]
        component = nx.Graph()
        for u, v, key in edge_ids:
            component.add_edge(u, v)
            data = graph[u][v][key]
            raw_modes = data.get("allowed_modes")
            if isinstance(raw_modes, str):
                try:
                    raw_modes = json.loads(raw_modes)
                except json.JSONDecodeError:
                    raw_modes = [raw_modes]
            allowed = set(map(str, raw_modes or []))
            if "walk" not in allowed:
                raise ValueError(f"course edge {u, v, key} is not allowed for walking")
            if bike_graph.has_edge(u, v, key) or drive_graph.has_edge(u, v, key):
                raise ValueError(f"walk-only course edge {u, v, key} leaked into bike/drive graph")
        terminals = sorted((node for node, degree in component.degree() if degree == 1),
                           key=str)
        if len(terminals) < 2:
            raise ValueError(f"active course component has fewer than two terminals: {ids}")
        if not nx.is_connected(component):
            raise ValueError(f"active course component is topologically disconnected: {ids}")
        terminal_access = []
        for terminal in terminals:
            reachable = []
            for anchor in anchor_nodes:
                if anchor not in walk_graph:
                    continue
                try:
                    distance = nx.shortest_path_length(
                        walk_graph, terminal, anchor, weight="length"
                    )
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    continue
                reachable.append((float(distance), anchor))
            if not reachable:
                raise ValueError(f"course branch terminal cannot walk to an anchor: {ids}")
            distance, anchor = min(reachable)
            terminal_access.append({
                "terminal_node": str(terminal),
                "nearest_anchor_node": str(anchor),
                "walking_distance_m": round(distance, 2),
            })
        results.append({
            "source_ids": list(ids),
            "terminal_nodes": [str(node) for node in terminals],
            "anchor_nodes": list(map(str, anchor_nodes)),
            "course_edge_count": len(edge_ids),
            "terminal_to_anchor_routes": terminal_access,
            "maximum_terminal_to_anchor_distance_m": max(
                row["walking_distance_m"] for row in terminal_access
            ),
            "bike_drive_policy": "course_added_edges_excluded_until_access_verified",
        })
    return results


def _validate_course_poi_snap_distances(graph: nx.MultiDiGraph,
                                       poi_migration: dict) -> list[dict]:
    from pyproj import Transformer
    from shapely.geometry import Point
    from shapely.ops import transform
    from spatial.coord_transform import gcj02_to_wgs84

    to_metric = Transformer.from_crs("EPSG:4326", "EPSG:4547", always_xy=True).transform
    nodes = [
        (node_id, transform(to_metric, Point(float(data["x"]), float(data["y"]))))
        for node_id, data in graph.nodes(data=True)
        if data.get("x") is not None and data.get("y") is not None
    ]
    result = []
    for decision in poi_migration["decisions"]:
        gcj = decision["target_gcj02"]
        wgs = gcj02_to_wgs84(float(gcj["lng"]), float(gcj["lat"]))
        point = transform(to_metric, Point(*wgs))
        nearest = min((point.distance(node_point), node_id) for node_id, node_point in nodes)
        distance_m, node_id = nearest
        if distance_m > 30.0:
            raise ValueError(
                f"course POI {decision['course_name']} is {distance_m:.1f} m from the route graph"
            )
        result.append({
            "course_name": decision["course_name"],
            "target_poi_id": decision["target_poi_id"],
            "nearest_routing_node": str(node_id),
            "snap_distance_m": round(float(distance_m), 2),
        })
    return result


def _validate_release(base_graph, fused_graph, source_features, match_report,
                      network_migration, poi_migration, merged_pois,
                      topology_exceptions):
    source_ids = {feature["properties"]["source_id"] for feature in source_features}
    course_rows = network_migration["course_feature_dispositions"]
    disposition_ids = {row["source_id"] for row in course_rows}
    if source_ids != disposition_ids:
        raise ValueError("not every course road has exactly one migration disposition")
    if len(network_migration["old_edge_dispositions"]) != base_graph.number_of_edges():
        raise ValueError("not every original directed edge has a migration disposition")
    if any(row.get("active_release") for row in course_rows
           if row.get("match_action") == "ambiguous"):
        raise ValueError("an ambiguous course road was activated")
    active_new = [row for row in course_rows
                  if row.get("active_release") and row.get("match_action") == "new_candidate"]
    if any(row.get("component_anchor_count", 0) < 2 for row in active_new):
        raise ValueError("a new course component was activated without two safe anchors")
    if len(poi_migration["poi_id_map"]) != poi_migration["source_id_count"]:
        raise ValueError("some normalized course spot source IDs lack a formal POI mapping")
    if poi_migration["counts"]["unmatched"] != 0:
        raise ValueError("some course POI was not resolved through the reviewed crosswalk")
    if len({poi.get("id") for poi in merged_pois["pois"]}) != len(merged_pois["pois"]):
        raise ValueError("merged POI IDs are not unique")
    match_count = sum(match_report["course"]["counts_by_action"].values())
    if match_count != len(source_features):
        raise ValueError("matching report does not account for every course road")

    spatial_sanity = _validate_spatial_geometry(fused_graph)
    route_checks = _validate_component_routes(fused_graph, network_migration)
    topology_checks = validate_topology_exceptions(
        base_graph, fused_graph, source_features, network_migration,
        topology_exceptions,
    )
    return {
        "course_road_count": len(source_features),
        "spatial_geometry_sanity": spatial_sanity,
        "course_match_counts": match_report["course"]["counts_by_action"],
        "course_active_geometry_replacements": sum(
            bool(row.get("geometry_replaced_edges")) for row in course_rows
        ),
        "course_active_static_attribute_rows": sum(
            bool(row.get("attributes_applied_edges")) for row in course_rows
        ),
        "course_static_only_rows": sum(
            bool(row.get("attributes_applied_edges"))
            and not row.get("geometry_replaced_edges")
            for row in course_rows
        ),
        "course_matched_rows_not_applied": sum(
            row.get("match_action") == "matched"
            and not row.get("geometry_replaced_edges")
            and not row.get("attributes_applied_edges")
            for row in course_rows
        ),
        "course_static_attribute_edges": sum(
            len(row.get("attributes_applied_edges") or []) for row in course_rows
        ),
        "course_active_new_candidates": len(active_new),
        "course_pending_new_candidates": sum(
            row.get("match_action") == "new_candidate" and not row.get("active_release")
            for row in course_rows
        ),
        "course_ambiguous_pending": sum(
            row.get("match_action") == "ambiguous" and not row.get("active_release")
            for row in course_rows
        ),
        "old_edge_disposition_counts": dict(sorted(Counter(
            row["disposition"] for row in network_migration["old_edge_dispositions"]
        ).items())),
        "course_poi_count": poi_migration["input_count"],
        "poi_matches": poi_migration["poi_id_map"],
        "course_poi_route_snaps": _validate_course_poi_snap_distances(
            fused_graph, poi_migration
        ),
        "routable_course_components": route_checks,
        "intentional_topology_exceptions": topology_checks,
    }


def _validate_spatial_geometry(graph: nx.MultiDiGraph) -> dict:
    """Fail release builds on coordinates or lengths impossible for campus roads."""
    from math import isfinite
    from shapely import wkt

    node_coords = [
        (float(data["x"]), float(data["y"]))
        for _, data in graph.nodes(data=True)
        if data.get("x") is not None and data.get("y") is not None
    ]
    if not node_coords:
        raise ValueError("road graph has no coordinate-bearing nodes")
    min_lng, max_lng = min(x for x, _ in node_coords), max(x for x, _ in node_coords)
    min_lat, max_lat = min(y for _, y in node_coords), max(y for _, y in node_coords)
    # The margin tolerates small external connectors, while rejecting a vertex
    # that jumped to another region (for example 109E, 0N).
    margin = 0.05
    max_length_m = 10_000.0
    checked = 0
    max_seen_length = 0.0
    for u, v, key, edge in graph.edges(keys=True, data=True):
        length = float(edge.get("length", 0.0))
        if not isfinite(length) or length <= 0 or length > max_length_m:
            raise ValueError(
                f"edge {(u, v, key)} has impossible campus-road length: {length} m"
            )
        raw_geometry = edge.get("geometry")
        if raw_geometry:
            try:
                geometry = wkt.loads(raw_geometry) if isinstance(raw_geometry, str) else raw_geometry
            except (TypeError, ValueError) as exc:
                raise ValueError(f"edge {(u, v, key)} has unreadable geometry") from exc
            if geometry.is_empty or not geometry.is_valid:
                raise ValueError(f"edge {(u, v, key)} has invalid geometry")
            min_x, min_y, max_x, max_y = geometry.bounds
            if (min_x < min_lng - margin or max_x > max_lng + margin
                    or min_y < min_lat - margin or max_y > max_lat + margin):
                raise ValueError(f"edge {(u, v, key)} geometry is outside campus extent")
        checked += 1
        max_seen_length = max(max_seen_length, length)
    return {"checked_edges": checked, "max_edge_length_m": round(max_seen_length, 2),
            "campus_extent_margin_degrees": margin}


def _validate_serialized_runtime_graph(graph_path: Path, expected_counts: dict,
                                       migration: dict) -> dict:
    """Exercise the same GraphML and sidecar path the running service uses."""
    graph = nx.read_graphml(graph_path, node_type=int)
    if graph.number_of_nodes() != expected_counts["nodes"]:
        raise ValueError("GraphML round-trip changed the node count")
    if graph.number_of_edges() != expected_counts["edges"]:
        raise ValueError("GraphML round-trip changed the directed-edge count")
    if not graph.graph.get("course_release_fingerprint"):
        raise ValueError("course release fingerprint was not saved into GraphML")
    spatial_sanity = _validate_spatial_geometry(graph)

    _mark_osm_provenance(graph)
    annotation_coverage = 0.0
    if Path(_annotations_path()).is_file():
        annotation_coverage = _merge_annotations(graph, _annotations_path())
    override_count = 0
    if Path(_overrides_path()).is_file():
        override_count = _merge_overrides(graph, _overrides_path())
    review_path = ROOT / "data/campus_review_decisions.json"
    review_count = 0
    if review_path.is_file():
        review_count = _merge_verified_road_reviews(graph, str(review_path))

    replaced_edges = 0
    for _, _, _, edge in graph.edges(keys=True, data=True):
        if str(edge.get("course_geometry_replaced", "")).lower() != "true":
            continue
        replaced_edges += 1
        if edge.get("slope_level") is not None or edge.get("scenery_level") is not None:
            raise ValueError("stale slope/scenery annotations returned on course geometry")
        refs = edge.get("source_refs") or []
        if isinstance(refs, str):
            try:
                refs = json.loads(refs)
            except json.JSONDecodeError:
                from ast import literal_eval
                refs = literal_eval(refs)
        ref_sources = {row.get("source") for row in refs if isinstance(row, dict)}
        if "WHU coursework" not in ref_sources or "OpenStreetMap" not in ref_sources:
            raise ValueError("course-replaced edge lost OSM or course provenance at runtime")
    expected_replaced = sum(
        row["disposition"] == "course_geometry_replaced"
        for row in migration["old_edge_dispositions"]
    )
    if replaced_edges != expected_replaced:
        raise ValueError("GraphML round-trip changed the course geometry replacement count")
    route_checks = _validate_component_routes(graph, migration)
    return {
        "node_count": graph.number_of_nodes(),
        "directed_edge_count": graph.number_of_edges(),
        "spatial_geometry_sanity": spatial_sanity,
        "course_geometry_replaced_edges_with_both_sources": replaced_edges,
        "annotation_coverage": round(float(annotation_coverage), 4),
        "overrides_applied": override_count,
        "verified_road_reviews_applied": review_count,
        "routable_course_components": route_checks,
    }


def build_release(source_dir: Path, base_graph_path: Path, pois_path: Path,
                  crosswalk_path: Path, output_dir: Path,
                  topology_exceptions_path: Path | None = None) -> dict:
    source_dir = source_dir.resolve()
    base_graph_path = base_graph_path.resolve()
    pois_path = pois_path.resolve()
    crosswalk_path = crosswalk_path.resolve()
    topology_exceptions_path = (
        topology_exceptions_path or ROOT / "data/course_topology_exceptions.json"
    ).resolve()
    output_dir = output_dir.resolve()
    if (not base_graph_path.is_file() or not pois_path.is_file()
            or not crosswalk_path.is_file() or not topology_exceptions_path.is_file()):
        raise FileNotFoundError(
            "base graph, POI library, crosswalk, and topology exception policy must exist"
        )

    source_dir_out = output_dir / "source"
    matching_dir = output_dir / "matching"
    poi_dir = output_dir / "pois"
    source_result = import_course_data(source_dir, source_dir_out)
    match_files(source_dir_out / "course_roads_normalized.geojson",
                base_graph_path, matching_dir)
    match_report = _read_json(matching_dir / "course_osm_matches.json")
    source_features = _read_json(source_dir_out / "course_roads_normalized.geojson")["features"]
    poi_features = _read_json(source_dir_out / "course_pois_normalized.geojson")["features"]
    topology_policy = _read_json(topology_exceptions_path)
    if topology_policy.get("schema_version") != 1:
        raise ValueError("unsupported course topology exception schema")
    topology_exceptions = topology_policy.get("exceptions") or []
    base_graph = nx.read_graphml(base_graph_path, node_type=int)
    fused_graph, network_migration = fuse_network(
        base_graph, source_features, match_report
    )
    merged_pois, poi_migration = fuse_course_pois(
        _read_json(pois_path), poi_features, _read_json(crosswalk_path)
    )

    base_sha = _sha256(base_graph_path)
    poi_source_sha = _sha256(pois_path)
    crosswalk_sha = _sha256(crosswalk_path)
    topology_policy_sha = _sha256(topology_exceptions_path)
    source_manifest = source_result["manifest"]
    release_identity = {
        "schema_version": 1,
        "base_graph_sha256": base_sha,
        "course_source_fingerprint_sha256": source_manifest["source_fingerprint_sha256"],
        "poi_source_sha256": poi_source_sha,
        "poi_crosswalk_sha256": crosswalk_sha,
        "topology_exception_policy_sha256": topology_policy_sha,
        "network_fusion_schema": network_migration["schema_version"],
    }
    release_fingerprint = hashlib.sha256(json.dumps(
        release_identity, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    fused_graph.graph["base_osm_graph_sha256"] = base_sha
    fused_graph.graph["course_source_fingerprint_sha256"] = source_manifest[
        "source_fingerprint_sha256"
    ]
    fused_graph.graph["course_release_fingerprint"] = release_fingerprint
    fused_graph.graph["course_source_priority"] = "course_wins_static_conflicts"
    fused_graph.graph["course_ambiguous_match_policy"] = "not_activated"

    validation = _validate_release(
        base_graph, fused_graph, source_features, match_report,
        network_migration, poi_migration, merged_pois, topology_exceptions,
    )
    poi_dir.mkdir(parents=True, exist_ok=True)
    graph_path = output_dir / "whu_road_network.graphml"
    _write_graphml_atomic(fused_graph, graph_path, base_graph_path)
    runtime_validation = _validate_serialized_runtime_graph(
        graph_path, network_migration["new_graph_counts"], network_migration
    )
    validation["serialized_runtime_graph"] = runtime_validation
    write_json_atomic(output_dir / "network_migration.json", network_migration)
    write_json_atomic(poi_dir / "pois.merged.json", merged_pois)
    write_json_atomic(poi_dir / "course_poi_migration.json", poi_migration)
    write_json_atomic(output_dir / "validation.json", validation)

    manifest = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "release_fingerprint": release_fingerprint,
        "source_priority": "course wins only on explicit/static correspondence; OSM/Amap remain in the hybrid architecture",
        "redistribution_status": source_manifest["redistribution_status"],
        "license_status": source_manifest["license_status"],
        "source_id": source_result["source_id"],
        "inputs": {
            "base_graph_sha256": base_sha,
            "poi_library_sha256": poi_source_sha,
            "course_source_fingerprint_sha256": source_manifest["source_fingerprint_sha256"],
            "crosswalk_sha256": crosswalk_sha,
            "topology_exception_policy_sha256": topology_policy_sha,
        },
        "outputs": {
            "road_network": {
                "path": graph_path.name,
                "sha256": _sha256(graph_path),
                "nodes": fused_graph.number_of_nodes(),
                "directed_edges": fused_graph.number_of_edges(),
            },
            "poi_library": {
                "path": "pois/pois.merged.json",
                "sha256": _sha256(poi_dir / "pois.merged.json"),
                "count": len(merged_pois["pois"]),
            },
        },
        "validation": validation,
    }
    write_json_atomic(output_dir / "release_manifest.json", manifest)
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path,
                        help="Course RoadNet directory containing whu_road.shp and whu_spot.shp")
    parser.add_argument("--base-graph", type=Path,
                        default=ROOT / "data/whu_road_network_osm_base.graphml")
    parser.add_argument("--pois", type=Path, default=ROOT / "data/pois.json")
    parser.add_argument("--crosswalk", type=Path,
                        default=ROOT / "data/course_poi_crosswalk.json")
    parser.add_argument("--topology-exceptions", type=Path,
                        default=ROOT / "data/course_topology_exceptions.json")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "output/course_fusion/release")
    args = parser.parse_args(argv)
    manifest = build_release(args.source_dir, args.base_graph, args.pois,
                             args.crosswalk, args.output_dir,
                             args.topology_exceptions)
    print(json.dumps({
        "release_fingerprint": manifest["release_fingerprint"],
        "outputs": manifest["outputs"],
        "validation": manifest["validation"],
        "release_manifest": str((args.output_dir.resolve() / "release_manifest.json")),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
