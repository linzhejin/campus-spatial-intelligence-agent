"""Quarantine reviewed legacy POIs located inside a different university.

The source record is kept in an archive, and only explicit review decisions
can remove records from the Wuhan University campus catalog.
"""

import json
import sys
from pathlib import Path

from shapely.geometry import Point, shape

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from spatial.coord_transform import gcj02_to_wgs84  # noqa: E402
WHU_REFS = {"relation/7728026", "relation/20349098", "relation/20349097",
            "relation/10717504"}


def review_exclusions(pois, decisions, features, whu_refs):
    boundaries = {feature["properties"]["osm_id"]: feature for feature in features}
    by_id = {poi["id"]: poi for poi in pois}
    removed = set()
    archived = []
    for decision in decisions:
        poi_id = decision["poi_id"]
        foreign_ref = decision["foreign_boundary_ref"]
        if poi_id in removed or poi_id not in by_id or foreign_ref in whu_refs:
            raise ValueError(f"invalid review decision: {poi_id}")
        if foreign_ref not in boundaries:
            raise ValueError(f"foreign boundary missing: {foreign_ref}")
        poi = by_id[poi_id]
        if poi["name"] != decision["name"]:
            raise ValueError(f"reviewed name changed: {poi_id}")
        coordinate = poi["coordinates"]
        point = Point(*gcj02_to_wgs84(coordinate["lng"], coordinate["lat"]))
        if any(shape(boundaries[ref]["geometry"]).covers(point)
               for ref in whu_refs if ref in boundaries):
            raise ValueError(f"inside WHU boundary: {poi_id}")
        foreign = boundaries[foreign_ref]
        if not shape(foreign["geometry"]).covers(point):
            raise ValueError(f"outside reviewed boundary: {poi_id}")
        removed.add(poi_id)
        archived.append({"poi": poi, "review": decision,
                         "boundary_name": foreign["properties"].get("name")})
    return [poi for poi in pois if poi["id"] not in removed], archived


def _write_json(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def main():
    poi_path = ROOT / "data/pois.json"
    archive_path = ROOT / "data/pois_excluded_legacy.json"
    review_path = ROOT / "data/foreign_campus_poi_reviews.json"
    boundary_path = ROOT / "scripts/fetch/osm_campus_boundaries.geojson"
    catalog = json.loads(poi_path.read_text(encoding="utf-8"))
    reviews = json.loads(review_path.read_text(encoding="utf-8"))
    features = json.loads(boundary_path.read_text(encoding="utf-8"))["features"]
    archive = (json.loads(archive_path.read_text(encoding="utf-8")) if archive_path.exists()
               else {"schema_version": 1, "source_boundary": str(boundary_path.relative_to(ROOT)),
                     "excluded": []})
    archived_ids = {item["poi"]["id"] for item in archive["excluded"]}
    pending = [decision for decision in reviews["decisions"]
               if decision["poi_id"] not in archived_ids]
    kept, excluded = review_exclusions(catalog["pois"], pending, features, WHU_REFS)
    if not excluded:
        print("No new reviewed POIs to quarantine")
        return 0
    catalog["pois"] = kept
    catalog["count"] = len(kept)
    archive["excluded"].extend(excluded)
    _write_json(archive_path, archive)
    _write_json(poi_path, catalog)
    print(f"Quarantined {len(excluded)} reviewed foreign-campus POIs; {len(kept)} remain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
