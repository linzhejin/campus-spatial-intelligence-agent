"""Validation and review rules for traceable edge attributes."""

from copy import deepcopy
from hashlib import sha256
import json


CONFIDENCE = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
VERIFICATION = {
    "source_only",
    "derived_unverified",
    "field_verified",
    "institution_verified",
    "rejected",
}


def edge_key(edge_id):
    if not isinstance(edge_id, (list, tuple)) or len(edge_id) != 3:
        raise ValueError("edge_id must contain u, v, key")
    return f"{edge_id[0]}|{edge_id[1]}|{int(edge_id[2])}"


def _validate_attribute(name, value):
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    confidence = value.get("confidence", "unknown")
    verification = value.get("verification_status", "source_only")
    if confidence not in CONFIDENCE or verification not in VERIFICATION:
        raise ValueError(f"invalid {name} confidence or verification")
    level_name = "slope_level" if name == "terrain" else "scenery_level"
    level = value.get(level_name)
    if level is not None and level not in (1, 2, 3, 4, 5):
        raise ValueError(f"invalid {level_name}")
    if level is not None and confidence == "unknown":
        raise ValueError(f"{level_name} requires evidence and confidence")


def validate_master(data):
    if not isinstance(data, dict):
        raise ValueError("invalid master envelope")
    if data.get("schema_version") != 1 or not data.get("network_version"):
        raise ValueError("invalid master envelope")
    records = data.get("records")
    if not isinstance(records, list):
        raise ValueError("records must be a list")
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("record must be an object")
        segment_id = record.get("segment_id")
        if not segment_id or segment_id in seen:
            raise ValueError("segment_id must be unique")
        seen.add(segment_id)
        bindings = record.get("network_bindings")
        if not isinstance(bindings, list) or not bindings:
            raise ValueError("network_bindings must not be empty")
        for binding in bindings:
            edge_key(binding.get("edge_id"))
            if not binding.get("geometry_hash"):
                raise ValueError("network binding requires geometry_hash")
        _validate_attribute("terrain", record.get("terrain"))
        _validate_attribute("scenery", record.get("scenery"))
    return data


def merge_review(record, decision):
    if not isinstance(record, dict) or not isinstance(decision, dict):
        raise ValueError("record and decision must be objects")
    status = decision.get("verification_status")
    if status not in {"field_verified", "institution_verified", "rejected"}:
        raise ValueError("review must be independently verified or rejected")
    if not decision.get("evidence") or not decision.get("verified_at"):
        raise ValueError("review requires evidence and verified_at")
    attribute = decision.get("attribute")
    if attribute not in {"terrain", "scenery"}:
        raise ValueError("review attribute must be terrain or scenery")
    out = deepcopy(record)
    out.setdefault(attribute, {}).update(deepcopy(decision.get("value") or {}))
    out[attribute]["verification_status"] = status
    out[attribute]["confidence"] = "high"
    return out


def source_manifest_fingerprint(manifest):
    payload = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(payload.encode("utf-8")).hexdigest()
