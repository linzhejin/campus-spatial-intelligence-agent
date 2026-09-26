"""Source scoped Wuhan University boundaries for provider candidate filtering."""

import json
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform

from spatial.coord_transform import gcj02_to_wgs84

ROOT = Path(__file__).resolve().parents[2]
BOUNDARY_PATH = ROOT / "scripts/fetch/osm_campus_boundaries.geojson"
CAMPUS_REFS = {
    "文理学部": "relation/20349098",
    "工学部": "relation/20349097",
    "信息学部": "relation/7728026",
}
TO_METERS = Transformer.from_crs(4326, 32650, always_xy=True).transform


def load_whu_polygons(path=BOUNDARY_PATH):
    features = json.loads(Path(path).read_text(encoding="utf-8"))["features"]
    by_ref = {feature["properties"].get("osm_id"): shape(feature["geometry"])
              for feature in features}
    missing = set(CAMPUS_REFS.values()) - by_ref.keys()
    if missing:
        raise ValueError(f"WHU campus boundaries missing: {sorted(missing)}")
    return {name: by_ref[ref] for name, ref in CAMPUS_REFS.items()}


def campus_for_gcj(lng, lat, polygons, boundary_tolerance_m=0.0):
    point = Point(*gcj02_to_wgs84(float(lng), float(lat)))
    for name, polygon in polygons.items():
        if polygon.covers(point):
            return name
    if boundary_tolerance_m > 0:
        projected_point = transform(TO_METERS, point)
        candidates = [(transform(TO_METERS, polygon).distance(projected_point), name)
                      for name, polygon in polygons.items()]
        distance, name = min(candidates)
        if distance <= boundary_tolerance_m:
            return name
    return None
