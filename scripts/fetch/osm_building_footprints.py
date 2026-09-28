"""Fetch OSM building footprints intersecting Wuhan University's campus polygons.

The output is an audit-only ODbL-derived GeoJSON layer. It is used to flag
possible route/building intersections, never to infer pedestrian access.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import httpx
from shapely.geometry import Polygon, shape, mapping
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[2]
OSM_MAP_URL = "https://api.openstreetmap.org/api/0.6/map"
CAMPUS_NAMES = {
    "武汉大学信息学部", "武汉大学文理学部", "武汉大学工学部",
}


def format_map_bbox(bounds: tuple[float, float, float, float]) -> str:
    west, south, east, north = bounds
    return f"{west},{south},{east},{north}"


def parse_osm_map_xml(xml_text: str) -> dict:
    """Reconstruct bounded OSM building ways from the OSM API XML response."""
    root = ET.fromstring(xml_text)
    if root.tag != "osm":
        raise ValueError("OSM map response is not an osm XML document")
    nodes = {
        node.attrib["id"]: {"lon": float(node.attrib["lon"]),
                            "lat": float(node.attrib["lat"])}
        for node in root.findall("node")
    }
    elements = []
    for way in root.findall("way"):
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in way.findall("tag")}
        if not tags.get("building") or tags.get("building") == "no":
            continue
        refs = [nd.attrib["ref"] for nd in way.findall("nd")]
        geometry = [nodes[ref] for ref in refs if ref in nodes]
        if len(geometry) != len(refs):
            geometry = []
        elements.append({"type": "way", "id": int(way.attrib["id"]),
                         "tags": tags, "geometry": geometry})
    meta = root.find("meta")
    return {"elements": elements,
            "osm_base": meta.attrib.get("osm_base") if meta is not None else None}


def extract_building_features(response: dict, campus_geometry) -> tuple[list[dict], dict]:
    elements = response.get("elements") or []
    features = []
    building_ways = outside = invalid = 0
    seen = set()
    for element in elements:
        if element.get("type") != "way":
            continue
        tags = element.get("tags") or {}
        building = tags.get("building")
        if not building or building == "no":
            continue
        building_ways += 1
        osm_id = f"way/{element.get('id')}"
        if osm_id in seen:
            continue
        coords = [(float(point["lon"]), float(point["lat"]))
                  for point in element.get("geometry") or []]
        try:
            geometry = Polygon(coords)
        except (TypeError, ValueError):
            invalid += 1
            continue
        if len(coords) < 4 or geometry.is_empty or not geometry.is_valid or geometry.area <= 0:
            invalid += 1
            continue
        if not geometry.intersects(campus_geometry):
            outside += 1
            continue
        seen.add(osm_id)
        properties = {
            "osm_id": osm_id,
            "building": building,
            "name": tags.get("name"),
            "source": "OpenStreetMap",
            "license": "ODbL-1.0",
        }
        for key in ("building:levels", "height", "access", "entrance", "layer"):
            if tags.get(key) is not None:
                properties[key] = tags[key]
        features.append({
            "type": "Feature", "id": osm_id,
            "geometry": mapping(geometry), "properties": properties,
        })
    return features, {
        "elements": len(elements), "building_ways": building_ways,
        "valid_footprints": len(features), "outside_campus": outside,
        "invalid_footprints": invalid,
    }


def _load_campus_union(path: Path):
    document = json.loads(path.read_text(encoding="utf-8"))
    geometries = [
        shape(feature["geometry"])
        for feature in document.get("features", [])
        if feature.get("geometry")
        and (feature.get("properties") or {}).get("name") in CAMPUS_NAMES
    ]
    if len(geometries) != 3 or any(not geometry.is_valid for geometry in geometries):
        raise ValueError("expected valid OSM polygons for all three WHU campuses")
    return unary_union(geometries)


def fetch_buildings(output_path: Path, *, boundaries_path: Path | None = None) -> dict:
    boundaries_path = boundaries_path or ROOT / "scripts/fetch/osm_campus_boundaries.geojson"
    campus_geometry = _load_campus_union(boundaries_path)
    bounds = tuple(map(float, campus_geometry.bounds))
    response = httpx.get(
        OSM_MAP_URL, params={"bbox": format_map_bbox(bounds)},
        headers={"User-Agent": "WHU-Spatial-Intelligence-Agent/1.0 (campus research audit)",
                 "Accept": "application/xml"},
        timeout=httpx.Timeout(90, connect=15),
    )
    response.raise_for_status()
    payload = parse_osm_map_xml(response.text)
    features, summary = extract_building_features(payload, campus_geometry)
    document = {
        "type": "FeatureCollection",
        "name": "WHU campus OSM building footprints (audit only)",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "metadata": {
            "source": "OpenStreetMap API 0.6 /map",
            "license": "ODbL-1.0",
            "attribution": "© OpenStreetMap contributors",
            "query_bounds_wgs84": list(bounds),
            "osm_base": payload.get("osm_base"),
            "source_last_modified": response.headers.get("Last-Modified"),
            "source_etag": response.headers.get("ETag"),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "use_policy": "possible geometric crossing flag only; not access evidence",
        },
        "features": features,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(output_path)
    return document["metadata"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "scripts/audit_output/osm_buildings.geojson",
    )
    parser.add_argument("--boundaries", type=Path,
                        default=ROOT / "scripts/fetch/osm_campus_boundaries.geojson")
    args = parser.parse_args(argv)
    result = fetch_buildings(args.output.resolve(), boundaries_path=args.boundaries.resolve())
    # Escape non-console glyphs such as © for legacy Windows code pages.
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
