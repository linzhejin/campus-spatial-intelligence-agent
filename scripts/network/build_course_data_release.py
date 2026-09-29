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
from spatial.course_fusion import (  # noqa: E402
    apply_course_junction_overrides,
    fuse_network,
)
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


def _stabilize_graphml_key_ids(path: Path, base_graph_path: Path,
                               previous_graph_path: Path | None = None) -> None:
    """Keep base and previous-release GraphML keys stable across data updates."""
    base_xml = base_graph_path.read_bytes().decode("utf-8")
    release_xml = path.read_bytes().decode("utf-8")

    def key_rows(content: str) -> list[dict[str, str]]:
        return [match.groupdict() for match in _GRAPHML_KEY_RE.finditer(content)]

    base_rows = key_rows(base_xml)
    previous_rows = []
    if (previous_graph_path is not None and previous_graph_path.is_file()
            and previous_graph_path.resolve() != path.resolve()):
        previous_rows = key_rows(previous_graph_path.read_bytes().decode("utf-8"))
    release_rows = key_rows(release_xml)
    base_by_signature = {
        (row["scope"], row["name"], row["type"]): row["id"]
        for row in base_rows
    }
    target_by_signature = dict(base_by_signature)
    for row in previous_rows:
        signature = (row["scope"], row["name"], row["type"])
        existing_id = target_by_signature.get(signature)
        if existing_id is not None and existing_id != row["id"]:
            raise ValueError(f"GraphML key ID changed across releases for {signature}")
        target_by_signature[signature] = row["id"]
    numeric_ids = [int(row["id"][1:]) for row in [*base_rows, *previous_rows]
                   if row["id"].startswith("d") and row["id"][1:].isdigit()]
    next_id = max(numeric_ids, default=-1) + 1
    new_signatures = sorted({
        (row["scope"], row["name"], row["type"])
        for row in release_rows
        if (row["scope"], row["name"], row["type"]) not in target_by_signature
    })
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
                          base_graph_path: Path,
                          previous_graph_path: Path | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    _save_graphml(graph, str(temporary))
    temporary.replace(path)
    _stabilize_graphml_key_ids(path, base_graph_path, previous_graph_path)


def _publish_file_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".publish.tmp")
    with source.open("rb") as source_file, temporary.open("wb") as output_file:
        while chunk := source_file.read(1024 * 1024):
            output_file.write(chunk)
    temporary.replace(destination)


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


def _materialized_course_source_ids(graph: nx.MultiDiGraph) -> set[str]:
    from ast import literal_eval

    result = set()
    for _, _, _, edge in graph.edges(keys=True, data=True):
        raw_ids = edge.get("course_source_ids") or []
        if isinstance(raw_ids, str):
            try:
                raw_ids = json.loads(raw_ids)
            except json.JSONDecodeError:
                try:
                    raw_ids = literal_eval(raw_ids)
                except (ValueError, SyntaxError):
                    raw_ids = [raw_ids]
        if not isinstance(raw_ids, (list, tuple, set)):
            raw_ids = [raw_ids]
        result.update(map(str, raw_ids))
        if edge.get("course_source_id"):
            result.add(str(edge["course_source_id"]))
    return result


def _validate_component_routes(graph: nx.MultiDiGraph, migration: dict) -> list[dict]:
    from spatial.routing import filter_graph_for_mode

    walk_graph, _, _ = filter_graph_for_mode(graph, "walk")
    bike_graph, _, _ = filter_graph_for_mode(graph, "bike")
    drive_graph, _, _ = filter_graph_for_mode(graph, "drive")
    trusted_rows = {int(row) for row in migration.get("trusted_walk_source_rows", [])}
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
        component_rows = {
            int(row["source_row"]) for row in matching_dispositions
            if row.get("source_row") is not None
        }
        trusted_component = bool(component_rows) and component_rows.issubset(trusted_rows)
        minimum_anchor_count = 1 if trusted_component else 2
        anchor_node_ids = sorted({
            str(node_id)
            for row in matching_dispositions
            for anchor in row.get("component_anchors", [])
            for node_id in anchor.get("release_node_ids", [])
        })
        if len(anchor_node_ids) < minimum_anchor_count:
            raise ValueError(
                f"active course component has fewer than {minimum_anchor_count} "
                f"materialized anchors: {ids}"
            )
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
        # Route from every component terminal to its nearest external anchor.
        # Reversing the directed walk graph lets one multi-source Dijkstra
        # answer all terminal-to-anchor queries in one pass, instead of
        # running a shortest-path search for every terminal/anchor pair.
        reverse_walk_graph = walk_graph.reverse(copy=False)
        anchor_distances, anchor_paths = nx.multi_source_dijkstra(
            reverse_walk_graph, sources=anchor_nodes, weight="length"
        )
        terminal_access = []
        for terminal in terminals:
            if terminal not in anchor_distances or not anchor_paths.get(terminal):
                raise ValueError(f"course branch terminal cannot walk to an anchor: {ids}")
            path_from_anchor = anchor_paths[terminal]
            anchor = path_from_anchor[0]
            distance = float(anchor_distances[terminal])
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


def _validate_junction_override_edges(graph: nx.MultiDiGraph, migration: dict) -> int:
    """Confirm audited gate connectors survived GraphML serialization as walk-only edges."""
    checked = 0
    for override in migration.get("course_junction_overrides", []):
        edge_ids = override.get("edge_ids") or []
        if len(edge_ids) != 2:
            raise ValueError(f"junction override {override.get('id')} is not bidirectional")
        for raw_u, raw_v, raw_key in edge_ids:
            u = next((node for node in graph.nodes if str(node) == str(raw_u)), None)
            v = next((node for node in graph.nodes if str(node) == str(raw_v)), None)
            key = int(raw_key)
            if (u is None or v is None or not graph.has_edge(u, v, key)):
                raise ValueError("junction override edge is missing after GraphML round-trip")
            edge = graph[u][v][key]
            if edge.get("course_junction_override_id") != override.get("id"):
                raise ValueError("junction override provenance was lost after GraphML round-trip")
            raw_modes = edge.get("allowed_modes") or []
            if isinstance(raw_modes, str):
                try:
                    raw_modes = json.loads(raw_modes)
                except json.JSONDecodeError:
                    raw_modes = [raw_modes]
            if set(map(str, raw_modes)) != {"walk"}:
                raise ValueError("junction override is not walk-only after GraphML round-trip")
            checked += 1
    return checked


def _build_course_spatial_reference(source_features: list[dict], poi_features: list[dict],
                                    network_migration: dict, poi_migration: dict,
                                    release_fingerprint: str,
                                    license_status: str,
                                    road_coverage: dict | None = None) -> dict:
    """Build a displayable, auditable layer without implying field verification."""
    dispositions = network_migration.get("course_feature_dispositions") or []
    disposition_by_id = {row.get("source_id"): row for row in dispositions}
    source_by_id = {feature.get("properties", {}).get("source_id"): feature
                    for feature in source_features}
    if (None in disposition_by_id or len(disposition_by_id) != len(dispositions)
            or set(source_by_id) != set(disposition_by_id)):
        raise ValueError("course reference layer cannot map every road to one disposition")
    if len(source_by_id) != len(source_features):
        raise ValueError("course reference layer has duplicate road source IDs")
    source_prefixes = {source_id.rsplit(":", 1)[0] for source_id in source_by_id}
    if len(source_prefixes) != 1:
        raise ValueError("course road features contain mixed source datasets")
    junction_source_ids = {
        source_id
        for override in network_migration.get("course_junction_overrides", [])
        for source_id in override.get("course_source_ids", [])
    }

    output_features = []
    for source_id, source_feature in source_by_id.items():
        source = source_feature["properties"]
        disposition = disposition_by_id[source_id]
        if (source_feature.get("geometry", {}).get("type") != "LineString"
                or not source_feature["geometry"].get("coordinates")):
            raise ValueError(f"course road feature has invalid geometry: {source_id}")
        action = (disposition.get("source_match_action")
                  or disposition.get("match_action") or "unknown")
        active = bool(disposition.get("active_release"))
        if source.get("road_class") == "construction" and not active:
            routing_disposition = "construction_excluded"
        elif active:
            routing_disposition = "active_in_route"
        elif action == "ambiguous":
            routing_disposition = "ambiguous_not_changed"
        elif action == "matched":
            routing_disposition = "matched_not_applied"
        else:
            routing_disposition = "candidate_not_activated"
        properties = {
            "name": source.get("name") or source.get("name_raw") or "未命名校方道路",
            "display_name": (
                "玉兰二门连接小路" if source_id in junction_source_ids
                else source.get("name") or source.get("name_raw") or "未命名校方道路"
            ),
            "source_id": source_id,
            "source_row": source.get("source_row"),
            "road_class": source.get("road_class"),
            "road_class_raw": source.get("road_class_raw"),
            "surface": source.get("surface"),
            "lane_count": source.get("lane_count"),
            "source_crs": source.get("source_crs", "EPSG:4547"),
            "source_verification": source.get(
                "source_verification", "course_supplied_not_field_verified"
            ),
            "match_action": action,
            "activation_policy": disposition.get("activation_policy"),
            "routing_disposition": routing_disposition,
            "active_in_routing_graph": active,
            "routing_reason": disposition.get("reason"),
            "network_coverage": (road_coverage or {}).get(source_id, {}),
            "source_layer": "whu_road",
            "license_status": license_status,
        }
        if disposition.get("routing_override"):
            properties["routing_override"] = disposition["routing_override"]
        output_features.append({
            "type": "Feature",
            "id": source_id,
            "geometry": source_feature.get("geometry"),
            "properties": properties,
        })

    poi_id_map = poi_migration.get("poi_id_map") or {}
    for source_feature in poi_features:
        source = source_feature.get("properties") or {}
        if source_feature.get("geometry", {}).get("type") != "Point":
            raise ValueError("normalized course spot is not a point geometry")
        source_ids = source.get("source_ids") or [source.get("source_id")]
        source_ids = [item for item in source_ids if isinstance(item, str)]
        if not source_ids:
            raise ValueError("normalized course spot has no stable source ID")
        mapped_poi_ids = sorted({poi_id_map[source_id] for source_id in source_ids
                                 if source_id in poi_id_map})
        if not mapped_poi_ids:
            raise ValueError(f"course spot is missing a formal POI mapping: {source_ids}")
        output_features.append({
            "type": "Feature",
            "id": source_feature.get("id") or source_ids[0],
            "geometry": source_feature.get("geometry"),
            "properties": {
                "name": source.get("name") or "未命名校方地点",
                "source_id": source_ids[0],
                "source_ids": source_ids,
                "source_rows": source.get("source_rows") or [],
                "formal_poi_ids": mapped_poi_ids,
                "source_crs": source.get("source_crs", "EPSG:4547"),
                "source_verification": "course_supplied_not_field_verified",
                "source_layer": "whu_spot",
                "license_status": license_status,
            },
        })

    return {
        "type": "FeatureCollection",
        "source_id": next(iter(source_prefixes)),
        "release_fingerprint": release_fingerprint,
        "source_crs": "EPSG:4547",
        "coordinate_system": "EPSG:4326",
        "license_status": license_status,
        "counts": {"roads": len(source_features), "spots": len(poi_features)},
        "features": output_features,
    }


def _metric_network_index(graph: nx.MultiDiGraph):
    from pyproj import Transformer
    from shapely.geometry import LineString
    from shapely import wkt
    from shapely.ops import transform
    from shapely.strtree import STRtree

    to_metric = Transformer.from_crs("EPSG:4326", "EPSG:4547", always_xy=True).transform
    node_coords = {
        node: (float(data["x"]), float(data["y"]))
        for node, data in graph.nodes(data=True)
        if data.get("x") is not None and data.get("y") is not None
    }
    lines_by_wkb = {}
    for u, v, _, data in graph.edges(keys=True, data=True):
        raw_geometry = data.get("geometry")
        if raw_geometry:
            geometry = wkt.loads(raw_geometry) if isinstance(raw_geometry, str) else raw_geometry
        else:
            geometry = LineString([node_coords[u], node_coords[v]])
        if geometry.is_empty or not geometry.is_valid:
            continue
        metric_geometry = transform(to_metric, geometry)
        lines_by_wkb.setdefault(metric_geometry.normalize().wkb, metric_geometry)
    lines = list(lines_by_wkb.values())
    if not lines:
        raise ValueError("cannot audit course roads against an empty route network")
    return lines, STRtree(lines)


def _audit_course_road_coverage(base_graph: nx.MultiDiGraph,
                                release_graph: nx.MultiDiGraph,
                                source_features: list[dict],
                                release_fingerprint: str) -> dict:
    """Measure geometric proximity to OSM/release lines; this is not access verification."""
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform, unary_union

    to_metric = Transformer.from_crs("EPSG:4326", "EPSG:4547", always_xy=True).transform
    networks = {
        "osm_base": _metric_network_index(base_graph),
        "release": _metric_network_index(release_graph),
    }
    thresholds = (3, 8, 15)
    rows = []
    coverage_by_id = {}
    for feature in source_features:
        source_id = feature["properties"]["source_id"]
        source_row = feature["properties"].get("source_row")
        line = transform(to_metric, shape(feature["geometry"]))
        if line.is_empty or not line.is_valid or line.length <= 0:
            raise ValueError(f"course road geometry cannot be measured: {source_id}")
        metrics = {}
        for network_name, (network_geometries, network_index) in networks.items():
            nearby_indexes = network_index.query(line.buffer(max(thresholds)))
            nearby = [network_geometries[int(index)] for index in nearby_indexes]
            for threshold in thresholds:
                close_geometries = [geometry for geometry in nearby
                                    if geometry.distance(line) <= threshold]
                if close_geometries:
                    local_corridor = unary_union(close_geometries).buffer(threshold)
                    covered_length = line.intersection(local_corridor).length
                else:
                    covered_length = 0.0
                metrics[f"{network_name}_within_{threshold}m_pct"] = round(
                    min(100.0, covered_length / line.length * 100.0), 2
                )
        coverage_by_id[source_id] = metrics
        rows.append({
            "source_id": source_id,
            "source_row": source_row,
            "name": feature["properties"].get("name"),
            "course_length_m": round(float(line.length), 2),
            **metrics,
        })

    summary = {}
    for network_name in ("osm_base", "release"):
        summary[network_name] = {}
        for threshold in thresholds:
            key = f"{network_name}_within_{threshold}m_pct"
            covered = [row for row in rows if row[key] >= 95.0]
            summary[network_name][f"within_{threshold}m_at_least_95pct"] = len(covered)
    unresolved_15m = [
        row for row in rows if row["release_within_15m_pct"] < 95.0
    ]
    return {
        "schema_version": 1,
        "release_fingerprint": release_fingerprint,
        "metric_crs": "EPSG:4547",
        "source_crs": "EPSG:4547",
        "course_road_count": len(rows),
        "thresholds_m": list(thresholds),
        "coverage_definition": "course line length within buffer of road geometry divided by course line length",
        "interpretation": "Geometric proximity only. It does not establish pedestrian access, on-site passability, or field verification.",
        "summary": summary,
        "release_rows_below_95pct_within_15m": sorted(
            unresolved_15m, key=lambda row: row["source_row"]
        ),
        "roads": rows,
    }, coverage_by_id


def _validate_release(base_graph, fused_graph, source_features, match_report,
                      network_migration, poi_migration, merged_pois,
                      topology_exceptions, expected_active_source_ids=None):
    source_ids = {feature["properties"]["source_id"] for feature in source_features}
    course_rows = network_migration["course_feature_dispositions"]
    disposition_ids = {row["source_id"] for row in course_rows}
    if source_ids != disposition_ids:
        raise ValueError("not every course road has exactly one migration disposition")
    if expected_active_source_ids is not None:
        active_source_ids = {
            row["source_id"] for row in course_rows if row.get("active_release")
        }
        expected_active_source_ids = set(expected_active_source_ids)
        if active_source_ids != expected_active_source_ids:
            raise ValueError(
                "course routing activation differs from the explicit source-row policy"
            )
        materialized = _materialized_course_source_ids(fused_graph)
        if not expected_active_source_ids.issubset(materialized):
            missing = sorted(expected_active_source_ids - materialized)
            raise ValueError(f"active course roads are absent from the graph: {missing[:10]}")
    if len(network_migration["old_edge_dispositions"]) != base_graph.number_of_edges():
        raise ValueError("not every original directed edge has a migration disposition")
    if any(row.get("active_release") for row in course_rows
           if row.get("match_action") == "ambiguous"):
        raise ValueError("an ambiguous course road was activated")
    active_new = [row for row in course_rows
                  if row.get("active_release") and row.get("match_action") == "new_candidate"]
    trusted_rows = {int(row) for row in network_migration.get("trusted_walk_source_rows", [])}
    if any(
            row.get("component_anchor_count", 0)
            < (1 if int(row.get("source_row", -1)) in trusted_rows else 2)
            for row in active_new):
        raise ValueError("a course component was activated without its required safe anchor")
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
        "course_active_geometry_variants": sum(
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
        "course_active_walk_source_rows": sum(
            row.get("active_release") is True for row in course_rows
        ),
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
        "course_junction_overrides": network_migration.get(
            "course_junction_overrides", []
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
    if (migration.get("course_junction_overrides")
            and not graph.graph.get("course_junction_override_policy_sha256")):
        raise ValueError("junction override policy fingerprint was not saved into GraphML")
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
        row["disposition"] == "retained_osm_with_walk_only_course_variant"
        for row in migration["old_edge_dispositions"]
    )
    if replaced_edges != expected_replaced:
        raise ValueError("GraphML round-trip changed the course geometry replacement count")
    route_checks = _validate_component_routes(graph, migration)
    return {
        "node_count": graph.number_of_nodes(),
        "directed_edge_count": graph.number_of_edges(),
        "spatial_geometry_sanity": spatial_sanity,
        "course_geometry_variant_edges_with_both_sources": replaced_edges,
        "annotation_coverage": round(float(annotation_coverage), 4),
        "overrides_applied": override_count,
        "verified_road_reviews_applied": review_count,
        "junction_override_edges_checked": _validate_junction_override_edges(graph, migration),
        "routable_course_components": route_checks,
    }


def build_release(source_dir: Path, base_graph_path: Path, pois_path: Path,
                  crosswalk_path: Path, output_dir: Path,
                  topology_exceptions_path: Path | None = None,
                  junction_overrides_path: Path | None = None,
                  routing_policy_path: Path | None = None) -> dict:
    source_dir = source_dir.resolve()
    base_graph_path = base_graph_path.resolve()
    pois_path = pois_path.resolve()
    crosswalk_path = crosswalk_path.resolve()
    topology_exceptions_path = (
        topology_exceptions_path or ROOT / "data/course_topology_exceptions.json"
    ).resolve()
    junction_overrides_path = (
        junction_overrides_path or ROOT / "data/course_junction_overrides.json"
    ).resolve()
    routing_policy_path = (
        routing_policy_path or ROOT / "data/course_routing_policy.json"
    ).resolve()
    output_dir = output_dir.resolve()
    if (not base_graph_path.is_file() or not pois_path.is_file()
            or not crosswalk_path.is_file() or not topology_exceptions_path.is_file()
            or not junction_overrides_path.is_file() or not routing_policy_path.is_file()):
        raise FileNotFoundError(
            "base graph, POI library, crosswalk, topology exceptions, and "
            "junction override and course routing policies must exist"
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
    routing_policy = _read_json(routing_policy_path)
    if routing_policy.get("schema_version") != 1:
        raise ValueError("unsupported course routing policy schema")
    if routing_policy.get("source_layer") != "whu_road":
        raise ValueError("course routing policy must target the whu_road layer")
    if routing_policy.get("source_authority") != "explicit_user_instruction":
        raise ValueError("course road activation requires explicit source authority")
    if sorted(map(str, routing_policy.get("allowed_modes") or [])) != ["walk"]:
        raise ValueError("unverified course road rows may only enter the walk graph")
    if sorted(map(str, routing_policy.get("excluded_road_classes") or [])) != ["construction"]:
        raise ValueError("course routing policy must keep construction roads out of routing")
    if not isinstance(routing_policy.get("include_unclassified"), bool):
        raise ValueError("course routing policy must explicitly handle unclassified roads")
    if routing_policy.get("allow_single_anchor_for_trusted_source") is not True:
        raise ValueError("trusted course branches require the reviewed single-anchor policy")
    construction_override_rows = sorted({
        int(row) for row in routing_policy.get("walkable_construction_source_rows", [])
    })
    raw_construction_override_rows = routing_policy.get(
        "walkable_construction_source_rows", []
    )
    if len(construction_override_rows) != len(raw_construction_override_rows):
        raise ValueError("walkable construction source rows must be unique")
    construction_overrides = routing_policy.get("construction_walk_overrides") or []
    if not isinstance(construction_overrides, list):
        raise ValueError("construction walk overrides must be a list")
    override_rows = [int(row["source_row"]) for row in construction_overrides
                     if isinstance(row, dict) and row.get("source_row") is not None]
    if (len(override_rows) != len(construction_overrides)
            or sorted(set(override_rows)) != construction_override_rows):
        raise ValueError(
            "walkable construction rows must have exactly one explicit override each"
        )
    construction_override_by_row = {}
    for override in construction_overrides:
        row = int(override["source_row"])
        if (sorted(set(map(str, override.get("allowed_modes") or []))) != ["walk"]
                or override.get("verification_status") != "source_only"
                or override.get("authority") != "explicit_user_instruction"
                or not str(override.get("reason") or "").strip()
                or not str(override.get("evidence_status") or "").strip()):
            raise ValueError(
                f"construction row {row} requires a reasoned, walk-only user override"
            )
        construction_override_by_row[row] = override
    try:
        attachment_tolerance_m = float(routing_policy["attachment_tolerance_m"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("course routing policy requires an attachment tolerance") from exc
    if not 0 < attachment_tolerance_m <= 5.0:
        raise ValueError("course routing attachment tolerance must be within (0, 5] meters")
    preserved_rows = sorted({
        int(row)
        for exception in topology_exceptions
        if exception.get("active_new_candidate_forbidden", True)
        for row in exception.get("source_rows", [])
    })
    policy_preserved_rows = sorted({
        int(row) for row in routing_policy.get("excluded_source_rows", [])
    })
    if policy_preserved_rows != preserved_rows:
        raise ValueError(
            "course routing policy exclusions must match reviewed topology exceptions"
        )
    junction_policy = _read_json(junction_overrides_path)
    if junction_policy.get("schema_version") != 1:
        raise ValueError("unsupported course junction override policy schema")
    base_graph = nx.read_graphml(base_graph_path, node_type=int)
    included_classes = set(map(str, routing_policy.get("included_road_classes") or []))
    excluded_classes = set(map(str, routing_policy.get("excluded_road_classes") or []))
    trusted_walk_source_rows = set()
    observed_classes = set()
    for feature in source_features:
        properties = feature.get("properties") or {}
        source_row = int(properties["source_row"])
        road_class = properties.get("road_class")
        if source_row in construction_override_by_row:
            if str(road_class) != "construction":
                raise ValueError(
                    f"construction walk override row {source_row} is not a construction feature"
                )
            trusted_walk_source_rows.add(source_row)
            observed_classes.add(str(road_class))
            continue
        if road_class is None:
            if routing_policy["include_unclassified"]:
                trusted_walk_source_rows.add(source_row)
            continue
        road_class = str(road_class)
        observed_classes.add(road_class)
        if road_class in included_classes:
            trusted_walk_source_rows.add(source_row)
        elif road_class not in excluded_classes:
            raise ValueError(f"course road class has no routing policy: {road_class}")
    if not observed_classes.issubset(included_classes | excluded_classes):
        raise ValueError("course routing policy does not account for every road class")
    fused_graph, network_migration = fuse_network(
        base_graph, source_features, match_report,
        attachment_tolerance_m=attachment_tolerance_m,
        trusted_walk_source_rows=trusted_walk_source_rows,
        preserved_source_rows=preserved_rows,
        walkable_construction_source_rows=construction_override_rows,
        walkable_construction_overrides=construction_override_by_row,
    )
    apply_course_junction_overrides(fused_graph, network_migration, junction_policy)
    merged_pois, poi_migration = fuse_course_pois(
        _read_json(pois_path), poi_features, _read_json(crosswalk_path)
    )

    base_sha = _sha256(base_graph_path)
    poi_source_sha = _sha256(pois_path)
    crosswalk_sha = _sha256(crosswalk_path)
    topology_policy_sha = _sha256(topology_exceptions_path)
    junction_policy_sha = _sha256(junction_overrides_path)
    routing_policy_sha = _sha256(routing_policy_path)
    source_manifest = source_result["manifest"]
    release_identity = {
        "schema_version": 1,
        "base_graph_sha256": base_sha,
        "course_source_fingerprint_sha256": source_manifest["source_fingerprint_sha256"],
        "poi_source_sha256": poi_source_sha,
        "poi_crosswalk_sha256": crosswalk_sha,
        "topology_exception_policy_sha256": topology_policy_sha,
        "course_junction_override_policy_sha256": junction_policy_sha,
        "course_routing_policy_sha256": routing_policy_sha,
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
    fused_graph.graph["course_ambiguous_match_policy"] = (
        "trusted_course_geometry_added_walk_only_without_deleting_osm_topology"
    )
    fused_graph.graph["course_junction_override_policy_sha256"] = junction_policy_sha
    fused_graph.graph["course_routing_policy_sha256"] = routing_policy_sha

    expected_active_source_ids = {
        feature["properties"]["source_id"] for feature in source_features
        if int(feature["properties"]["source_row"]) in trusted_walk_source_rows
        and int(feature["properties"]["source_row"]) not in preserved_rows
    }

    validation = _validate_release(
        base_graph, fused_graph, source_features, match_report,
        network_migration, poi_migration, merged_pois, topology_exceptions,
        expected_active_source_ids,
    )
    validation["course_routing_policy_sha256"] = routing_policy_sha
    validation["walkable_construction_source_rows"] = construction_override_rows
    validation["course_excluded_topology_rows"] = preserved_rows
    coverage_audit, road_coverage = _audit_course_road_coverage(
        base_graph, fused_graph, source_features, release_fingerprint
    )
    validation["course_geometry_coverage"] = coverage_audit["summary"]
    validation["course_geometry_coverage_unresolved_count"] = len(
        coverage_audit["release_rows_below_95pct_within_15m"]
    )
    poi_dir.mkdir(parents=True, exist_ok=True)
    graph_path = output_dir / "whu_road_network.graphml"
    _write_graphml_atomic(
        fused_graph, graph_path, base_graph_path,
        ROOT / "data/whu_road_network.graphml",
    )
    runtime_validation = _validate_serialized_runtime_graph(
        graph_path, network_migration["new_graph_counts"], network_migration
    )
    validation["serialized_runtime_graph"] = runtime_validation
    write_json_atomic(output_dir / "network_migration.json", network_migration)
    write_json_atomic(poi_dir / "pois.merged.json", merged_pois, indent=1)
    write_json_atomic(poi_dir / "course_poi_migration.json", poi_migration)
    write_json_atomic(output_dir / "validation.json", validation)

    reference_layer = _build_course_spatial_reference(
        source_features, poi_features, network_migration, poi_migration,
        release_fingerprint, source_manifest["license_status"], road_coverage,
    )
    reference_release_path = output_dir / "course_spatial_reference.geojson"
    published_reference_path = ROOT / "data/course_spatial_reference.geojson"
    coverage_release_path = output_dir / "course_road_coverage_audit.json"
    published_coverage_path = ROOT / "data/course_road_coverage_audit.json"
    write_json_atomic(reference_release_path, reference_layer)
    write_json_atomic(coverage_release_path, coverage_audit)

    manifest = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "release_fingerprint": release_fingerprint,
        "source_priority": "trusted course geometry enters the walk graph without deleting OSM topology; Amap remains the independent online reference",
        "redistribution_status": source_manifest["redistribution_status"],
        "license_status": source_manifest["license_status"],
        "source_id": source_result["source_id"],
        "inputs": {
            "base_graph_sha256": base_sha,
            "poi_library_sha256": poi_source_sha,
            "course_source_fingerprint_sha256": source_manifest["source_fingerprint_sha256"],
            "crosswalk_sha256": crosswalk_sha,
            "topology_exception_policy_sha256": topology_policy_sha,
            "course_junction_override_policy_sha256": junction_policy_sha,
            "course_routing_policy_sha256": routing_policy_sha,
        },
        "outputs": {
            "road_network": {
                "path": graph_path.name,
                "published_path": "data/whu_road_network.graphml",
                "sha256": _sha256(graph_path),
                "nodes": fused_graph.number_of_nodes(),
                "directed_edges": fused_graph.number_of_edges(),
            },
            "poi_library": {
                "path": "pois/pois.merged.json",
                "published_path": "data/pois.json",
                "sha256": _sha256(poi_dir / "pois.merged.json"),
                "count": len(merged_pois["pois"]),
            },
            "course_spatial_reference": {
                "release_path": reference_release_path.name,
                "published_path": "data/course_spatial_reference.geojson",
                "sha256": _sha256(reference_release_path),
                "road_count": reference_layer["counts"]["roads"],
                "spot_count": reference_layer["counts"]["spots"],
                "coordinate_system": reference_layer["coordinate_system"],
            },
            "course_road_coverage_audit": {
                "release_path": coverage_release_path.name,
                "published_path": "data/course_road_coverage_audit.json",
                "sha256": _sha256(coverage_release_path),
                "road_count": coverage_audit["course_road_count"],
                "unresolved_below_95pct_within_15m": len(
                    coverage_audit["release_rows_below_95pct_within_15m"]
                ),
                "metric_crs": coverage_audit["metric_crs"],
            },
        },
        "validation": validation,
    }
    write_json_atomic(output_dir / "release_manifest.json", manifest)

    # Promote only after source, topology, serialization, and route checks have
    # passed. These are the assets read by the running API and map layer.
    _publish_file_atomic(graph_path, ROOT / "data/whu_road_network.graphml")
    write_json_atomic(ROOT / "data/pois.json", merged_pois, indent=1)
    write_json_atomic(published_reference_path, reference_layer)
    write_json_atomic(published_coverage_path, coverage_audit)
    if _sha256(ROOT / "data/whu_road_network.graphml") != _sha256(graph_path):
        raise ValueError("published runtime graph differs from the validated release graph")
    if _sha256(ROOT / "data/pois.json") != _sha256(poi_dir / "pois.merged.json"):
        raise ValueError("published POI master differs from the validated merged library")
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
    parser.add_argument("--junction-overrides", type=Path,
                        default=ROOT / "data/course_junction_overrides.json")
    parser.add_argument("--routing-policy", type=Path,
                        default=ROOT / "data/course_routing_policy.json")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "output/course_fusion/release")
    args = parser.parse_args(argv)
    manifest = build_release(args.source_dir, args.base_graph, args.pois,
                             args.crosswalk, args.output_dir,
                             args.topology_exceptions, args.junction_overrides,
                             args.routing_policy)
    print(json.dumps({
        "release_fingerprint": manifest["release_fingerprint"],
        "outputs": manifest["outputs"],
        "validation": manifest["validation"],
        "release_manifest": str((args.output_dir.resolve() / "release_manifest.json")),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
