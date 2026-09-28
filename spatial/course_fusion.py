"""Conservative graph fusion for the normalized WHU coursework road layer."""

from __future__ import annotations

import ast
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math


def _parse(value):
    if isinstance(value, str):
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            try:
                return json.loads(value)
            except (ValueError, TypeError):
                return value
    return value


def _edge_id(raw):
    if not isinstance(raw, (list, tuple)) or len(raw) < 3:
        return None
    u, v, key = raw[:3]
    try:
        u = int(u) if str(u).lstrip("-").isdigit() else u
        v = int(v) if str(v).lstrip("-").isdigit() else v
        key = int(key)
    except (TypeError, ValueError):
        return None
    return u, v, key


def _line_from_edge(graph, u, v, data):
    from shapely import wkt
    from shapely.geometry import LineString
    from shapely.ops import linemerge

    geometry = _parse(data.get("geometry"))
    if isinstance(geometry, str):
        try:
            geometry = wkt.loads(geometry)
        except Exception:
            geometry = None
    if geometry is None or not hasattr(geometry, "geom_type"):
        geometry = LineString([
            (float(graph.nodes[u]["x"]), float(graph.nodes[u]["y"])),
            (float(graph.nodes[v]["x"]), float(graph.nodes[v]["y"])),
        ])
    if geometry.geom_type == "MultiLineString":
        geometry = linemerge(geometry)
    if geometry.geom_type != "LineString" or geometry.is_empty:
        return None
    return geometry


def _canonical_geometry_id(line):
    coords = [(round(float(x), 3), round(float(y), 3)) for x, y, *rest in line.coords]
    canonical = min(coords, list(reversed(coords)))
    return hashlib.sha256(
        json.dumps(canonical, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _graph_points(graph, to_metric):
    from shapely.geometry import Point
    from shapely.ops import transform

    points = []
    refs = []
    for node, data in graph.nodes(data=True):
        if data.get("x") is None or data.get("y") is None:
            continue
        point = transform(to_metric, Point(float(data["x"]), float(data["y"])))
        points.append(point)
        refs.append(node)
    return points, refs


def _physical_edges(graph, to_metric):
    """Collapse reverse directed edges while preserving parallel topologies."""
    records = {}
    from shapely.ops import transform

    for u, v, key, data in graph.edges(keys=True, data=True):
        line = _line_from_edge(graph, u, v, data)
        if line is None:
            continue
        metric = transform(to_metric, line)
        geometry_id = _canonical_geometry_id(metric)
        endpoint_pair = tuple(sorted((str(u), str(v))))
        physical_id = f"{geometry_id}:{endpoint_pair[0]}:{endpoint_pair[1]}"
        row = records.get(physical_id)
        if row is None:
            row = {
                "physical_id": physical_id,
                "geometry": metric,
                "edge_ids": [],
                "endpoint_nodes": set(),
                "data_rows": [],
                "grade_separated": False,
            }
            records[physical_id] = row
        row["edge_ids"].append((u, v, int(key)))
        row["endpoint_nodes"].update((u, v))
        row["data_rows"].append(data)
        row["grade_separated"] = row["grade_separated"] or _grade_separated(data)
    return list(records.values())


def _grade_separated(data):
    def value(raw):
        raw = _parse(raw)
        if isinstance(raw, (list, tuple)):
            return any(value(item) for item in raw)
        return raw not in (None, False, 0, "", "no", "false", "0")

    layer = _parse(data.get("layer", "0"))
    try:
        nonzero_layer = float(layer) != 0.0
    except (TypeError, ValueError):
        nonzero_layer = str(layer) not in {"None", ""}
    return value(data.get("bridge")) or value(data.get("tunnel")) or nonzero_layer


def _query_indices(tree, geometries, geometry_index, query_geometry):
    from numbers import Integral

    result = tree.query(query_geometry)
    indices = []
    for item in result:
        if isinstance(item, Integral) or getattr(item, "dtype", None) is not None:
            indices.append(int(item))
        else:
            index = geometry_index.get(id(item))
            if index is not None:
                indices.append(index)
    return sorted(set(indices))


def _nearest_attachment(point, graph, node_points, node_refs, node_tree,
                        edge_records, edge_tree, edge_geometries, edge_index,
                        tolerance_m):
    from shapely.geometry import Point

    nearby_node_indices = _query_indices(
        node_tree, node_points, {id(g): i for i, g in enumerate(node_points)},
        point.buffer(tolerance_m),
    ) if node_tree is not None else []
    node_candidates = sorted(
        (point.distance(node_points[index]), node_refs[index])
        for index in nearby_node_indices
        if point.distance(node_points[index]) <= tolerance_m
    )
    if node_candidates:
        best_distance, best_node = node_candidates[0]
        alternatives = [row for row in node_candidates[1:]
                        if row[1] != best_node and row[0] <= best_distance + 0.5]
        if not alternatives:
            return {"kind": "node", "node_id": best_node,
                    "gap_m": float(best_distance)}
        return {"kind": "ambiguous", "reason": "multiple_nearby_nodes",
                "gap_m": float(best_distance)}

    edge_indices = _query_indices(edge_tree, edge_geometries, edge_index,
                                  point.buffer(tolerance_m))
    edge_candidates = []
    for index in edge_indices:
        row = edge_records[index]
        distance = point.distance(row["geometry"])
        if distance > tolerance_m:
            continue
        measure = row["geometry"].project(point)
        nearest_point = row["geometry"].interpolate(measure)
        edge_candidates.append((distance, row["physical_id"], row, measure, nearest_point))
    edge_candidates.sort(key=lambda item: (item[0], item[1]))
    if not edge_candidates:
        return {"kind": "none", "reason": "no_network_anchor_within_tolerance"}
    best = edge_candidates[0]
    ties = [item for item in edge_candidates[1:] if item[0] <= best[0] + 0.5]
    if ties:
        return {"kind": "ambiguous", "reason": "parallel_or_crossing_edge_candidates",
                "gap_m": float(best[0])}
    if best[2]["grade_separated"]:
        return {"kind": "none", "reason": "grade_separation_veto",
                "gap_m": float(best[0])}
    return {
        "kind": "edge",
        "physical_id": best[1],
        "gap_m": float(best[0]),
        "measure_m": float(best[3]),
        "point_metric": (float(best[4].x), float(best[4].y)),
        "edge_ids": [list(edge_id) for edge_id in best[2]["edge_ids"]],
    }


def _cluster_course_endpoints(endpoint_rows, tolerance_m=0.15):
    parents = list(range(len(endpoint_rows)))

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for left in range(len(endpoint_rows)):
        point_left = endpoint_rows[left]["point_metric"]
        for right in range(left + 1, len(endpoint_rows)):
            point_right = endpoint_rows[right]["point_metric"]
            if point_left.distance(point_right) <= tolerance_m:
                union(left, right)

    groups = defaultdict(list)
    for index, row in enumerate(endpoint_rows):
        groups[find(index)].append(row)
    return list(groups.values())


def _course_components(features, endpoint_cluster):
    parents = {}

    def find(value):
        parents.setdefault(value, value)
        if parents[value] != value:
            parents[value] = find(parents[value])
        return parents[value]

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for feature in features:
        source_id = feature["properties"]["source_id"]
        left, right = endpoint_cluster[source_id, 0], endpoint_cluster[source_id, 1]
        union(left, right)
    grouped = defaultdict(list)
    for feature in features:
        source_id = feature["properties"]["source_id"]
        root = find(endpoint_cluster[source_id, 0])
        grouped[root].append(feature)
    return list(grouped.values())


def _source_ref(source_id):
    return {
        "source": "WHU coursework",
        "id": source_id,
        "license": "not_provided_with_source",
    }


def _append_course_provenance(edge, source_id):
    edge["course_source_ids"] = sorted(set(_parse(edge.get("course_source_ids") or [])
                                           + [source_id]))
    refs = _parse(edge.get("source_refs") or [])
    if isinstance(refs, dict):
        refs = [refs]
    refs = list(refs) if isinstance(refs, (list, tuple)) else []
    course_ref = _source_ref(source_id)
    if course_ref not in refs:
        refs.append(course_ref)
    edge["source_refs"] = refs


def _append_course_attributes(edge, properties, source_id):
    _append_course_provenance(edge, source_id)
    edge["course_road_class"] = properties["road_class"]
    edge["course_road_class_raw"] = properties.get("road_class_raw")
    edge["course_surface"] = properties.get("surface")
    edge["course_surface_raw"] = properties.get("surface_raw")
    edge["course_lane_count"] = properties.get("lane_count")
    edge["course_geographic_code"] = properties.get("geographic_code")
    edge["course_raw_source_properties"] = json.dumps(
        properties.get("raw_source_properties") or {}, ensure_ascii=False,
        sort_keys=True, allow_nan=False,
    )
    if properties.get("name"):
        current_name = edge.get("name")
        if current_name and str(current_name) != str(properties["name"]):
            edge.setdefault("osm_name", current_name)
            edge["course_name_conflict"] = json.dumps({
                "osm_name": current_name, "course_name": properties["name"],
                "priority": "course",
            }, ensure_ascii=False, allow_nan=False)
        edge["name"] = str(properties["name"])
        edge["course_name_source"] = "course"


def _set_geometry(graph, u, v, edge, metric_line, from_metric):
    from shapely.ops import transform

    wgs_line = transform(from_metric, metric_line)
    edge["geometry"] = wgs_line.wkt
    edge["length"] = float(metric_line.length)


def _replace_matched_edges(graph, course_features, match_by_source,
                           to_metric, from_metric, max_endpoint_offset_m=2.0):
    from shapely.geometry import Point, shape
    from shapely.ops import substring, transform

    proposals = defaultdict(list)
    attribute_proposals = defaultdict(list)
    dispositions = {}
    for feature in course_features:
        properties = feature.get("properties") or {}
        source_id = properties["source_id"]
        match = match_by_source.get(source_id, {})
        action = match.get("action")
        disposition = {
            "source_id": source_id,
            "source_row": properties.get("source_row"),
            "match_action": action or "unaccounted",
            "active_release": False,
            "geometry_replaced_edges": [],
            "attributes_applied_edges": [],
            "reason": "ambiguous_correspondence" if action == "ambiguous" else None,
        }
        dispositions[source_id] = disposition
        if action != "matched":
            continue
        candidates = match.get("candidates") or []
        evidence = match.get("evidence") or {}
        if len(candidates) != 1 or evidence.get("candidate_count", 1) != 1:
            disposition["reason"] = "non_unique_candidate_track"
            continue
        course_line = transform(to_metric, shape(feature["geometry"]))
        if course_line.geom_type != "LineString" or course_line.length <= 0:
            disposition["reason"] = "unsupported_course_geometry"
            continue
        for raw_edge_id in candidates[0].get("edge_ids", []):
            edge_id = _edge_id(raw_edge_id)
            if edge_id is None:
                continue
            u, v, key = edge_id
            if not graph.has_edge(u, v, key):
                continue
            edge = graph[u][v][key]
            old_line = _line_from_edge(graph, u, v, edge)
            if old_line is None:
                continue
            old_metric = transform(to_metric, old_line)
            node_u = transform(to_metric, Point(float(graph.nodes[u]["x"]),
                                                float(graph.nodes[u]["y"])))
            node_v = transform(to_metric, Point(float(graph.nodes[v]["x"]),
                                                float(graph.nodes[v]["y"])))
            along_u = course_line.project(node_u)
            along_v = course_line.project(node_v)
            offset_u = node_u.distance(course_line)
            offset_v = node_v.distance(course_line)
            span = abs(along_v - along_u)
            ratio = span / old_metric.length if old_metric.length > 0 else 0.0
            has_static_attributes = any(
                properties.get(key) is not None and properties.get(key) != ""
                for key in ("name", "road_class", "surface", "lane_count", "geographic_code")
            )
            if (has_static_attributes and not _grade_separated(edge)
                    and _course_edge_static_match(
                        old_metric, course_line, along_u, along_v, span, ratio, evidence
                    )):
                attribute_proposals[edge_id].append({
                    "source_id": source_id,
                    "properties": properties,
                })
            if (span <= 0.5 or offset_u > max_endpoint_offset_m
                    or offset_v > max_endpoint_offset_m or not 0.65 <= ratio <= 1.5):
                continue
            segment = substring(course_line, along_u, along_v)
            if segment.geom_type != "LineString" or segment.length <= 0:
                continue
            coords = list(segment.coords)
            coords[0] = (node_u.x, node_u.y)
            coords[-1] = (node_v.x, node_v.y)
            from shapely.geometry import LineString
            segment = LineString(coords)
            if not segment.is_valid or segment.length <= 0:
                continue
            proposals[edge_id].append({
                "source_id": source_id,
                "properties": properties,
                "line": segment,
            })

    for edge_id, edge_proposals in proposals.items():
        u, v, key = edge_id
        if len(edge_proposals) != 1:
            for proposal in edge_proposals:
                dispositions[proposal["source_id"]]["reason"] = "multiple_course_features_target_same_edge"
            continue
        proposal = edge_proposals[0]
        edge = graph[u][v][key]
        _set_geometry(graph, u, v, edge, proposal["line"], from_metric)
        _append_course_provenance(edge, proposal["source_id"])
        edge["course_geometry_source"] = "WHU coursework"
        edge["course_geometry_replaced"] = True
        # Geometry-specific evidence cannot be carried across a changed line.
        for key_name in ("slope_level", "scenery_level", "grade_abs_pct",
                         "grade_signed_pct", "elevation_start_m", "elevation_end_m"):
            edge.pop(key_name, None)
        record = dispositions[proposal["source_id"]]
        record["geometry_replaced_edges"].append([str(u), str(v), int(key)])
        record["active_release"] = True
        record["reason"] = "course_geometry_replaced_on_unique_edge_correspondence"

    for edge_id, edge_proposals in attribute_proposals.items():
        u, v, key = edge_id
        signatures = {
            json.dumps({
                name: proposal["properties"].get(name)
                for name in ("name", "road_class", "surface", "lane_count", "geographic_code")
            }, ensure_ascii=False, sort_keys=True, allow_nan=False)
            for proposal in edge_proposals
        }
        if len(signatures) != 1:
            for proposal in edge_proposals:
                dispositions[proposal["source_id"]].setdefault(
                    "attribute_conflict_edges", []
                ).append([str(u), str(v), int(key)])
            continue
        edge = graph[u][v][key]
        seen_source_ids = set()
        for proposal in edge_proposals:
            source_id = proposal["source_id"]
            if source_id in seen_source_ids:
                continue
            seen_source_ids.add(source_id)
            _append_course_attributes(edge, proposal["properties"], source_id)
            record = dispositions[source_id]
            edge_id_json = [str(u), str(v), int(key)]
            if edge_id_json not in record["attributes_applied_edges"]:
                record["attributes_applied_edges"].append(edge_id_json)
            record["active_release"] = True
            if not record["geometry_replaced_edges"]:
                record["reason"] = "course_static_attributes_applied_geometry_retained"

    for source_id, record in dispositions.items():
        if record["match_action"] == "matched" and not record["geometry_replaced_edges"]:
            if not record["attributes_applied_edges"] and record["reason"] is None:
                record["reason"] = "no_edge_fully_covered_within_endpoint_tolerance"
    return dispositions


def _course_edge_static_match(edge_line, course_line, along_start, along_end,
                              projected_span, length_ratio, evidence):
    """Allow static attribution on a close parallel match without moving geometry."""
    import math

    if (evidence.get("grade_separation_conflict")
            or int(evidence.get("candidate_count", 1) or 1) != 1):
        return False
    try:
        coverage = float(evidence.get("coverage", 1.0))
        median = float(evidence.get("median_distance_m", 0.0))
        p95 = float(evidence.get("p95_distance_m", 0.0))
        direction = float(evidence.get("direction_difference_deg", 0.0))
    except (TypeError, ValueError):
        return False
    if coverage < 0.8 or median > 5.0 or p95 > 8.0 or direction > 35.0:
        return False
    if projected_span <= 0.5 or not 0.35 <= length_ratio <= 1.8:
        return False
    if edge_line.length <= 0:
        return False
    sample_distances = [
        edge_line.interpolate(edge_line.length * fraction).distance(course_line)
        for fraction in (0.1, 0.3, 0.5, 0.7, 0.9)
    ]
    if max(sample_distances) > 5.0:
        return False

    edge_coords = list(edge_line.coords)
    if len(edge_coords) < 2:
        return False
    edge_angle = math.atan2(edge_coords[-1][1] - edge_coords[0][1],
                            edge_coords[-1][0] - edge_coords[0][0])
    middle = (along_start + along_end) / 2.0
    before = course_line.interpolate(max(0.0, middle - 0.5))
    after = course_line.interpolate(min(course_line.length, middle + 0.5))
    course_angle = math.atan2(after.y - before.y, after.x - before.x)
    angle_difference = abs((edge_angle - course_angle + math.pi / 2) % math.pi - math.pi / 2)
    return math.degrees(angle_difference) <= 35.0


def _split_edges(graph, edge_records, split_points, from_metric, to_metric,
                 allocate_node_id, old_edge_dispositions):
    from shapely.geometry import Point, LineString
    from shapely.ops import substring, transform

    node_for_split = {}
    for physical_id, records in split_points.items():
        for item in records:
            point_metric = Point(item["point_metric"])
            split_key = (physical_id, round(point_metric.x, 2), round(point_metric.y, 2))
            if split_key not in node_for_split:
                node_id = item["split_node_id"]
                wgs_point = transform(from_metric, point_metric)
                graph.add_node(node_id, x=float(wgs_point.x), y=float(wgs_point.y),
                               course_split_node="true", course_split_physical_id=physical_id)
                node_for_split[split_key] = node_id
            elif item["split_node_id"] != node_for_split[split_key]:
                item["split_node_id"] = node_for_split[split_key]

    for physical_id, records in split_points.items():
        physical = next(row for row in edge_records if row["physical_id"] == physical_id)
        for original_edge_id in physical["edge_ids"]:
            u, v, key = original_edge_id
            if not graph.has_edge(u, v, key):
                continue
            original_data = deepcopy(graph[u][v][key])
            original_line = _line_from_edge(graph, u, v, original_data)
            if original_line is None:
                continue
            metric_line = transform(to_metric, original_line)
            cuts = []
            for item in records:
                point = Point(item["point_metric"])
                measure = metric_line.project(point)
                if point.distance(metric_line) > 0.02:
                    continue
                if measure <= 0.1 or measure >= metric_line.length - 0.1:
                    continue
                cuts.append((measure, item["split_node_id"]))
            cuts.sort(key=lambda pair: pair[0])
            unique_cuts = []
            for measure, node_id in cuts:
                if unique_cuts and abs(measure - unique_cuts[-1][0]) <= 0.1:
                    continue
                unique_cuts.append((measure, node_id))
            if not unique_cuts:
                continue
            node_chain = [(0.0, u)] + unique_cuts + [(float(metric_line.length), v)]
            graph.remove_edge(u, v, key)
            new_edge_ids = []
            for (start_measure, start_node), (end_measure, end_node) in zip(node_chain, node_chain[1:]):
                segment = substring(metric_line, start_measure, end_measure)
                if segment.geom_type != "LineString" or segment.length <= 0:
                    continue
                coords = list(segment.coords)
                # Graph nodes store WGS-84. Do not pass them through the
                # inverse metric transform: that expects EPSG:4547 and turns
                # ordinary campus coordinates into points near 109E, 0N.
                start_metric = transform(
                    to_metric,
                    Point(float(graph.nodes[start_node]["x"]),
                          float(graph.nodes[start_node]["y"])),
                )
                end_metric = transform(
                    to_metric,
                    Point(float(graph.nodes[end_node]["x"]),
                          float(graph.nodes[end_node]["y"])),
                )
                coords[0] = (start_metric.x, start_metric.y)
                coords[-1] = (end_metric.x, end_metric.y)
                segment = LineString(coords)
                attrs = deepcopy(original_data)
                attrs["geometry"] = transform(from_metric, segment).wkt
                attrs["length"] = float(segment.length)
                attrs["edge_split_from"] = json.dumps([str(u), str(v), key])
                new_key = graph.add_edge(start_node, end_node, **attrs)
                new_edge_ids.append([str(start_node), str(end_node), int(new_key)])
            old_edge_dispositions[(str(u), str(v), int(key))] = {
                "edge_id": [str(u), str(v), int(key)],
                "disposition": "split",
                "replacement_edge_ids": new_edge_ids,
                "reason": "course_endpoint_on_existing_osm_edge",
            }


def _add_course_edge(graph, u, v, metric_line, properties, source_id,
                     from_metric):
    from shapely.ops import transform

    wgs_line = transform(from_metric, metric_line)
    road_class = properties.get("road_class")
    highway = "footway" if road_class == "pedestrian" else "service"
    ref = _source_ref(source_id)
    attrs = {
        "length": float(metric_line.length),
        "geometry": wgs_line.wkt,
        "highway": highway,
        "course_road_class": road_class,
        "course_road_class_raw": properties.get("road_class_raw"),
        "course_surface": properties.get("surface"),
        "course_surface_raw": properties.get("surface_raw"),
        "course_lane_count": properties.get("lane_count"),
        "course_geographic_code": properties.get("geographic_code"),
        "course_raw_source_properties": json.dumps(
            properties.get("raw_source_properties") or {}, ensure_ascii=False,
            sort_keys=True, allow_nan=False,
        ),
        "course_source_id": source_id,
        "course_source_ids": json.dumps([source_id], ensure_ascii=False),
        "course_geometry_source": "WHU coursework",
        "source_refs": json.dumps([ref], ensure_ascii=False, sort_keys=True),
        "verification_status": "source_only",
        "access_metadata_status": "unknown",
        "current_status": "unknown",
        "source_priority": "course_wins_static_conflicts",
        "allowed_modes": json.dumps(["walk"]),
    }
    if properties.get("name"):
        attrs["name"] = str(properties["name"])
        attrs["course_name_source"] = "course"
    return graph.add_edge(u, v, **attrs)


def fuse_network(old_graph, course_features, match_report, *, attachment_tolerance_m=2.0):
    """Fuse only unique matches and well-anchored new course components.

    Ambiguous features remain in the review layer. New components are published
    only when two distinct endpoints attach to existing topology, and grade-
    separated edges cannot be split or connected by a course endpoint.
    """
    from pyproj import Transformer
    from shapely.geometry import Point, shape
    from shapely.ops import transform
    from shapely.strtree import STRtree

    graph = deepcopy(old_graph)
    to_metric = Transformer.from_crs("EPSG:4326", "EPSG:4547", always_xy=True).transform
    from_metric = Transformer.from_crs("EPSG:4547", "EPSG:4326", always_xy=True).transform
    match_rows = match_report.get("matches", []) if isinstance(match_report, dict) else match_report
    match_by_source = {row["source_id"]: row for row in match_rows if row.get("source_id")}
    old_edge_ids = [(str(u), str(v), int(k)) for u, v, k in old_graph.edges(keys=True)]
    old_edge_dispositions = {
        tuple(edge_id): {"edge_id": list(edge_id), "disposition": "unchanged",
                         "replacement_edge_ids": []}
        for edge_id in old_edge_ids
    }

    course_dispositions = _replace_matched_edges(
        graph, course_features, match_by_source, to_metric, from_metric
    )
    # Keep an explicit migration audit for any legacy directed edge whose
    # geometry was replaced in place.  Edge identity is intentionally stable,
    # so this is the only place the report can show that its payload changed.
    for edge_id in old_graph.edges(keys=True):
        u, v, key = edge_id
        if graph.has_edge(u, v, key) and graph[u][v][key].get("course_geometry_replaced"):
            disposition_key = (str(u), str(v), int(key))
            old_edge_dispositions[disposition_key] = {
                "edge_id": [str(u), str(v), int(key)],
                "disposition": "course_geometry_replaced",
                "replacement_edge_ids": [[str(u), str(v), int(key)]],
                "reason": "unique_course_correspondence_replaced_geometry_in_place",
            }
    new_features = [feature for feature in course_features
                    if match_by_source.get((feature.get("properties") or {}).get("source_id"), {})
                    .get("action") == "new_candidate"
                    and (feature.get("properties") or {}).get("road_class") != "construction"]

    endpoint_rows = []
    metric_lines = {}
    for feature in new_features:
        source_id = feature["properties"]["source_id"]
        line = transform(to_metric, shape(feature["geometry"]))
        if line.geom_type != "LineString" or line.length <= 0:
            course_dispositions[source_id]["reason"] = "unsupported_course_geometry"
            continue
        metric_lines[source_id] = line
        endpoint_rows.extend([
            {"source_id": source_id, "side": 0, "point_metric": Point(line.coords[0])},
            {"source_id": source_id, "side": 1, "point_metric": Point(line.coords[-1])},
        ])
    endpoint_groups = _cluster_course_endpoints(endpoint_rows)
    endpoint_cluster = {}
    cluster_points = {}
    for cluster_id, group in enumerate(endpoint_groups):
        for row in group:
            endpoint_cluster[row["source_id"], row["side"]] = cluster_id
        # The first source coordinate is retained; members are within 15 cm.
        cluster_points[cluster_id] = group[0]["point_metric"]
    components = _course_components(new_features, endpoint_cluster)

    node_points, node_refs = _graph_points(graph, to_metric)
    node_tree = STRtree(node_points) if node_points else None
    edge_records = _physical_edges(graph, to_metric)
    edge_geometries = [row["geometry"] for row in edge_records]
    edge_tree = STRtree(edge_geometries) if edge_geometries else None
    edge_index = {id(geometry): index for index, geometry in enumerate(edge_geometries)}
    node_id_next = max([int(node) for node in graph.nodes if str(node).lstrip("-").isdigit()] or [0]) + 1

    def allocate_node_id():
        nonlocal node_id_next
        value = node_id_next
        node_id_next += 1
        return value

    components_to_add = []
    split_points = defaultdict(list)
    split_node_ids = {}
    for component in components:
        component_source_ids = [feature["properties"]["source_id"] for feature in component]
        component_clusters = sorted({endpoint_cluster[source_id, side]
                                     for source_id in component_source_ids for side in (0, 1)})
        attachments = {}
        for cluster_id in component_clusters:
            point = cluster_points[cluster_id]
            attachments[cluster_id] = _nearest_attachment(
                point, graph, node_points, node_refs, node_tree,
                edge_records, edge_tree, edge_geometries, edge_index,
                attachment_tolerance_m,
            )
        anchor_details = {}
        for cluster_id, attachment in attachments.items():
            if attachment["kind"] == "node":
                signature = ("node", str(attachment["node_id"]))
            elif attachment["kind"] == "edge":
                p = attachment["point_metric"]
                signature = ("edge", attachment["physical_id"],
                             round(p[0], 1), round(p[1], 1))
            else:
                continue
            anchor = anchor_details.setdefault(signature, {
                "attachment": deepcopy(attachment),
                "cluster_ids": [],
                "source_endpoints": [],
            })
            anchor["cluster_ids"].append(cluster_id)
            for feature in component:
                properties = feature["properties"]
                source_id = properties["source_id"]
                for side in (0, 1):
                    if endpoint_cluster[source_id, side] == cluster_id:
                        anchor["source_endpoints"].append({
                            "source_id": source_id,
                            "source_row": properties.get("source_row"),
                            "side": "start" if side == 0 else "end",
                        })
        component_anchors = sorted(anchor_details.values(), key=lambda row: (
            row["attachment"]["kind"],
            str(row["attachment"].get("node_id", row["attachment"].get("physical_id", ""))),
        ))
        component_source_ids = sorted(feature["properties"]["source_id"]
                                      for feature in component)
        if len(anchor_details) < 2:
            for feature in component:
                source_id = feature["properties"]["source_id"]
                record = course_dispositions[source_id]
                record.update({
                    "active_release": False,
                    "reason": "insufficient_safe_anchors",
                    "component_source_ids": component_source_ids,
                    "component_anchor_count": len(anchor_details),
                    "component_anchors": component_anchors,
                    "endpoint_attachments": [attachments[endpoint_cluster[source_id, side]]
                                              for side in (0, 1)],
                })
            continue
        components_to_add.append({
            "features": component,
            "clusters": component_clusters,
            "attachments": attachments,
            "source_ids": component_source_ids,
            "anchor_count": len(anchor_details),
            "anchors": component_anchors,
        })

    # Allocate split nodes and source-side nodes only for releaseable components.
    cluster_node = {}
    connector_specs = []
    for component in components_to_add:
        for cluster_id in component["clusters"]:
            attachment = component["attachments"][cluster_id]
            point = cluster_points[cluster_id]
            if attachment["kind"] == "node":
                cluster_node[cluster_id] = attachment["node_id"]
            elif attachment["kind"] == "edge":
                point_metric = attachment["point_metric"]
                split_key = (attachment["physical_id"], round(point_metric[0], 2),
                             round(point_metric[1], 2))
                if split_key not in split_node_ids:
                    split_node_ids[split_key] = allocate_node_id()
                split_node_id = split_node_ids[split_key]
                split_points[attachment["physical_id"]].append({
                    "point_metric": point_metric,
                    "split_node_id": split_node_id,
                })
                if attachment["gap_m"] <= 0.05:
                    cluster_node[cluster_id] = split_node_id
                else:
                    course_node_id = allocate_node_id()
                    wgs_point = transform(from_metric, point)
                    graph.add_node(course_node_id, x=float(wgs_point.x), y=float(wgs_point.y),
                                   course_source_node="true")
                    cluster_node[cluster_id] = course_node_id
                    connector_specs.append({
                        "cluster_id": cluster_id,
                        "course_node_id": course_node_id,
                        "split_node_id": split_node_id,
                        "source_ids": sorted({row["source_id"] for feature in component["features"]
                                              for row in endpoint_rows
                                              if row["source_id"] == feature["properties"]["source_id"]
                                              and endpoint_cluster[row["source_id"], row["side"]] == cluster_id}),
                        "point_metric": attachment["point_metric"],
                        "course_point_metric": point,
                        "gap_m": attachment["gap_m"],
                    })
            else:
                course_node_id = allocate_node_id()
                wgs_point = transform(from_metric, point)
                graph.add_node(course_node_id, x=float(wgs_point.x), y=float(wgs_point.y),
                               course_source_node="true")
                cluster_node[cluster_id] = course_node_id

    for component in components_to_add:
        connector_split_nodes = {
            spec["cluster_id"]: spec["split_node_id"]
            for spec in connector_specs
        }
        for anchor in component["anchors"]:
            release_nodes = []
            for cluster_id in anchor["cluster_ids"]:
                attachment = component["attachments"][cluster_id]
                if attachment["kind"] == "node":
                    node_id = cluster_node[cluster_id]
                elif cluster_id in connector_split_nodes:
                    node_id = connector_split_nodes[cluster_id]
                else:
                    node_id = cluster_node[cluster_id]
                if node_id not in release_nodes:
                    release_nodes.append(node_id)
            anchor["release_node_ids"] = sorted(map(str, release_nodes))

    if split_points:
        _split_edges(graph, edge_records, split_points, from_metric, to_metric,
                     allocate_node_id, old_edge_dispositions)
        # _split_edges assigned node ids as split_node_id already; ensure they
        # exist before connector or coursework edges are inserted.

    # Add short, explicit gap links where trusted source endpoints fall just
    # off an existing road centerline; keep them separate from road geometry.
    for connector in connector_specs:
        from shapely.geometry import LineString
        line = LineString([connector["course_point_metric"], connector["point_metric"]])
        source_id = connector["source_ids"][0] if connector["source_ids"] else "course:connector"
        attrs = {
            "highway": "footway",
            "length": float(line.length),
            "geometry": transform(from_metric, line).wkt,
            "course_connector": "true",
            "course_source_ids": json.dumps(connector["source_ids"], ensure_ascii=False),
            "source_refs": json.dumps([_source_ref(item) for item in connector["source_ids"]],
                                       ensure_ascii=False, sort_keys=True),
            "allowed_modes": json.dumps(["walk"]),
            "verification_status": "source_only",
            "connector_gap_m": float(connector["gap_m"]),
            "course_geometry_source": "WHU coursework endpoint correspondence",
        }
        graph.add_edge(connector["course_node_id"], connector["split_node_id"], **deepcopy(attrs))
        reverse = LineString(list(line.coords)[::-1])
        reverse_attrs = deepcopy(attrs)
        reverse_attrs["geometry"] = transform(from_metric, reverse).wkt
        graph.add_edge(connector["split_node_id"], connector["course_node_id"], **reverse_attrs)

    for component in components_to_add:
        for feature in component["features"]:
            properties = feature["properties"]
            source_id = properties["source_id"]
            source_line = metric_lines[source_id]
            start_cluster = endpoint_cluster[source_id, 0]
            end_cluster = endpoint_cluster[source_id, 1]
            start_node = cluster_node[start_cluster]
            end_node = cluster_node[end_cluster]
            start_point = transform(to_metric, Point(float(graph.nodes[start_node]["x"]),
                                                      float(graph.nodes[start_node]["y"])))
            end_point = transform(to_metric, Point(float(graph.nodes[end_node]["x"]),
                                                    float(graph.nodes[end_node]["y"])))
            coords = list(source_line.coords)
            coords[0] = (start_point.x, start_point.y)
            coords[-1] = (end_point.x, end_point.y)
            from shapely.geometry import LineString
            source_line = LineString(coords)
            forward_key = _add_course_edge(graph, start_node, end_node, source_line,
                                           properties, source_id, from_metric)
            reverse_line = LineString(list(source_line.coords)[::-1])
            reverse_key = _add_course_edge(graph, end_node, start_node, reverse_line,
                                           properties, source_id, from_metric)
            record = course_dispositions[source_id]
            record.update({
                "active_release": True,
                "reason": "new_course_geometry_connected_at_two_existing_network_anchors",
                "component_source_ids": component["source_ids"],
                "component_anchor_count": component["anchor_count"],
                "component_anchors": component["anchors"],
                "added_edge_ids": [[str(start_node), str(end_node), int(forward_key)],
                                   [str(end_node), str(start_node), int(reverse_key)]],
                "endpoint_attachments": [
                    component["attachments"][start_cluster],
                    component["attachments"][end_cluster],
                ],
            })

    for feature in course_features:
        source_id = feature["properties"]["source_id"]
        course_dispositions.setdefault(source_id, {
            "source_id": source_id,
            "source_row": feature["properties"].get("source_row"),
            "match_action": match_by_source.get(source_id, {}).get("action", "unaccounted"),
            "active_release": False,
            "geometry_replaced_edges": [],
            "attributes_applied_edges": [],
            "reason": "not_activated",
        })

    migration = {
        "schema_version": 1,
        "priority_policy": "course_geometry_and_nonempty_static_attributes_win_on_proven_match",
        "unknown_course_values_policy": "preserve_existing_values",
        "elevation_policy": "course_z_all_zero_is_unavailable",
        "new_course_mode_policy": "walk_only_until_nonwalk_access_is_verified",
        "attachment_tolerance_m": attachment_tolerance_m,
        "old_graph_counts": {"nodes": old_graph.number_of_nodes(),
                             "edges": old_graph.number_of_edges()},
        "new_graph_counts": {"nodes": graph.number_of_nodes(),
                             "edges": graph.number_of_edges()},
        "course_feature_dispositions": sorted(
            course_dispositions.values(), key=lambda row: (
                row.get("source_row") if row.get("source_row") is not None else -1,
                row["source_id"],
            )
        ),
        "old_edge_dispositions": sorted(
            old_edge_dispositions.values(),
            key=lambda row: (row["edge_id"][0], row["edge_id"][1], row["edge_id"][2]),
        ),
    }
    return graph, migration
