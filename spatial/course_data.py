"""Normalization and import helpers for user-supplied WHU coursework data.

The imported GeoJSON is a derived working artifact. Original Shapefile files
remain at their source location and are represented by hashes in the manifest.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET


ROAD_NAME_FIELD = "道路名"
ROAD_CLASS_FIELD = "道路类"
ROAD_SURFACE_FIELD = "路面材"
ROAD_LANES_FIELD = "车道数"
ROAD_CODE_FIELDS = ("地理编", "地理编码")

_UNKNOWN_NAMES = {"", "无名路", "未命名", "未命名路", "未知"}
_CLASS_MAP = {
    "机动车": "motor_vehicle",
    "机动车道": "motor_vehicle",
    "车行道": "motor_vehicle",
    "人行道": "pedestrian",
    "行人道": "pedestrian",
    "电动车": "electric_vehicle",
    "电动车道": "electric_vehicle",
    "施工": "construction",
}
_SURFACE_MAP = {
    "水泥": "concrete",
    "水泥路面": "concrete",
    "沥青": "asphalt",
    "沥青路面": "asphalt",
    "砖石": "paving_blocks",
    "砂石": "gravel",
}


def _text(value) -> str | None:
    if value is None:
        return None
    try:
        # Handles pandas/numpy NaN without making pandas a runtime dependency.
        if math.isnan(float(value)):
            return None
    except (TypeError, ValueError, OverflowError):
        pass
    text = str(value).strip()
    return text or None


def _nonnegative_integer(value) -> int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        return None
    return int(number)


def _json_scalar(value):
    if value is None:
        return None
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def normalize_course_road(properties: dict) -> dict:
    """Normalize the course layer's known fields without inventing semantics.

    This source's line Z values were inspected and are all zero. They are
    therefore explicitly unavailable elevation, not measured flat terrain.
    """
    raw_name = _text(properties.get(ROAD_NAME_FIELD))
    name = raw_name if raw_name not in _UNKNOWN_NAMES else None
    raw_class = _text(properties.get(ROAD_CLASS_FIELD))
    raw_surface = _text(properties.get(ROAD_SURFACE_FIELD))
    raw_code = next((properties.get(field) for field in ROAD_CODE_FIELDS
                     if properties.get(field) is not None), None)
    try:
        if raw_code is not None and math.isnan(float(raw_code)):
            raw_code = None
    except (TypeError, ValueError, OverflowError):
        pass

    return {
        "name": name,
        "name_raw": raw_name,
        "road_class": _CLASS_MAP.get(raw_class),
        "road_class_raw": raw_class,
        "surface": _SURFACE_MAP.get(raw_surface),
        "surface_raw": raw_surface,
        "lane_count": _nonnegative_integer(properties.get(ROAD_LANES_FIELD)),
        "geographic_code": _json_scalar(raw_code),
        "raw_source_properties": {
            str(key): _json_scalar(value) for key, value in properties.items()
        },
        "elevation_available": False,
        "elevation_source": None,
        # Static source classification is not evidence of current access state.
        "blocked_modes": [],
        "current_status": "unknown",
    }


def normalize_course_spot(properties: dict, source_id: str, source_row: int) -> dict:
    """Keep the point's full source record alongside its normalized name."""
    raw = {str(key): _json_scalar(value) for key, value in properties.items()}
    raw_name = _text(properties.get("name"))
    name = raw_name if raw_name not in _UNKNOWN_NAMES else None
    return {
        "name": name,
        "source_rows": [int(source_row)],
        "source_ids": [str(source_id)],
        "source_records": [{
            "source_row": int(source_row),
            "source_id": str(source_id),
            "raw_source_properties": raw,
        }],
    }


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_records(layer_path: Path) -> list[dict]:
    """Hash source files and known metadata sidecars; never copy the raw files."""
    candidates = [
        layer_path.with_suffix(suffix)
        for suffix in (".shp", ".shx", ".dbf", ".prj", ".cpg")
    ]
    candidates.extend((layer_path.with_suffix(".shp.xml"), layer_path.with_suffix(".xml")))
    records = []
    for path in candidates:
        if not path.is_file() or any(row["path"] == path.name for row in records):
            continue
        records.append({
            "path": path.name,
            "size_bytes": path.stat().st_size,
            "filesystem_mtime_utc": datetime.fromtimestamp(
                path.stat().st_mtime, timezone.utc
            ).isoformat(),
            "sha256": sha256_file(path),
        })
    return records


def _metadata_dates(xml_path: Path) -> dict:
    if not xml_path.exists():
        return {"created_date": None, "processing_dates": []}
    try:
        root = ET.parse(xml_path).getroot()
    except (OSError, ET.ParseError):
        return {"created_date": None, "processing_dates": []}
    created = root.findtext(".//CreaDate")
    dates = set()
    for process in root.findall(".//Process"):
        value = process.attrib.get("Date")
        if value and len(value) == 8 and value.isdigit():
            dates.add(f"{value[:4]}-{value[4:6]}-{value[6:]}")
    if created and len(created) == 8 and created.isdigit():
        created = f"{created[:4]}-{created[4:6]}-{created[6:]}"
    else:
        created = None
    return {"created_date": created, "processing_dates": sorted(dates)}


def _fingerprint(records: list[dict]) -> str:
    payload = [
        {"path": row["path"], "sha256": row["sha256"]}
        for row in sorted(records, key=lambda item: item["path"])
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


def _drop_z(geometry):
    from shapely.ops import transform

    def xy(x, y, z=None):
        return x, y

    return transform(xy, geometry)


def _json_write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def import_course_data(source_dir: Path | str, output_dir: Path | str) -> dict:
    """Normalize the course road and point layers to a local, auditable bundle."""
    import geopandas as gpd
    from shapely.geometry import mapping

    source_dir = Path(source_dir).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    road_path = source_dir / "whu_road.shp"
    spot_path = source_dir / "whu_spot.shp"
    if not road_path.is_file() or not spot_path.is_file():
        raise FileNotFoundError("Expected RoadNet/whu_road.shp and whu_spot.shp")

    roads = gpd.read_file(road_path)
    spots = gpd.read_file(spot_path)
    if roads.crs is None or spots.crs is None:
        raise ValueError("Course layers must declare a CRS")
    if roads.crs.to_epsg() != 4547 or spots.crs.to_epsg() != 4547:
        raise ValueError("Expected the inspected course layers in EPSG:4547")
    if roads.geometry.isna().any() or roads.geometry.is_empty.any():
        raise ValueError("Course road layer contains missing or empty geometry")
    if not roads.geometry.geom_type.isin(["LineString", "MultiLineString"]).all():
        raise ValueError("Course road layer contains a non-line geometry")
    if spots.geometry.isna().any() or spots.geometry.is_empty.any():
        raise ValueError("Course point layer contains missing or empty geometry")
    if not spots.geometry.geom_type.eq("Point").all():
        raise ValueError("Course spot layer contains a non-point geometry")

    road_files = _artifact_records(road_path)
    spot_files = _artifact_records(spot_path)
    fingerprint = _fingerprint(road_files + spot_files)
    source_id = f"whu_course_{fingerprint[:16]}"
    roads_wgs84 = roads.to_crs("EPSG:4326")
    road_features = []
    for row_number, (_, row) in enumerate(roads_wgs84.iterrows()):
        raw_properties = {key: value for key, value in row.items() if key != "geometry"}
        properties = normalize_course_road(raw_properties)
        properties.update({
            "source_id": f"{source_id}:road:{row_number:04d}",
            "source_layer": "whu_road",
            "source_row": row_number,
            "source_crs": "EPSG:4547",
            "source_priority": "course_wins_static_conflicts",
            "source_verification": "course_supplied_not_field_verified",
        })
        road_features.append({
            "type": "Feature",
            "id": properties["source_id"],
            "geometry": mapping(_drop_z(row.geometry)),
            "properties": properties,
        })

    spots_wgs84 = spots.to_crs("EPSG:4326")
    deduplicated_spots = []
    for row_number, (_, row) in enumerate(spots_wgs84.iterrows()):
        spot_source_id = f"{source_id}:spot:{row_number:04d}"
        normalized = normalize_course_spot(
            {key: value for key, value in row.items() if key != "geometry"},
            spot_source_id, row_number,
        )
        name = normalized["name"]
        if name is None:
            continue
        point = row.geometry
        match = next((item for item in deduplicated_spots
                      if item["properties"]["name"] == name
                      and item["_point"].distance(point) < 0.00001), None)
        if match is not None:
            match["properties"]["source_rows"].append(row_number)
            match["properties"]["source_ids"].append(spot_source_id)
            match["properties"]["source_records"].extend(normalized["source_records"])
            continue
        props = normalized
        props.update({
            "source_layer": "whu_spot",
            "source_crs": "EPSG:4547",
            "source_priority": "course_wins_static_conflicts",
            "source_verification": "course_supplied_not_field_verified",
        })
        deduplicated_spots.append({
            "type": "Feature",
            "id": props["source_ids"][0],
            "geometry": mapping(point),
            "properties": props,
            "_point": point,
        })
    for feature in deduplicated_spots:
        feature.pop("_point", None)

    manifest = {
        "schema_version": 1,
        "source_id": source_id,
        "source_fingerprint_sha256": fingerprint,
        "source_description": "User-supplied Wuhan University coursework layers",
        "source_priority": "course_wins_static_conflicts; existing data fills absent course coverage",
        "priority_authorization": "explicit_user_instruction",
        "source_crs": "EPSG:4547",
        "normalized_crs": "EPSG:4326",
        "source_layer_counts": {"whu_road": len(roads), "whu_spot": len(spots)},
        "normalized_counts": {
            "roads": len(road_features), "spots": len(deduplicated_spots),
        },
        "road_layer_metadata_dates": _metadata_dates(road_path.with_suffix(".shp.xml")),
        "imported_at_utc": datetime.now(timezone.utc).isoformat(),
        "license_status": "not_provided_with_source",
        "redistribution_status": "not_established; original source files are not copied",
        "elevation_status": "unavailable; inspected line Z values are all zero",
        "sources": [
            {"layer": "whu_road", "files": road_files},
            {"layer": "whu_spot", "files": spot_files},
        ],
    }
    _json_write_atomic(output_dir / "source_manifest.json", manifest)
    _json_write_atomic(output_dir / "course_roads_normalized.geojson", {
        "type": "FeatureCollection", "coordinate_system": "EPSG:4326",
        "features": road_features,
    })
    _json_write_atomic(output_dir / "course_pois_normalized.geojson", {
        "type": "FeatureCollection", "coordinate_system": "EPSG:4326",
        "features": deduplicated_spots,
    })
    return {
        "source_id": source_id,
        "road_count": len(road_features),
        "spot_record_count": len(spots),
        "unique_spot_count": len(deduplicated_spots),
        "output_dir": str(output_dir),
        "manifest": manifest,
    }
