from pathlib import Path

import pytest

from scripts.network.edge_attribute_paths import project_root
from scripts.network.compute_slope_from_dem import main as legacy_dem_main
from scripts.network.generate_placeholder_annotations import main as placeholder_main
from spatial.edge_attributes import (
    edge_key,
    merge_review,
    source_manifest_fingerprint,
    validate_master,
)


def _master():
    return {
        "schema_version": 1,
        "network_version": "graph-v1",
        "records": [{
            "segment_id": "seg-1",
            "network_bindings": [{
                "edge_id": [1, 2, 0],
                "geometry_hash": "g",
                "source_refs": [],
            }],
            "terrain": {
                "slope_level": None,
                "confidence": "unknown",
                "verification_status": "source_only",
            },
            "scenery": {
                "scenery_level": None,
                "confidence": "unknown",
                "verification_status": "source_only",
            },
        }],
    }


def test_network_script_root_is_repository_root():
    root = project_root()
    assert isinstance(root, Path)
    assert (root / "spatial").is_dir()
    assert (root / "data" / "whu_road_network.graphml").is_file()
    assert root.name == "campus-spatial-intelligence-agent"


def test_unknown_is_valid_but_default_level_without_evidence_is_not():
    master = _master()
    assert validate_master(master)["records"][0]["terrain"]["slope_level"] is None
    master["records"][0]["terrain"] = {
        "slope_level": 3,
        "confidence": "unknown",
        "verification_status": "source_only",
    }
    with pytest.raises(ValueError, match="evidence"):
        validate_master(master)


def test_verified_review_overrides_derived_candidate_without_mutating_input():
    record = {
        "terrain": {
            "grade_signed_pct": 4.0,
            "confidence": "low",
            "verification_status": "derived_unverified",
        }
    }
    decision = {
        "attribute": "terrain",
        "value": {"grade_signed_pct": 8.2},
        "verification_status": "field_verified",
        "verified_at": "2026-09-27",
        "evidence": "survey/segment-1.csv",
    }
    merged = merge_review(record, decision)
    assert merged["terrain"]["grade_signed_pct"] == 8.2
    assert merged["terrain"]["verification_status"] == "field_verified"
    assert record["terrain"]["grade_signed_pct"] == 4.0


def test_edge_key_and_source_fingerprint_are_deterministic():
    assert edge_key([10, 20, "3"]) == "10|20|3"
    a = {"schema_version": 1, "sources": [{"source_id": "a", "version": "1"}]}
    b = {"sources": [{"version": "1", "source_id": "a"}], "schema_version": 1}
    assert source_manifest_fingerprint(a) == source_manifest_fingerprint(b)


@pytest.mark.parametrize("attribute", ["terrain", "scenery"])
def test_review_requires_independent_evidence(attribute):
    with pytest.raises(ValueError, match="evidence"):
        merge_review(
            {attribute: {}},
            {
                "attribute": attribute,
                "value": {},
                "verification_status": "field_verified",
                "verified_at": "2026-09-27",
            },
        )


@pytest.mark.parametrize("legacy_main", [legacy_dem_main, placeholder_main])
def test_legacy_generators_refuse_default_publication(legacy_main):
    annotations = project_root() / "data" / "road_annotations.json"
    before = annotations.read_bytes()
    assert legacy_main() == 2
    assert annotations.read_bytes() == before


def test_registered_sources_have_reproducibility_and_license_fields():
    import json

    path = project_root() / "data" / "edge_attribute_sources.json"
    sources = json.loads(path.read_text(encoding="utf-8"))["sources"]
    required = {
        "source_id", "source_type", "version", "acquired_at", "crs",
        "resolution_m", "sha256", "license", "redistribution", "local_path",
    }
    assert {row["source_id"] for row in sources} >= {
        "osm_graphml_current", "poi_master_current", "legacy_road_annotations"
    }
    assert all(required <= set(row) for row in sources)
