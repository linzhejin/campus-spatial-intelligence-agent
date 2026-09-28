"""Build a conservative edge-attribute master from the current graph and legacy file."""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

import networkx as nx

try:
    from scripts.network.edge_attribute_paths import (
        GRAPH_PATH,
        LEGACY_ANNOTATIONS_PATH,
        MASTER_PATH,
        SOURCES_PATH,
        project_root,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.network.edge_attribute_paths import (
        GRAPH_PATH,
        LEGACY_ANNOTATIONS_PATH,
        MASTER_PATH,
        SOURCES_PATH,
        project_root,
    )

from spatial.edge_attributes import edge_key, source_manifest_fingerprint, validate_master


def _json_value(value):
    if isinstance(value, str):
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return value
    return value


def _source_refs(data):
    refs = _json_value(data.get("source_refs") or [])
    if isinstance(refs, dict):
        refs = [refs]
    if not isinstance(refs, list):
        refs = []
    normalized = [ref for ref in refs if isinstance(ref, dict)]
    osmids = _json_value(data.get("osmid"))
    if osmids is not None and not isinstance(osmids, (list, tuple, set)):
        osmids = [osmids]
    for osmid in osmids or []:
        ref = {"source": "OpenStreetMap", "id": f"way/{osmid}"}
        if ref not in normalized:
            normalized.append(ref)
    return sorted(
        normalized,
        key=lambda ref: json.dumps(ref, ensure_ascii=False, sort_keys=True),
    )


def canonical_geometry_points(graph, u, v, key):
    data = graph[u][v][key]
    geometry = data.get("geometry")
    if isinstance(geometry, str):
        try:
            from shapely import wkt
            geometry = wkt.loads(geometry)
        except Exception:
            geometry = None
    if geometry is not None and hasattr(geometry, "coords"):
        points = list(geometry.coords)
    else:
        points = [
            (float(graph.nodes[u]["x"]), float(graph.nodes[u]["y"])),
            (float(graph.nodes[v]["x"]), float(graph.nodes[v]["y"])),
        ]
    return [[round(float(x), 7), round(float(y), 7)] for x, y in points]


def geometry_hash(graph, u, v, key):
    raw = json.dumps(canonical_geometry_points(graph, u, v, key), separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


def segment_id(source_refs, geom_hash, direction):
    canonical_refs = sorted(
        json.dumps(ref, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for ref in source_refs
    )
    identity = {
        "source_refs": canonical_refs,
        "geometry_hash": geom_hash,
        "direction": direction,
    }
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return "seg_" + sha256(raw.encode("utf-8")).hexdigest()[:20]


def graph_fingerprint(graph):
    nodes = [
        [str(node), round(float(data["x"]), 7), round(float(data["y"]), 7)]
        for node, data in graph.nodes(data=True)
    ]
    nodes.sort(key=lambda row: row[0])
    edges = []
    for u, v, key, data in graph.edges(keys=True, data=True):
        edges.append({
            "edge_id": [str(u), str(v), int(key)],
            "source_refs": _source_refs(data),
            "geometry_hash": geometry_hash(graph, u, v, key),
        })
    edges.sort(key=lambda row: (row["edge_id"][0], row["edge_id"][1], row["edge_id"][2]))
    raw = json.dumps(
        {"nodes": nodes, "edges": edges},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(raw.encode("utf-8")).hexdigest()


def _unknown_terrain(source_refs=None):
    return {
        "elevation_start_m": None,
        "elevation_end_m": None,
        "grade_signed_pct": None,
        "grade_abs_pct": None,
        "grade_p90_pct": None,
        "elevation_gain_m": None,
        "elevation_loss_m": None,
        "facility_type": None,
        "slope_level": None,
        "source_refs": list(source_refs or []),
        "method_version": "legacy_placeholder",
        "confidence": "unknown",
        "verification_status": "source_only",
    }


def _unknown_scenery(source_refs=None):
    return {
        "greenery": None,
        "shade": None,
        "water": None,
        "heritage": None,
        "quietness": None,
        "seasonality": None,
        "viewpoint": None,
        "scenery_score": None,
        "scenery_level": None,
        "source_refs": list(source_refs or []),
        "method_version": "legacy_poi_name_heuristic",
        "confidence": "unknown",
        "verification_status": "source_only",
    }


def migrate_legacy_record(legacy, binding, *, geometry_replaced=False):
    if geometry_replaced:
        # The legacy measurements describe the old shape, not the replacement.
        legacy = None
    legacy = legacy or {}
    note = str(legacy.get("note") or "")
    source_refs = ["legacy_road_annotations"] if legacy else []
    terrain = _unknown_terrain(source_refs)
    if "DEM实测" in note and "占位" not in note:
        match = re.search(r"坡度\s*([0-9.]+)%", note)
        if match:
            terrain.update({
                "grade_abs_pct": float(match.group(1)),
                "slope_level": legacy.get("slope_level"),
                "method_version": "legacy_endpoint_dem_unversioned",
                "confidence": "low",
                "verification_status": "derived_unverified",
            })
    return {
        "network_bindings": [binding],
        "terrain": terrain,
        "scenery": _unknown_scenery(source_refs),
    }


def _sha256_file(path):
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _acquired_at(path):
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date().isoformat()


def _source_entry(source_id, source_type, path, *, version, license_name, redistribution,
                  crs=None, resolution_m=None, notes=None):
    entry = {
        "source_id": source_id,
        "source_type": source_type,
        "version": version,
        "acquired_at": _acquired_at(path),
        "crs": crs,
        "resolution_m": resolution_m,
        "sha256": _sha256_file(path),
        "license": license_name,
        "redistribution": redistribution,
        "local_path": path.relative_to(project_root()).as_posix(),
    }
    if notes:
        entry["notes"] = notes
    return entry


def graph_source_license(graph):
    """Describe the current graph's combined-source redistribution status."""
    if graph.graph.get("course_release_fingerprint"):
        return {
            "license": "mixed_sources_license_review_required",
            "redistribution": "not_established",
            "notes": (
                "Graph combines OSM ODbL data with WHU coursework geometry; "
                "course-source redistribution permission is not established. "
                "See the course release manifest before distribution."
            ),
        }
    return {
        "license": "ODbL-1.0",
        "redistribution": "share_alike",
        "notes": "OpenStreetMap-derived routing graph.",
    }


def _write_sources(path, entries):
    existing = {"schema_version": 1, "sources": []}
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
    by_id = {row["source_id"]: row for row in existing.get("sources", [])}
    by_id.update({row["source_id"]: row for row in entries})
    manifest = {"schema_version": 1, "sources": [by_id[key] for key in sorted(by_id)]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def build_master(graph_path, legacy_path, output_path, sources_path):
    graph = nx.read_graphml(graph_path, node_type=int)
    legacy_data = json.loads(legacy_path.read_text(encoding="utf-8"))
    legacy_by_key = {
        edge_key(row.get("edge_id")): row
        for row in legacy_data.get("edges", [])
        if isinstance(row, dict) and row.get("edge_id")
    }
    poi_path = project_root() / "data" / "pois.json"
    graph_version = graph_fingerprint(graph)
    graph_source = graph_source_license(graph)
    entries = [
        _source_entry(
            "osm_graphml_current", "directed_routing_graph", graph_path,
            version=graph_version, license_name=graph_source["license"],
            redistribution=graph_source["redistribution"], crs="EPSG:4326",
            notes=graph_source["notes"],
        ),
        _source_entry(
            "poi_master_current", "poi_master", poi_path,
            version=_sha256_file(poi_path), license_name="per_record_source_refs",
            redistribution="mixed_requires_record_review", crs="GCJ-02",
            notes="Publication scope follows each POI source_refs and verification_status.",
        ),
        _source_entry(
            "legacy_road_annotations", "legacy_edge_attributes", legacy_path,
            version=_sha256_file(legacy_path), license_name="unverified_internal_derivation",
            redistribution="internal_audit_only", crs="EPSG:4326",
        ),
    ]
    manifest = _write_sources(sources_path, entries)

    records = []
    matched = 0
    placeholder = 0
    dem_candidates = 0
    geometry_stale_skipped = 0
    for u, v, key, data in sorted(
        graph.edges(keys=True, data=True), key=lambda row: (str(row[0]), str(row[1]), int(row[2]))
    ):
        edge_id = [int(u), int(v), int(key)]
        geom_hash = geometry_hash(graph, u, v, key)
        refs = _source_refs(data)
        binding = {
            "edge_id": edge_id,
            "geometry_hash": geom_hash,
            "source_refs": refs,
        }
        legacy = legacy_by_key.get(edge_key(edge_id))
        geometry_replaced = str(data.get("course_geometry_replaced", "")).lower() == "true"
        if legacy and geometry_replaced:
            geometry_stale_skipped += 1
        elif legacy:
            matched += 1
            if "占位" in str(legacy.get("note") or ""):
                placeholder += 1
            if "DEM实测" in str(legacy.get("note") or ""):
                dem_candidates += 1
        migrated = migrate_legacy_record(
            legacy, binding, geometry_replaced=geometry_replaced
        )
        migrated.update({
            "segment_id": segment_id(refs, geom_hash, f"{u}>{v}:{key}"),
            "length_m": round(float(data.get("length") or 0.0), 3),
            "road_name": data.get("name") if isinstance(data.get("name"), str) else None,
            "highway": _json_value(data.get("highway")),
        })
        records.append(migrated)

    master = {
        "schema_version": 1,
        "network_version": graph_version,
        "source_manifest_fingerprint": source_manifest_fingerprint(manifest),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "records": records,
    }
    validate_master(master)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(master, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "graph_edges": graph.number_of_edges(),
        "records": len(records),
        "legacy_matched": matched,
        "legacy_unmatched": len(legacy_by_key) - matched,
        "legacy_geometry_stale_skipped": geometry_stale_skipped,
        "placeholder_unknown": placeholder,
        "legacy_dem_candidates": dem_candidates,
        "network_version": graph_version,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="构建保守的路段属性主库")
    parser.add_argument("--graph", default=str(GRAPH_PATH))
    parser.add_argument("--legacy", default=str(LEGACY_ANNOTATIONS_PATH))
    parser.add_argument("--output", default=str(MASTER_PATH))
    parser.add_argument("--sources", default=str(SOURCES_PATH))
    args = parser.parse_args(argv)
    summary = build_master(
        Path(args.graph).resolve(), Path(args.legacy).resolve(),
        Path(args.output).resolve(), Path(args.sources).resolve(),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
