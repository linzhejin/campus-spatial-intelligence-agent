"""Release checks for reviewed, intentional topology gaps in course road data."""

from __future__ import annotations

import json

import networkx as nx
from pyproj import Transformer
from shapely.geometry import Point
from shapely.ops import transform


def _as_string_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = [value]
    if not isinstance(value, (list, tuple, set)):
        value = [value]
    return [str(item) for item in value]


def _nearest_node(graph: nx.MultiDiGraph, point: Point, project_point) -> tuple[float, object]:
    candidates = [
        (point.distance(transform(
            project_point, Point(float(data["x"]), float(data["y"]))
        )), node)
        for node, data in graph.nodes(data=True)
        if data.get("x") is not None and data.get("y") is not None
    ]
    if not candidates:
        raise ValueError("the route graph has no nodes with coordinates")
    distance, node = min(candidates, key=lambda row: (row[0], str(row[1])))
    return float(distance), node


def _route_distance(graph: nx.MultiDiGraph, start, end) -> float | None:
    from spatial.routing import filter_graph_for_mode

    walk_graph, _, _ = filter_graph_for_mode(graph, "walk")
    try:
        return float(nx.shortest_path_length(walk_graph, start, end, weight="length"))
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


def validate_topology_exceptions(base_graph: nx.MultiDiGraph,
                                 release_graph: nx.MultiDiGraph,
                                 source_features: list[dict],
                                 network_migration: dict,
                                 exceptions: list[dict]) -> list[dict]:
    """Prove intentional source breaks do not become closures or false connectors."""
    if not exceptions:
        return []

    to_metric = Transformer.from_crs(
        "EPSG:4326", "EPSG:4547", always_xy=True
    ).transform
    features_by_layer_row = {}
    for feature in source_features:
        props = feature.get("properties") or {}
        try:
            row = int(props["source_row"])
        except (KeyError, TypeError, ValueError):
            continue
        features_by_layer_row[(str(props.get("source_layer", "")), row)] = feature

    disposition_by_source = {
        str(row.get("source_id")): row
        for row in network_migration.get("course_feature_dispositions", [])
    }
    results = []
    for exception in exceptions:
        source_rows = [int(row) for row in exception["source_rows"]]
        layer = str(exception["source_layer"])
        try:
            first, second = [features_by_layer_row[(layer, row)] for row in source_rows]
        except KeyError as error:
            raise ValueError(
                f"topology exception {exception['id']} references a missing course source row"
            ) from error

        expected_name = str(exception["expected_name"])
        source_ids = []
        for feature in (first, second):
            props = feature.get("properties") or {}
            if str(props.get("name") or "").strip() != expected_name:
                raise ValueError(
                    f"topology exception {exception['id']} road name no longer matches"
                )
            source_id = str(props.get("source_id") or feature.get("id") or "")
            if not source_id:
                raise ValueError(f"topology exception {exception['id']} has no source ID")
            source_ids.append(source_id)
            blocked = set(_as_string_list(props.get("blocked_modes")))
            if "all" in blocked or "walk" in blocked:
                raise ValueError(
                    f"{expected_name} course source rows {source_rows} must not block walking"
                )
            status = str(props.get("current_status") or "unknown").strip().lower()
            if status in {"closed", "blocked", "inaccessible", "封闭", "禁止通行"}:
                raise ValueError(
                    f"{expected_name} course source rows {source_rows} must not be marked closed"
                )

        coordinates_first = (first.get("geometry") or {}).get("coordinates") or []
        coordinates_second = (second.get("geometry") or {}).get("coordinates") or []
        if len(coordinates_first) < 2 or len(coordinates_second) < 2:
            raise ValueError(f"topology exception {exception['id']} has invalid source geometry")
        from_xy = coordinates_first[-1] if exception.get("from_endpoint", "end") == "end" else coordinates_first[0]
        to_xy = coordinates_second[-1] if exception.get("to_endpoint", "start") == "end" else coordinates_second[0]
        from_point = transform(to_metric, Point(float(from_xy[0]), float(from_xy[1])))
        to_point = transform(to_metric, Point(float(to_xy[0]), float(to_xy[1])))
        gap_distance = float(from_point.distance(to_point))
        expected_gap = exception["gap_distance_m"]
        if not float(expected_gap["min"]) <= gap_distance <= float(expected_gap["max"]):
            raise ValueError(
                f"topology exception {exception['id']} gap is {gap_distance:.2f} m, "
                "outside its reviewed range"
            )

        max_snap = float(exception.get("max_endpoint_snap_m", 10.0))
        from_snap, from_node = _nearest_node(base_graph, from_point, to_metric)
        to_snap, to_node = _nearest_node(base_graph, to_point, to_metric)
        if max(from_snap, to_snap) > max_snap:
            raise ValueError(
                f"topology exception {exception['id']} endpoints do not safely snap to OSM"
            )
        if from_node not in release_graph or to_node not in release_graph:
            raise ValueError(f"topology exception {exception['id']} lost a release anchor node")

        base_distance = _route_distance(base_graph, from_node, to_node)
        release_distance = _route_distance(release_graph, from_node, to_node)
        if exception.get("walk_must_remain_connected", True) and (
                base_distance is None or release_distance is None):
            raise ValueError(
                f"{expected_name} walking connectivity was lost across the intentional gap"
            )

        disposition_rows = [disposition_by_source.get(source_id) for source_id in source_ids]
        if any(row is None for row in disposition_rows):
            raise ValueError(f"topology exception {exception['id']} lacks migration records")
        if exception.get("active_new_candidate_forbidden", True) and any(
                row.get("match_action") == "new_candidate" and row.get("active_release")
                for row in disposition_rows
        ):
            raise ValueError(
                f"{expected_name} discontinuity must remain pending instead of being "
                "activated as an unmatched new connector"
            )

        for _, _, _, edge in release_graph.edges(keys=True, data=True):
            edge_source_ids = set(_as_string_list(edge.get("course_source_ids")))
            if edge.get("course_source_id"):
                edge_source_ids.add(str(edge["course_source_id"]))
            if not edge_source_ids.intersection(source_ids):
                continue
            blocked = set(_as_string_list(edge.get("blocked_modes")))
            if "all" in blocked or "walk" in blocked:
                raise ValueError(
                    f"{expected_name} release edges must not block walking"
                )

        results.append({
            "id": exception["id"],
            "source_rows": source_rows,
            "source_ids": source_ids,
            "expected_name": expected_name,
            "intentional_gap_distance_m": round(gap_distance, 2),
            "endpoint_snap_distances_m": {
                "from": round(from_snap, 2),
                "to": round(to_snap, 2),
            },
            "base_walk_route_exists": base_distance is not None,
            "base_walk_route_distance_m": (
                round(base_distance, 2) if base_distance is not None else None
            ),
            "release_walk_route_exists": release_distance is not None,
            "release_walk_route_distance_m": (
                round(release_distance, 2) if release_distance is not None else None
            ),
            "policy": "preserve_existing_walk_topology_no_closure_or_gap_connector",
            "evidence_status": exception.get("evidence_status", "user_provided_context"),
        })
    return results
