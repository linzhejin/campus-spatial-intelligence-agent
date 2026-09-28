"""Derive transparent scenery components without converting missing evidence to zero."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

import networkx as nx

try:
    from scripts.network.edge_attribute_paths import GRAPH_PATH, MASTER_PATH, SOURCES_PATH, project_root
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.network.edge_attribute_paths import GRAPH_PATH, MASTER_PATH, SOURCES_PATH, project_root

from scripts.network.build_edge_attribute_master import canonical_geometry_points, graph_fingerprint
from spatial.coord_transform import gcj02_to_wgs84
from spatial.edge_attributes import edge_key, validate_master


SCENERY_WEIGHTS = {
    "greenery": 0.25,
    "shade": 0.20,
    "water": 0.15,
    "heritage": 0.15,
    "quietness": 0.15,
    "seasonality": 0.10,
}
SCENERY_METHOD_VERSION = "scenery_components_v1"


def scenery_score(components):
    present = {
        key: min(1.0, max(0.0, float(components[key])))
        for key in SCENERY_WEIGHTS
        if components.get(key) is not None
    }
    if not present:
        return None, 0.0
    coverage = sum(SCENERY_WEIGHTS[key] for key in present)
    score = sum(SCENERY_WEIGHTS[key] * value for key, value in present.items()) / coverage
    return round(score, 6), round(coverage, 6)


def scenery_level(score):
    if score is None:
        return None
    value = min(1.0, max(0.0, float(score)))
    if value < 0.2:
        return 1
    if value < 0.4:
        return 2
    if value < 0.6:
        return 3
    if value < 0.8:
        return 4
    return 5


def scenery_confidence(coverage, direct_sources=0):
    if coverage <= 0:
        return "unknown"
    if coverage < 0.80 or direct_sources <= 0:
        return "low"
    return "medium"


def _haversine_m(a, b):
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371000.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


def _midpoint(points):
    if len(points) == 1:
        return tuple(points[0])
    lengths = [_haversine_m(tuple(a), tuple(b)) for a, b in zip(points, points[1:])]
    target = sum(lengths) / 2
    walked = 0.0
    for i, length in enumerate(lengths):
        if walked + length >= target:
            ratio = 0.0 if length == 0 else (target - walked) / length
            a, b = points[i], points[i + 1]
            return (a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio)
        walked += length
    return tuple(points[-1])


def _highway_value(raw):
    if isinstance(raw, list):
        values = raw
    else:
        text = str(raw or "")
        values = [part.strip(" []'\"") for part in text.split(",")]
    order = {
        "motorway": 0.0, "trunk": 0.05, "primary": 0.1,
        "secondary": 0.25, "tertiary": 0.4, "residential": 0.65,
        "service": 0.75, "unclassified": 0.65, "living_street": 0.8,
        "cycleway": 0.85, "footway": 0.9, "path": 0.9,
        "pedestrian": 0.9, "steps": 0.85,
    }
    known = [order[value] for value in values if value in order]
    return min(known) if known else None


def _source_ids(poi):
    ids = []
    for ref in poi.get("source_refs") or []:
        if not isinstance(ref, dict) or not ref.get("source"):
            continue
        ref_id = ref.get("id") or ref.get("url") or "record"
        ids.append(f"{ref['source']}:{ref_id}")
    return ids


def _evidence_pois(pois):
    heritage_categories = {"heritage", "landmark", "museum", "pavilion", "monument", "historic"}
    prepared = []
    for poi in pois:
        refs = _source_ids(poi)
        if not refs or poi.get("subcategory") not in heritage_categories:
            continue
        coords = poi.get("coordinates") or {}
        if coords.get("lng") is None or coords.get("lat") is None:
            continue
        lon, lat = gcj02_to_wgs84(float(coords["lng"]), float(coords["lat"]))
        prepared.append({"point": (lon, lat), "refs": refs, "subcategory": poi.get("subcategory")})
    return prepared


def derive_edge_scenery(edge_data, midpoint, heritage_pois):
    components = {key: None for key in SCENERY_WEIGHTS}
    evidence = {}
    quietness = _highway_value(edge_data.get("highway"))
    if quietness is not None:
        components["quietness"] = quietness
        evidence["quietness"] = {
            "value": quietness,
            "source_refs": ["osm_graphml_current"],
            "method_version": "osm_road_class_proxy_v1",
            "confidence": "low",
        }

    nearest = None
    nearest_distance = float("inf")
    for poi in heritage_pois:
        distance = _haversine_m(midpoint, poi["point"])
        if distance < nearest_distance:
            nearest, nearest_distance = poi, distance
    if nearest and nearest_distance <= 250.0:
        value = round(max(0.0, 1.0 - nearest_distance / 250.0), 6)
        components["heritage"] = value
        evidence["heritage"] = {
            "value": value,
            "distance_m": round(nearest_distance, 1),
            "source_refs": ["poi_master_current", *nearest["refs"]],
            "method_version": "source_bearing_heritage_distance_v1",
            "confidence": "low",
        }

    score, coverage = scenery_score(components)
    direct = sum(
        row.get("method_version") not in {"osm_road_class_proxy_v1"}
        for row in evidence.values()
    )
    confidence = scenery_confidence(coverage, direct_sources=direct)
    source_refs = sorted({ref for row in evidence.values() for ref in row["source_refs"]})
    return {
        **components,
        "viewpoint": None,
        "scenery_score": score,
        "scenery_level": scenery_level(score),
        "component_coverage": coverage,
        "component_evidence": evidence,
        "source_refs": source_refs,
        "method_version": SCENERY_METHOD_VERSION,
        "confidence": confidence,
        "verification_status": "derived_unverified" if score is not None else "source_only",
    }


def derive(graph, master, pois):
    if master.get("network_version") != graph_fingerprint(graph):
        raise ValueError("master network_version does not match graph")
    edge_map = {
        edge_key([int(u), int(v), int(key)]): (u, v, key, data)
        for u, v, key, data in graph.edges(keys=True, data=True)
    }
    heritage = _evidence_pois(pois)
    stats = {key: 0 for key in (*SCENERY_WEIGHTS, "unknown", "low", "medium")}
    for record in master["records"]:
        binding = record["network_bindings"][0]
        found = edge_map.get(edge_key(binding["edge_id"]))
        if not found:
            stats["unknown"] += 1
            continue
        u, v, key, data = found
        midpoint = _midpoint(canonical_geometry_points(graph, u, v, key))
        scenery = derive_edge_scenery(data, midpoint, heritage)
        record["scenery"] = scenery
        stats[scenery["confidence"]] += 1
        for component in SCENERY_WEIGHTS:
            if scenery[component] is not None:
                stats[component] += 1
    stats["heritage_evidence_pois"] = len(heritage)
    return stats


def main(argv=None):
    parser = argparse.ArgumentParser(description="从已登记开放来源推导可解释景观分量")
    parser.add_argument("--graph", default=str(GRAPH_PATH))
    parser.add_argument("--master", default=str(MASTER_PATH))
    parser.add_argument("--sources", default=str(SOURCES_PATH))
    parser.add_argument("--pois", default=str(project_root() / "data" / "pois.json"))
    parser.add_argument("--output", default=str(MASTER_PATH))
    args = parser.parse_args(argv)

    sources = json.loads(Path(args.sources).read_text(encoding="utf-8"))
    registered = {row.get("source_id") for row in sources.get("sources", [])}
    if not {"osm_graphml_current", "poi_master_current"} <= registered:
        raise SystemExit("source manifest must register graph and POI master first")
    graph = nx.read_graphml(args.graph, node_type=int)
    master = json.loads(Path(args.master).read_text(encoding="utf-8"))
    pois_envelope = json.loads(Path(args.pois).read_text(encoding="utf-8"))
    validate_master(master)
    stats = derive(graph, master, pois_envelope.get("pois", []))
    master["generated_at"] = datetime.now(timezone.utc).isoformat()
    validate_master(master)
    output = Path(args.output).resolve()
    output.write_text(json.dumps(master, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**stats, "output": str(output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
