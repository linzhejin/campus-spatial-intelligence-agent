"""Derive directional terrain candidates from a versioned offline DEM."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

import networkx as nx
import numpy as np

try:
    from scripts.network.edge_attribute_paths import GRAPH_PATH, MASTER_PATH, SOURCES_PATH, project_root
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.network.edge_attribute_paths import GRAPH_PATH, MASTER_PATH, SOURCES_PATH, project_root

from scripts.network.build_edge_attribute_master import (
    _sha256_file,
    canonical_geometry_points,
    graph_fingerprint,
)
from spatial.edge_attributes import edge_key, source_manifest_fingerprint, validate_master


SLOPE_BINS = ((2.0, 1), (5.0, 2), (8.0, 3), (15.0, 4), (float("inf"), 5))


def unknown_terrain(facility_type=None):
    return {
        "elevation_start_m": None,
        "elevation_end_m": None,
        "grade_signed_pct": None,
        "grade_abs_pct": None,
        "grade_p90_pct": None,
        "elevation_gain_m": None,
        "elevation_loss_m": None,
        "facility_type": facility_type,
        "slope_level": None,
        "confidence": "unknown",
        "verification_status": "source_only",
        "method_version": "terrain_profile_v1",
    }


def _fill_short_nodata(profile):
    values = [None if value is None else float(value) for value in profile]
    if len(values) < 2 or values[0] is None or values[-1] is None:
        return None
    run = 0
    max_run = 0
    for value in values:
        if value is None:
            run += 1
            max_run = max(max_run, run)
        else:
            run = 0
    if max_run > 2:
        return None
    known_idx = [i for i, value in enumerate(values) if value is not None]
    known_values = [values[i] for i in known_idx]
    return np.interp(range(len(values)), known_idx, known_values).tolist()


def terrain_candidate(profile, length_m, resolution_m, facility_type=None):
    values = _fill_short_nodata(profile)
    if values is None or len(values) < 2 or length_m <= 0 or resolution_m <= 0:
        return unknown_terrain(facility_type=facility_type)
    deltas = [b - a for a, b in zip(values, values[1:])]
    grade_signed = (values[-1] - values[0]) / length_m * 100.0
    grade_abs = abs(grade_signed)
    sample_run = length_m / max(1, len(values) - 1)
    local_grades = [abs(delta) / sample_run * 100.0 for delta in deltas]
    p90 = float(np.percentile(local_grades, 90))
    confidence = "low" if length_m < 3.0 * resolution_m else "medium"
    level = next(level for threshold, level in SLOPE_BINS if grade_abs < threshold)
    return {
        "elevation_start_m": round(values[0], 3),
        "elevation_end_m": round(values[-1], 3),
        "grade_signed_pct": round(grade_signed, 3),
        "grade_abs_pct": round(grade_abs, 3),
        "grade_p90_pct": round(p90, 3),
        "elevation_gain_m": round(sum(max(0.0, delta) for delta in deltas), 3),
        "elevation_loss_m": round(sum(max(0.0, -delta) for delta in deltas), 3),
        "facility_type": facility_type,
        "slope_level": level,
        "confidence": confidence,
        "verification_status": "derived_unverified",
        "method_version": "terrain_profile_v1",
    }


def _haversine_m(a, b):
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371000.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


def _interpolate_wgs84(points, spacing_m):
    if len(points) < 2:
        return list(points)
    lengths = [_haversine_m(a, b) for a, b in zip(points, points[1:])]
    total = sum(lengths)
    if total <= 0:
        return [points[0], points[-1]]
    targets = np.linspace(0.0, total, max(2, math.ceil(total / spacing_m) + 1))
    sampled = []
    segment = 0
    offset = 0.0
    for target in targets:
        while segment < len(lengths) - 1 and target > offset + lengths[segment]:
            offset += lengths[segment]
            segment += 1
        run = lengths[segment]
        ratio = 0.0 if run == 0 else min(1.0, max(0.0, (target - offset) / run))
        a, b = points[segment], points[segment + 1]
        sampled.append((a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio))
    return sampled


def sample_profile(dataset, points, spacing_m):
    """Sample a complete WGS-84 edge geometry; nodata remains ``None``."""
    sampled = _interpolate_wgs84(points, max(1.0, float(spacing_m)))
    crs_text = dataset.crs.to_string() if hasattr(dataset.crs, "to_string") else str(dataset.crs)
    coords = sampled
    if crs_text.upper() not in {"EPSG:4326", "OGC:CRS84", "WGS84"}:
        try:
            from pyproj import Transformer
        except ImportError as exc:
            raise RuntimeError("sampling a projected DEM requires pyproj") from exc
        transform = Transformer.from_crs("EPSG:4326", dataset.crs, always_xy=True)
        coords = [transform.transform(lon, lat) for lon, lat in sampled]
    result = []
    nodata = dataset.nodata
    for sample in dataset.sample(coords):
        value = float(sample[0]) if len(sample) else float("nan")
        if not math.isfinite(value) or (nodata is not None and value == float(nodata)):
            result.append(None)
        else:
            result.append(value)
    return result


def _truthy(value):
    return str(value).strip().lower() in {"yes", "true", "1", "designated"}


def detect_facility_type(data):
    highway = str(data.get("highway") or "").lower()
    if highway == "elevator":
        return "elevator"
    if data.get("conveying") not in (None, "", "no", False):
        return "escalator"
    if highway == "steps":
        return "steps"
    if _truthy(data.get("ramp")) or highway == "ramp":
        return "ramp"
    if _truthy(data.get("bridge")):
        return "bridge"
    if _truthy(data.get("tunnel")):
        return "tunnel"
    if _truthy(data.get("indoor")):
        return "indoor"
    return None


def _oriented_points(graph, u, v, key):
    points = canonical_geometry_points(graph, u, v, key)
    start = (float(graph.nodes[u]["x"]), float(graph.nodes[u]["y"]))
    if _haversine_m(tuple(points[-1]), start) < _haversine_m(tuple(points[0]), start):
        points.reverse()
    return [tuple(point) for point in points]


def _resolution_m(dataset, override=None):
    if override:
        return float(override)
    xres, yres = abs(float(dataset.res[0])), abs(float(dataset.res[1]))
    if getattr(dataset.crs, "is_geographic", False):
        return max(xres * 96400.0, yres * 111000.0)
    return max(xres, yres)


def derive(graph, master, dataset, source_id, resolution_m):
    if master.get("network_version") != graph_fingerprint(graph):
        raise ValueError("master network_version does not match graph")
    edge_map = {
        edge_key([int(u), int(v), int(key)]): (u, v, key, data)
        for u, v, key, data in graph.edges(keys=True, data=True)
    }
    stats = {"medium": 0, "low": 0, "unknown": 0, "nodata": 0, "high_risk": 0}
    facilities = {}
    for record in master["records"]:
        binding = record["network_bindings"][0]
        found = edge_map.get(edge_key(binding["edge_id"]))
        if not found:
            stats["unknown"] += 1
            continue
        u, v, key, data = found
        facility = detect_facility_type(data)
        if facility:
            facilities[facility] = facilities.get(facility, 0) + 1
        points = _oriented_points(graph, u, v, key)
        profile = sample_profile(dataset, points, max(resolution_m, 5.0))
        candidate = terrain_candidate(
            profile, float(record.get("length_m") or data.get("length") or 0),
            resolution_m, facility_type=facility,
        )
        candidate["source_refs"] = [source_id, "osm_graphml_current"]
        record["terrain"] = candidate
        stats[candidate["confidence"]] += 1
        if candidate["confidence"] == "unknown":
            stats["nodata"] += 1
        if (candidate.get("grade_p90_pct") or 0) >= 25:
            stats["high_risk"] += 1
    stats["facilities"] = facilities
    return stats


def main(argv=None):
    parser = argparse.ArgumentParser(description="从有版本的离线 DEM 推导方向性坡度候选")
    parser.add_argument("--dem", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--license", required=True)
    parser.add_argument("--redistribution", required=True, choices=("open", "restricted"))
    parser.add_argument("--acquired-at")
    parser.add_argument("--resolution-m", type=float)
    parser.add_argument("--graph", default=str(GRAPH_PATH))
    parser.add_argument("--master", default=str(MASTER_PATH))
    parser.add_argument("--source-manifest", default=str(SOURCES_PATH))
    parser.add_argument("--output", required=True,
                        help="显式候选主库输出；不会直接发布运行时快照")
    args = parser.parse_args(argv)

    dem_path = Path(args.dem).resolve()
    if not dem_path.is_file():
        parser.error(f"DEM file does not exist: {dem_path}")
    try:
        import rasterio
    except ImportError as exc:
        raise SystemExit("缺少 rasterio；请安装 requirements-dev.txt 后离线运行") from exc

    graph = nx.read_graphml(args.graph, node_type=int)
    master = json.loads(Path(args.master).read_text(encoding="utf-8"))
    validate_master(master)
    manifest_path = Path(args.source_manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    with rasterio.open(dem_path) as dataset:
        resolution_m = _resolution_m(dataset, args.resolution_m)
        if resolution_m <= 0:
            raise SystemExit("DEM resolution must be positive")
        stats = derive(graph, master, dataset, args.source_id, resolution_m)
        crs = dataset.crs.to_string()
        pixel_size = [abs(float(dataset.res[0])), abs(float(dataset.res[1]))]

    try:
        local_path = dem_path.relative_to(project_root()).as_posix()
    except ValueError:
        local_path = str(dem_path)
    acquired_at = args.acquired_at or datetime.fromtimestamp(
        dem_path.stat().st_mtime, timezone.utc
    ).date().isoformat()
    source = {
        "source_id": args.source_id,
        "source_type": "digital_elevation_model",
        "version": args.version,
        "acquired_at": acquired_at,
        "crs": crs,
        "resolution_m": round(resolution_m, 3),
        "pixel_size": pixel_size,
        "sha256": _sha256_file(dem_path),
        "license": args.license,
        "redistribution": args.redistribution,
        "local_path": local_path,
    }
    by_id = {row["source_id"]: row for row in manifest.get("sources", [])}
    by_id[args.source_id] = source
    updated_manifest = {
        "schema_version": 1,
        "sources": [by_id[key] for key in sorted(by_id)],
    }
    master["source_manifest_fingerprint"] = source_manifest_fingerprint(updated_manifest)
    master["generated_at"] = datetime.now(timezone.utc).isoformat()
    validate_master(master)

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(master, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest_path.write_text(
        json.dumps(updated_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({**stats, "source": source, "output": str(output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
