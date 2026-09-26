"""Build a dense, source-scoped routing graph from the local OSM candidate layer.

Importing this module is side-effect free. Running it writes a candidate GraphML
cache only after the source layer has been validated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import networkx as nx
from pyproj import Geod

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from spatial.osm_access import edge_access, node_access  # noqa: E402

GEOD = Geod(ellps="WGS84")
MODES = ("walk", "bike", "drive")
EXCLUDED_AREAS = {"yes", "1", "true"}


def _raw_tags(tags: dict) -> dict:
    """Return a GraphML-friendly copy while preserving OSM's source values."""
    return {str(key): str(value) for key, value in (tags or {}).items()}


def _mode_metadata(tags: dict, forward: bool) -> tuple[list[str], list[str], dict[str, str]]:
    directed = {**tags, "osm_way_forward": forward}
    allowed, blocked, advisories = [], [], {}
    for mode in MODES:
        ok, advisory = edge_access(directed, mode)
        (allowed if ok else blocked).append(mode)
        if advisory:
            advisories[mode] = advisory
    return allowed, blocked, advisories


def build_from_candidates(roads_doc: dict, nodes_doc: dict, source_sha256: str = ""):
    """Build an unsimplified directed MultiDiGraph from complete OSM ways.

    Each source way's OSM node sequence becomes graph nodes, retaining the
    geometry vertices needed to keep the rendered route on the selected edge.
    Way IDs are edge keys, so parallel OSM ways between the same nodes remain
    distinct. The graph carries no inferred slope or scenery annotations.
    """
    graph = nx.MultiDiGraph(
        crs="EPSG:4326",
        source="OpenStreetMap",
        source_license="ODbL-1.0",
        source_sha256=source_sha256,
        annotation_policy="source_scoped_only",
    )
    stats = {"ways_added": 0, "segments_added": 0, "excluded_highway_areas": 0,
             "invalid_ways": 0, "access_nodes_attached": 0}

    access_nodes = {}
    for feature in nodes_doc.get("features", []):
        props = feature.get("properties", {})
        raw_id = props.get("osm_id")
        geom = feature.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if raw_id is None or len(coords) < 2:
            continue
        node_id = int(raw_id)
        access_nodes[node_id] = _raw_tags(props.get("tags"))

    for feature in roads_doc.get("features", []):
        props = feature.get("properties", {})
        way_id = props.get("osm_id")
        refs = props.get("node_refs") or []
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        tags = _raw_tags(props.get("tags"))
        if tags.get("area", "").lower() in EXCLUDED_AREAS:
            stats["excluded_highway_areas"] += 1
            continue
        if way_id is None or len(refs) < 2 or len(refs) != len(coords):
            stats["invalid_ways"] += 1
            continue
        try:
            way_id = int(way_id)
            node_ids = [int(node_id) for node_id in refs]
            points = [(float(point[0]), float(point[1])) for point in coords]
        except (TypeError, ValueError, IndexError):
            stats["invalid_ways"] += 1
            continue

        for node_id, (lng, lat) in zip(node_ids, points):
            if node_id not in graph:
                graph.add_node(node_id, x=lng, y=lat)
            else:
                # Keep coordinates from the full source way. Reject conflicting
                # coordinates rather than silently moving a shared OSM node.
                old = graph.nodes[node_id]
                if (abs(float(old.get("x", lng)) - lng) > 1e-7
                        or abs(float(old.get("y", lat)) - lat) > 1e-7):
                    stats["invalid_ways"] += 1
                    break
        else:
            for index, (u, v) in enumerate(zip(node_ids, node_ids[1:])):
                lng1, lat1 = points[index]
                lng2, lat2 = points[index + 1]
                _, _, length = GEOD.inv(lng1, lat1, lng2, lat2)
                for forward, start, end in ((True, u, v), (False, v, u)):
                    allowed, blocked, advisories = _mode_metadata(tags, forward)
                    attrs = {
                        **tags,
                        "osmid": way_id,
                        "osm_version": int(props.get("osm_version") or 0),
                        "osm_timestamp": str(props.get("osm_timestamp") or ""),
                        "osm_way_forward": forward,
                        "length": float(length),
                        "allowed_modes": allowed,
                        "blocked_modes": blocked,
                        "access_advisories": advisories,
                        "source_refs": [{"source": "OpenStreetMap",
                                         "id": f"way/{way_id}",
                                         "license": "ODbL-1.0"}],
                        "verification_status": "source_only",
                    }
                    graph.add_edge(start, end, key=way_id, **attrs)
                    stats["segments_added"] += 1
            stats["ways_added"] += 1

    # Physical barriers block only the modes identified by their source tags;
    # unresolved/permit conditions remain visible as an advisory.
    for node_id, attrs in graph.nodes(data=True):
        if node_id in access_nodes:
            attrs.update(access_nodes[node_id])
            attrs["access_source_ref"] = f"node/{node_id}"
            stats["access_nodes_attached"] += 1
        tags = {key: value for key, value in attrs.items()
                if key in {"access", "foot", "bicycle", "vehicle", "motorcar",
                           "motor_vehicle", "barrier", "entrance"}}
        allowed = []
        blocked = []
        advisories = {}
        for mode in MODES:
            ok, advisory = node_access(tags, mode)
            (allowed if ok else blocked).append(mode)
            if advisory:
                advisories[mode] = advisory
        if blocked or advisories:
            attrs["allowed_modes"] = allowed
            attrs["blocked_modes"] = blocked
            attrs["access_advisories"] = advisories

    return graph, stats


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roads", type=Path,
                        default=ROOT / "scripts/audit_output/osm_route_candidates.geojson")
    parser.add_argument("--access-nodes", type=Path,
                        default=ROOT / "scripts/audit_output/osm_access_nodes.geojson")
    parser.add_argument("--source-pbf", type=Path,
                        default=ROOT / "scripts/audit_output/hubei-260924.osm.pbf")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "scripts/audit_output/whu_road_network_candidate.graphml")
    args = parser.parse_args(argv)
    if not args.roads.is_file() or not args.access_nodes.is_file():
        parser.error("缺少本地 OSM 候选道路或出入口文件；先运行 scripts/validate/extract_open_osm_routes.py")
    source_hash = (hashlib.sha256(args.source_pbf.read_bytes()).hexdigest()
                   if args.source_pbf.is_file() else "")
    graph, stats = build_from_candidates(_load_json(args.roads),
                                         _load_json(args.access_nodes), source_hash)
    if graph.number_of_edges() == 0:
        parser.error("候选图没有可用路段；没有改写任何缓存")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    from spatial.network import _save_graphml
    _save_graphml(graph, str(tmp))
    # Validate the serialized graph before replacing the requested output.
    reopened = nx.read_graphml(tmp, node_type=int)
    if reopened.number_of_edges() != graph.number_of_edges():
        tmp.unlink(missing_ok=True)
        raise RuntimeError("GraphML 重读边数不匹配；保留原输出")
    tmp.replace(args.output)
    print(json.dumps({"output": str(args.output), "nodes": graph.number_of_nodes(),
                      "edges": graph.number_of_edges(), **stats}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
