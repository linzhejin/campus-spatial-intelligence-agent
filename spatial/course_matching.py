"""Conservative correspondence between coursework roads and the OSM graph."""

from __future__ import annotations

import ast
from collections import defaultdict
from hashlib import sha256
from numbers import Integral
import json
import math


def classify_match(evidence: dict) -> str:
    """Return matched only when geometry, topology and source evidence agree.

    Values between clear correspondence and clear absence stay ambiguous. This
    prevents a nearby bridge, parallel road or partial overlap from being
    silently treated as the same traversable road.
    """
    try:
        candidate_count = int(evidence.get("candidate_count") or 0)
    except (TypeError, ValueError):
        candidate_count = 0
    coverage = _finite_number(evidence.get("coverage"), default=0.0)
    median = _finite_number(evidence.get("median_distance_m"), default=math.inf)
    p95 = _finite_number(evidence.get("p95_distance_m"), default=math.inf)
    direction = _finite_number(evidence.get("direction_difference_deg"), default=math.inf)
    name_relation = evidence.get("name_relation", "unknown")

    if candidate_count == 0:
        if coverage < 0.25 or median > 20.0:
            return "new_candidate"
        return "ambiguous"
    if (candidate_count != 1
            or evidence.get("grade_separation_conflict")
            or name_relation == "conflict"):
        return "ambiguous"
    if coverage < 0.25 or median > 20.0:
        return "new_candidate"
    endpoint_correspondence = bool(evidence.get("endpoint_correspondence"))
    if (coverage >= 0.90 and median <= 3.0 and p95 <= 8.0
            and direction <= 35.0 and endpoint_correspondence
            and name_relation in {"same", "compatible", "unknown"}):
        return "matched"
    return "ambiguous"


def _finite_number(value, default):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _rounded_or_none(value, digits=2):
    number = _finite_number(value, default=None)
    return round(number, digits) if number is not None else None


def _parse(value):
    if isinstance(value, str):
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return value
    return value


def _truthy_tag(value):
    value = _parse(value)
    if isinstance(value, (list, tuple)):
        return any(_truthy_tag(item) for item in value)
    return value not in (None, False, 0, "", "no", "false", "0")


def _way_ids(data):
    value = _parse(data.get("osmid"))
    if value is None:
        refs = _parse(data.get("source_refs") or [])
        if isinstance(refs, dict):
            refs = [refs]
        return sorted({str(ref.get("id")) for ref in refs or []
                       if isinstance(ref, dict) and ref.get("id")})
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return sorted({f"way/{item}" if not str(item).startswith("way/") else str(item)
                   for item in values if item is not None})


def _edge_geometry(graph, u, v, data):
    from shapely import wkt
    from shapely.geometry import LineString

    geometry = data.get("geometry")
    geometry = _parse(geometry)
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
        from shapely.ops import linemerge
        geometry = linemerge(geometry)
    if geometry.geom_type != "LineString" or geometry.is_empty:
        return None
    return geometry


def _canonical_physical_key(geometry, project):
    coords = [(round(float(x), 3), round(float(y), 3)) for x, y, *rest in geometry.coords]
    reversed_coords = list(reversed(coords))
    canonical = min(coords, reversed_coords)
    return sha256(json.dumps(canonical, separators=(",", ":")).encode()).hexdigest()


def _axis_direction(line):
    coords = list(line.coords)
    first = coords[0]
    last = coords[-1]
    return math.degrees(math.atan2(last[1] - first[1], last[0] - first[0])) % 180.0


def _angle_difference(a, b):
    diff = abs((a - b) % 180.0)
    return min(diff, 180.0 - diff)


def _direction_at(line, along_m):
    length = float(line.length)
    delta = min(2.0, max(0.25, length / 4.0))
    before = max(0.0, min(length, along_m - delta))
    after = max(0.0, min(length, along_m + delta))
    if after - before < 0.1:
        return _axis_direction(line)
    first = line.interpolate(before)
    last = line.interpolate(after)
    return math.degrees(math.atan2(last.y - first.y, last.x - first.x)) % 180.0


def _local_direction_difference(course_line, candidate_line):
    from shapely.ops import nearest_points

    point_course, point_candidate = nearest_points(course_line, candidate_line)
    return _angle_difference(
        _direction_at(course_line, course_line.project(point_course)),
        _direction_at(candidate_line, candidate_line.project(point_candidate)),
    )


def _sample_line(line, spacing_m=5.0):
    from shapely.geometry import Point

    count = max(2, int(math.ceil(line.length / spacing_m)) + 1)
    return [line.interpolate(index / (count - 1), normalized=True)
            for index in range(count)]


def _group_geometry(rows):
    from shapely.ops import unary_union

    return unary_union([row["geometry"] for row in rows])


def _topological_components(rows):
    """Join fragments only when their original routing graph shares a node."""
    if not rows:
        return []
    parents = list(range(len(rows)))

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    owner_by_node = {}
    for index, row in enumerate(rows):
        for node in row["endpoint_nodes"]:
            previous = owner_by_node.get(node)
            if previous is None:
                owner_by_node[node] = index
            else:
                union(index, previous)
    groups = defaultdict(list)
    for index, row in enumerate(rows):
        groups[find(index)].append(row)
    return list(groups.values())


def _line_parts(geometry):
    if geometry.geom_type == "LineString":
        return [geometry]
    if geometry.geom_type == "MultiLineString":
        return list(geometry.geoms)
    if geometry.geom_type == "GeometryCollection":
        return [part for sub in geometry.geoms for part in _line_parts(sub)]
    return []


def _grade_separation_conflict(course_line, nearby_rows, course_name):
    from shapely.geometry import Point

    if course_name and any(word in course_name for word in ("隧道", "天桥", "楼梯")):
        return False
    for row in nearby_rows:
        data = row["data"]
        if not (_truthy_tag(data.get("bridge")) or _truthy_tag(data.get("tunnel"))
                or str(_parse(data.get("layer", "0"))) not in {"0", "None", ""}):
            continue
        intersection = course_line.intersection(row["geometry"])
        if intersection.is_empty:
            continue
        if intersection.geom_type in {"Point", "MultiPoint"}:
            points = ([intersection] if intersection.geom_type == "Point"
                      else list(intersection.geoms))
            for point in points:
                if min(point.distance(Point(course_line.coords[0])),
                       point.distance(Point(course_line.coords[-1]))) < 2.0:
                    continue
                if _angle_difference(_axis_direction(course_line),
                                     _axis_direction(row["geometry"])) >= 15.0:
                    return True
    return False


def _point_to_node_distance(point, node_points, node_tree, node_by_geom_id):
    if not node_points:
        return math.inf
    nearest = node_tree.nearest(point)
    if isinstance(nearest, Integral) or getattr(nearest, "dtype", None) is not None:
        return point.distance(node_points[int(nearest)])
    if id(nearest) in node_by_geom_id:
        return point.distance(node_points[node_by_geom_id[id(nearest)]])
    return point.distance(nearest)


def _metrics(course_line, group_rows, node_points, node_tree, node_by_geom_id,
             name_relation):
    from shapely.geometry import Point

    group_geometry = _group_geometry(group_rows)
    samples = _sample_line(course_line)
    distances = [point.distance(group_geometry) for point in samples]
    sorted_distances = sorted(distances)
    p95_index = min(len(sorted_distances) - 1, int(math.ceil(0.95 * len(sorted_distances)) - 1))
    directions = [_local_direction_difference(course_line, row["geometry"])
                  for row in group_rows if row["geometry"].length > 0]
    direction = sorted(directions)[len(directions) // 2] if directions else math.inf
    start_point = Point(course_line.coords[0])
    end_point = Point(course_line.coords[-1])
    endpoint_track_correspondence = (
        start_point.distance(group_geometry) <= 5.0
        and end_point.distance(group_geometry) <= 5.0
    )
    endpoint_node_correspondence = (
        _point_to_node_distance(start_point, node_points, node_tree,
                                node_by_geom_id) <= 8.0
        and _point_to_node_distance(end_point, node_points, node_tree,
                                    node_by_geom_id) <= 8.0
    )
    return {
        "coverage": sum(distance <= 8.0 for distance in distances) / len(distances),
        "median_distance_m": sorted_distances[len(sorted_distances) // 2],
        "p95_distance_m": sorted_distances[p95_index],
        "direction_difference_deg": direction,
        "endpoint_correspondence": (endpoint_node_correspondence
                                     or endpoint_track_correspondence),
        "endpoint_node_correspondence": endpoint_node_correspondence,
        "endpoint_track_correspondence": endpoint_track_correspondence,
        "name_relation": name_relation,
    }


def match_course_features(course_features, graph, *, corridor_m=10.0):
    """Match every normalized course feature to OSM conservatively.

    Candidate fragments are joined by shared graph nodes, which supports a
    single course line split across multiple OSM ways. Parallel components
    remain separate candidates. The output accounts for every input row while
    only high-confidence, unique tracks are marked as matched.
    """
    from pyproj import Transformer
    from shapely.geometry import LineString, shape
    from shapely.ops import transform
    from shapely.strtree import STRtree

    to_metric = Transformer.from_crs("EPSG:4326", "EPSG:4547", always_xy=True).transform
    physical = {}
    node_points = []
    node_refs = []
    for node, data in graph.nodes(data=True):
        if data.get("x") is None or data.get("y") is None:
            continue
        from shapely.geometry import Point
        point = transform(to_metric, Point(float(data["x"]), float(data["y"])))
        node_points.append(point)
        node_refs.append(str(node))

    for u, v, key, data in graph.edges(keys=True, data=True):
        geometry = _edge_geometry(graph, u, v, data)
        if geometry is None:
            continue
        projected = transform(to_metric, geometry)
        physical_key = _canonical_physical_key(projected, to_metric)
        record = physical.get(physical_key)
        if record is None:
            record = {
                "physical_id": physical_key,
                "geometry": projected,
                "edge_ids": [],
                "way_ids": set(),
                "endpoint_nodes": set(),
                "names": set(),
                "data": data,
            }
            physical[physical_key] = record
        record["edge_ids"].append([str(u), str(v), int(key)])
        record["endpoint_nodes"].update((str(u), str(v)))
        record["way_ids"].update(_way_ids(data))
        raw_name = _parse(data.get("name"))
        names = raw_name if isinstance(raw_name, (list, tuple)) else [raw_name]
        record["names"].update(str(name).strip() for name in names if name and str(name).strip())

    rows = list(physical.values())
    if not rows:
        raise ValueError("OSM graph has no usable line geometry")
    tree = STRtree([row["geometry"] for row in rows])
    row_by_geom_id = {id(row["geometry"]): index for index, row in enumerate(rows)}
    node_tree = STRtree(node_points) if node_points else None
    node_by_geom_id = {id(point): index for index, point in enumerate(node_points)}

    name_groups = defaultdict(list)
    for row in rows:
        for name in row["names"]:
            name_groups[name.casefold()].append(row)

    matched = []
    review_features = []
    for feature in course_features:
        source_props = feature.get("properties") or {}
        source_id = source_props.get("source_id")
        if not source_id:
            raise ValueError("Every course line must have a stable source_id")
        course_geom = shape(feature["geometry"])
        course_line = transform(to_metric, course_geom)
        if course_line.geom_type == "MultiLineString":
            from shapely.ops import linemerge
            course_line = linemerge(course_line)
        if course_line.geom_type != "LineString" or course_line.is_empty:
            raise ValueError(f"Unsupported course geometry for {source_id}")
        course_name = source_props.get("name")
        corridor = course_line.buffer(corridor_m)
        tree_result = tree.query(corridor)
        nearby_indices = []
        for item in tree_result:
            if isinstance(item, Integral) or getattr(item, "dtype", None) is not None:
                nearby_indices.append(int(item))
            else:
                index = row_by_geom_id.get(id(item))
                if index is not None:
                    nearby_indices.append(index)
        nearby_rows = [rows[index] for index in set(nearby_indices)
                       if rows[index]["geometry"].distance(course_line) <= corridor_m]

        # Filter nearby cross streets by their local tangent. The selected
        # fragments are then chained only through shared routing nodes.
        aligned_rows = [row for row in nearby_rows
                        if _local_direction_difference(course_line, row["geometry"]) <= 45.0]
        candidates = []
        if course_name:
            course_key = str(course_name).casefold()
            exact_named_rows = [row for row in name_groups.get(course_key, [])
                                if row["geometry"].distance(course_line) <= corridor_m]
            named_ids = {row["physical_id"] for row in exact_named_rows}
            for component in _topological_components(exact_named_rows):
                candidates.append((f"name:{course_key}:{min(r['physical_id'] for r in component)[:12]}",
                                   component, "same"))
        else:
            course_key = None
            named_ids = set()
        other_rows = [row for row in aligned_rows
                      if row["physical_id"] not in named_ids]
        for component in _topological_components(other_rows):
            group_names = {name.casefold() for row in component for name in row["names"]}
            if course_key and course_key in group_names:
                relation = "same"
            elif course_key and group_names:
                relation = "conflict"
            else:
                relation = "unknown"
            candidates.append((f"component:{min(r['physical_id'] for r in component)[:12]}",
                               component, relation))

        candidate_records = []
        examined_tracks = []
        for group_id, group_rows, relation in candidates:
            metrics = _metrics(course_line, group_rows, node_points, node_tree,
                               node_by_geom_id, relation)
            qualifies = (metrics["coverage"] >= 0.80
                         and metrics["median_distance_m"] <= 8.0
                         and metrics["p95_distance_m"] <= 18.0
                         and metrics["direction_difference_deg"] <= 35.0)
            edge_ids = sorted({tuple(edge_id) for row in group_rows
                               for edge_id in row["edge_ids"]})
            track = {
                "candidate_id": group_id,
                "edge_ids": [list(edge_id) for edge_id in edge_ids],
                "qualifies": qualifies,
                "rejection_reasons": [] if qualifies else [
                    key for key, failed in (
                        ("low_coverage", metrics["coverage"] < 0.80),
                        ("median_distance", metrics["median_distance_m"] > 8.0),
                        ("p95_distance", metrics["p95_distance_m"] > 18.0),
                        ("direction_difference", metrics["direction_difference_deg"] > 35.0),
                    ) if failed
                ],
                **metrics,
            }
            examined_tracks.append(track)
            if qualifies:
                candidate_records.append(track)
        candidate_records.sort(key=lambda item: (
            item["median_distance_m"], item["candidate_id"]
        ))

        # Report nearest-edge geometry even when no complete way chain matched.
        if nearby_rows:
            all_metrics = _metrics(course_line, nearby_rows, node_points, node_tree,
                                   node_by_geom_id, "unknown")
        else:
            all_metrics = {
                "coverage": 0.0,
                "median_distance_m": None,
                "p95_distance_m": None,
                "direction_difference_deg": None,
                "endpoint_correspondence": False,
                "name_relation": "unknown",
            }
        evidence = {
            **(candidate_records[0] if len(candidate_records) == 1 else all_metrics),
            "candidate_count": len(candidate_records),
            "grade_separation_conflict": _grade_separation_conflict(
                course_line, nearby_rows, course_name
            ),
        }
        status = classify_match(evidence)
        record = {
            "source_id": source_id,
            "source_row": source_props.get("source_row"),
            "course_name": course_name,
            "action": status,
            "evidence": evidence,
            "candidates": candidate_records,
            "candidate_tracks_examined": examined_tracks,
            "nearby_osm_edge_count": len(nearby_rows),
            "review_reason": (
                "unique_high_confidence_correspondence" if status == "matched"
                else "no_osm_geometry_within_20m" if status == "new_candidate"
                else "overlap_or_identity_is_ambiguous"
            ),
        }
        matched.append(record)
        review_props = {
            "source_id": source_id,
            "source_row": source_props.get("source_row"),
            "course_name": course_name,
            "match_status": status,
            "candidate_count": evidence["candidate_count"],
            "coverage": round(float(evidence["coverage"]), 4),
            "median_distance_m": _rounded_or_none(evidence["median_distance_m"]),
            "p95_distance_m": _rounded_or_none(evidence["p95_distance_m"]),
            "grade_separation_conflict": evidence["grade_separation_conflict"],
            "review_reason": record["review_reason"],
        }
        review_features.append({
            "type": "Feature",
            "id": source_id,
            "geometry": feature["geometry"],
            "properties": review_props,
        })
    return {
        "schema_version": 1,
        "source_crs": "EPSG:4326",
        "comparison_crs": "EPSG:4547",
        "corridor_m": corridor_m,
        "graph_node_count": graph.number_of_nodes(),
        "graph_edge_count": graph.number_of_edges(),
        "course_feature_count": len(course_features),
        "matches": matched,
        "review_geojson": {"type": "FeatureCollection", "features": review_features},
    }
