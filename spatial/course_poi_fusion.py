"""Auditable fusion of the WHU coursework spot layer into the POI library."""

from __future__ import annotations

from copy import deepcopy
import json


def _as_list(value):
    return list(value) if isinstance(value, (list, tuple)) else []


def _source_ref(source_id, source_rows):
    return {
        "source": "WHU coursework",
        "id": source_id,
        "source_rows": list(source_rows),
        "license": "not_provided_with_source",
    }


def _feature_source_ids(properties):
    values = properties.get("source_ids")
    if not isinstance(values, list) or not values or any(not isinstance(x, str) for x in values):
        raise ValueError("course POI has no stable source_ids")
    return tuple(sorted(set(values)))


def fuse_course_pois(poi_data, course_features, crosswalk):
    """Apply an explicit source-row crosswalk and return a new POI document.

    The input point layer is WGS-84; ``data/pois.json`` is GCJ-02.  Coordinates
    are converted at this boundary only.  No name- or proximity-only match is
    allowed: every point must have one reviewed crosswalk entry that lists the
    exact normalized source IDs.
    """
    from spatial.coord_transform import wgs84_to_gcj02

    if not isinstance(poi_data, dict) or not isinstance(poi_data.get("pois"), list):
        raise ValueError("POI document must contain a pois array")
    if not isinstance(crosswalk, dict) or crosswalk.get("schema_version") != 1:
        raise ValueError("unsupported course POI crosswalk")
    prefix = crosswalk.get("source_id_prefix")
    decisions = crosswalk.get("decisions")
    if not isinstance(prefix, str) or not prefix or not isinstance(decisions, list):
        raise ValueError("course POI crosswalk is incomplete")

    decision_by_ids = {}
    for decision in decisions:
        source_ids = decision.get("source_ids") if isinstance(decision, dict) else None
        if not isinstance(source_ids, list) or not source_ids:
            raise ValueError("crosswalk decision must declare source_ids")
        key = tuple(sorted(set(source_ids)))
        if key in decision_by_ids:
            raise ValueError("duplicate source_ids in course POI crosswalk")
        if any(not source_id.startswith(prefix + ":") for source_id in key):
            raise ValueError("crosswalk source_id prefix does not match this dataset")
        decision_by_ids[key] = decision

    poi_by_id = {}
    for poi in poi_data["pois"]:
        poi_id = poi.get("id")
        if not isinstance(poi_id, str) or not poi_id or poi_id in poi_by_id:
            raise ValueError("POI IDs must be unique nonempty strings")
        poi_by_id[poi_id] = poi

    features_by_ids = {}
    for feature in course_features:
        if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
            raise ValueError("invalid course POI feature")
        properties = feature["properties"]
        source_ids = _feature_source_ids(properties)
        if source_ids in features_by_ids:
            raise ValueError("duplicate normalized course POI source IDs")
        if any(not source_id.startswith(prefix + ":") for source_id in source_ids):
            raise ValueError("course POI source_id prefix does not match crosswalk")
        features_by_ids[source_ids] = feature

    if set(features_by_ids) != set(decision_by_ids):
        missing = sorted(set(features_by_ids) - set(decision_by_ids))
        stale = sorted(set(decision_by_ids) - set(features_by_ids))
        raise ValueError(f"course POI crosswalk does not match current features: missing={missing}, stale={stale}")

    output = deepcopy(poi_data)
    output_pois = {poi["id"]: poi for poi in output["pois"]}
    decisions_report = []
    counts = {"updated": 0, "unchanged": 0, "unmatched": 0}
    poi_id_map = {}

    for source_ids in sorted(features_by_ids):
        feature = features_by_ids[source_ids]
        properties = feature["properties"]
        decision = decision_by_ids[source_ids]
        poi_id = decision.get("target_poi_id")
        if not isinstance(poi_id, str) or poi_id not in output_pois:
            raise ValueError(f"crosswalk target_poi_id is missing from POI library: {poi_id}")
        if decision.get("coordinate_priority") not in {"course", "existing"}:
            raise ValueError("crosswalk coordinate_priority must be 'course' or 'existing'")
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates")
        if geometry.get("type") != "Point" or not isinstance(coords, list) or len(coords) < 2:
            raise ValueError(f"unsupported course POI geometry for {source_ids[0]}")
        try:
            source_wgs = [float(coords[0]), float(coords[1])]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid course POI coordinate for {source_ids[0]}") from exc
        target_gcj = wgs84_to_gcj02(*source_wgs)
        if not all(map(lambda value: isinstance(value, (int, float)), target_gcj)):
            raise ValueError(f"coordinate conversion failed for {source_ids[0]}")

        poi = output_pois[poi_id]
        changed = False
        existing_ids = set(_as_list(poi.get("course_source_ids")))
        existing_history = _as_list(poi.get("coordinate_history"))
        old_coordinate = poi.get("coordinates")
        old_verification = poi.get("verification_status")

        aliases = _as_list(poi.get("aliases"))
        for alias in [properties.get("name"), *decision.get("aliases", [])]:
            if isinstance(alias, str) and alias.strip() and alias not in aliases and alias != poi.get("name"):
                aliases.append(alias)
                changed = True
        if aliases != poi.get("aliases", []):
            poi["aliases"] = aliases

        if decision["coordinate_priority"] == "course":
            new_coordinate = {"lng": round(float(target_gcj[0]), 9),
                              "lat": round(float(target_gcj[1]), 9)}
            if old_coordinate != new_coordinate:
                if not any(set(item.get("source_ids", [])) == set(source_ids)
                           for item in existing_history if isinstance(item, dict)):
                    existing_history.append({
                        "coordinates": deepcopy(old_coordinate),
                        "coordinate_system": poi.get("coordinate_system", "GCJ-02"),
                        "superseded_by_source_ids": list(source_ids),
                        "superseded_by": "WHU coursework",
                        "previous_verification_status": old_verification,
                    })
                poi["coordinates"] = new_coordinate
                poi["coordinate_system"] = "GCJ-02"
                poi["coordinate_source"] = "WHU coursework"
                poi["coordinate_source_ids"] = list(source_ids)
                poi["coordinate_source_crs"] = properties.get("source_crs", "EPSG:4326")
                poi["coordinate_transform"] = "WGS84_to_GCJ02_once"
                poi["coordinate_verification_status"] = "course_source_only"
                poi["verification_status"] = "source_only"
                changed = True
            if existing_history != poi.get("coordinate_history", []):
                poi["coordinate_history"] = existing_history
                changed = True

        refs = _as_list(poi.get("source_refs"))
        for source_id in source_ids:
            ref = _source_ref(source_id, properties.get("source_rows") or [])
            if ref not in refs:
                refs.append(ref)
                changed = True
        if refs != poi.get("source_refs", []):
            poi["source_refs"] = refs

        source_records = _as_list(properties.get("source_records"))
        if source_records:
            course_records = _as_list(poi.get("course_source_records"))
            known_source_ids = {
                row.get("source_id") for row in course_records if isinstance(row, dict)
            }
            for source_record in source_records:
                if (isinstance(source_record, dict)
                        and source_record.get("source_id") in source_ids
                        and source_record.get("source_id") not in known_source_ids):
                    course_records.append(deepcopy(source_record))
                    known_source_ids.add(source_record["source_id"])
                    changed = True
            if course_records != poi.get("course_source_records", []):
                poi["course_source_records"] = course_records

        new_ids = sorted(existing_ids | set(source_ids))
        if new_ids != sorted(existing_ids):
            poi["course_source_ids"] = new_ids
            changed = True
        basis = decision.get("match_basis")
        if basis and poi.get("course_match_basis") != basis:
            poi["course_match_basis"] = basis
            changed = True
        poi["source_priority"] = "course_wins_static_conflicts"

        if changed:
            counts["updated"] += 1
            disposition = "updated"
        else:
            counts["unchanged"] += 1
            disposition = "unchanged"
        for source_id in source_ids:
            poi_id_map[source_id] = poi_id
        decisions_report.append({
            "source_ids": list(source_ids),
            "source_rows": list(properties.get("source_rows") or []),
            "course_name": properties.get("name"),
            "target_poi_id": poi_id,
            "decision": disposition,
            "coordinate_priority": decision["coordinate_priority"],
            "match_basis": basis,
            "source_wgs84": source_wgs,
            "target_gcj02": {"lng": round(float(target_gcj[0]), 9),
                             "lat": round(float(target_gcj[1]), 9)},
            "previous_coordinate_gcj02": old_coordinate,
        })

    output["count"] = len(output["pois"])
    report = {
        "schema_version": 1,
        "source_id_prefix": prefix,
        "input_count": len(course_features),
        "source_id_count": sum(len(_feature_source_ids(feature["properties"]))
                                for feature in course_features),
        "counts": counts,
        "poi_id_map": poi_id_map,
        "decisions": decisions_report,
    }
    return output, report


def write_json_atomic(path, payload, *, indent=2):
    """Write JSON atomically so interrupted merges cannot truncate live data."""
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=indent, allow_nan=False) + "\n",
                    encoding="utf-8")
    temp.replace(target)
