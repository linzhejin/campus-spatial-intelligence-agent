# Slope and Scenery Master Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the apparently complete but mostly placeholder slope/scenery annotations with a traceable edge-attribute master, reproducible derivation pipeline, human-review layer, quality-gated runtime snapshot, and route-level uncertainty reporting.

**Architecture:** Current OSM edges, POIs, legacy annotations, versioned offline DEM inputs, optional open landscape layers, and verified decisions enter separate source records. Automated derivations write candidates into a master file; reviewed decisions override them; a publisher binds approved attributes to the current graph and creates the small runtime snapshot consumed by routing. Unknown values remain unknown throughout the pipeline.

**Tech Stack:** Python 3, NetworkX, Shapely, Rasterio and NumPy in the development environment, pytest, GraphML, GeoJSON, and versioned JSON artifacts.

## Global Constraints

- The existing OSM plus Amap mixed architecture remains in place; this work fixes data use and provenance before adding other sources.
- Current `data/road_annotations.json` is legacy input, not ground truth.
- Records whose note says “占位” become `unknown`; they never become medium level by default.
- Continuous measurements and component features are stored before 1–5 derived levels.
- Slope is directional and stores signed grade, absolute grade, elevation gain, elevation loss, and a high-percentile grade when a profile exists.
- A 30 m DEM cannot produce medium or high confidence for an edge shorter than 90 m.
- Stairs, ramps, elevators, escalators, bridges, tunnels, and indoor links are explicit facility facts and are not inferred only from DEM values.
- Scenery stores `greenery`, `shade`, `water`, `heritage`, `quietness`, `seasonality`, and `viewpoint` separately.
- Scenery v1 is `0.25 greenery + 0.20 shade + 0.15 water + 0.15 heritage + 0.15 quietness + 0.10 seasonality`; missing components are omitted and the remaining weights are renormalized.
- Missing scenery components are never treated as zero; component coverage lowers confidence.
- Confidence is one of `high|medium|low|unknown`; verification is one of `source_only|derived_unverified|field_verified|institution_verified|rejected`.
- Human or institution decisions are append-only evidence and cannot be overwritten by an automated derivation.
- Runtime uses only a snapshot whose graph fingerprint matches the loaded graph.
- A flat-route response is degraded when unknown slope exceeds 20% of route length.
- A scenery-route response is degraded when valid scenery feature coverage is below 80% of route length.
- Commercial map labels, photos, and route results remain online comparison evidence and do not enter the redistributable master without permission.
- Formal paper evaluation of flat, scenery, or profile recommendation waits for the corresponding quality gate.

---

## File Map

**Create**

- `spatial/edge_attributes.py` — schemas, confidence ordering, review merge, runtime merge, and route-quality summaries.
- `scripts/network/edge_attribute_paths.py` — one correct project-root resolver and default input/output paths.
- `scripts/network/build_edge_attribute_master.py` — audit current graph and migrate legacy evidence without turning placeholders into facts.
- `scripts/network/derive_terrain_attributes.py` — versioned offline DEM profile sampling and directional terrain candidates.
- `scripts/network/derive_scenery_attributes.py` — interpretable component derivation from registered open layers and the POI master.
- `scripts/network/publish_edge_attributes.py` — merge reviews, enforce binding/version checks, publish runtime snapshot, and write quality report.
- `scripts/validate/validate_edge_attributes.py` — structural and distribution validation command.
- `tests/test_edge_attribute_schema.py` — schema, unknown, confidence, and review precedence tests.
- `tests/test_edge_attribute_migration.py` — legacy placeholder migration and stable binding tests.
- `tests/test_terrain_derivation.py` — signed direction, profile, resolution, and facility tests.
- `tests/test_scenery_derivation.py` — component formula, missing values, and confidence tests.
- `tests/test_edge_attribute_publish.py` — graph fingerprint, review precedence, and publication gate tests.
- `data/edge_attribute_sources.json` — source register with generated checksums and licensing scope.
- `data/edge_attribute_master.json` — candidate and accepted edge attributes.
- `data/edge_attribute_review_decisions.json` — empty reviewed-decision envelope plus future evidence.
- `data/edge_attribute_quality_report.json` — generated counts, coverage, high-risk queue, and publication decisions.

**Modify**

- `requirements-dev.txt` — add offline raster processing dependencies.
- `scripts/network/compute_slope_from_dem.py` — retire online write-back behavior and redirect users to the new offline command.
- `scripts/network/generate_placeholder_annotations.py` — stop publishing placeholder facts; retain only an explicit legacy-audit entry point.
- `scripts/network/export_edges_for_annotation.py` and other `scripts/network/*.py` — use the shared root resolver.
- `spatial/network.py` — load only the published, graph-matched snapshot and expose separate terrain/scenery coverage.
- `spatial/routing.py` — consume confidence-aware fields and report route-level data quality.
- `api/routes.py` and `agents/tools.py` — return quality flags and evidence-backed explanation fields.
- `scripts/validate/check_deployment_readiness.py` — block formal flat/scenery readiness when data gates fail.
- `tests/test_network.py`, `tests/test_routing.py`, `tests/test_deployment_readiness.py` — runtime regression coverage.
- `data/road_annotations.json` — becomes a generated compatibility snapshot; never edited directly after migration.

---

### Task 1: Establish Schemas, Paths, and Source Registration

**Files:**
- Create: `spatial/edge_attributes.py`
- Create: `scripts/network/edge_attribute_paths.py`
- Create: `tests/test_edge_attribute_schema.py`
- Create: `data/edge_attribute_sources.json`
- Create: `data/edge_attribute_review_decisions.json`
- Modify: `requirements-dev.txt`
- Modify: `scripts/network/export_edges_for_annotation.py`
- Modify: `scripts/network/compute_slope_from_dem.py`
- Modify: `scripts/network/generate_placeholder_annotations.py`

**Interfaces:**
- Produces: `project_root() -> Path`, `edge_key(edge_id) -> str`, `validate_master(data) -> dict`, `merge_review(record, decision) -> dict`, `source_manifest_fingerprint(manifest) -> str`.
- Source register entries contain `source_id`, `source_type`, `version`, `acquired_at`, `crs`, `resolution_m`, `sha256`, `license`, `redistribution`, and `local_path`.

- [ ] **Step 1: Write failing schema and path tests**

```python
# tests/test_edge_attribute_schema.py
from pathlib import Path
import pytest

from scripts.network.edge_attribute_paths import project_root
from spatial.edge_attributes import merge_review, validate_master


def test_network_script_root_is_repository_root():
    root = project_root()
    assert (root / "spatial").is_dir()
    assert (root / "data" / "whu_road_network.graphml").is_file()
    assert root.name == "campus-spatial-intelligence-agent"


def test_unknown_is_valid_but_default_level_without_evidence_is_not():
    master = {
        "schema_version": 1,
        "network_version": "graph-v1",
        "records": [{
            "segment_id": "seg-1",
            "network_bindings": [{"edge_id": [1, 2, 0], "geometry_hash": "g", "source_refs": []}],
            "terrain": {"slope_level": None, "confidence": "unknown", "verification_status": "source_only"},
            "scenery": {"scenery_level": None, "confidence": "unknown", "verification_status": "source_only"},
        }],
    }
    assert validate_master(master)["records"][0]["terrain"]["slope_level"] is None
    master["records"][0]["terrain"] = {
        "slope_level": 3, "confidence": "unknown", "verification_status": "source_only",
    }
    with pytest.raises(ValueError, match="evidence"):
        validate_master(master)


def test_verified_review_overrides_derived_candidate():
    record = {"terrain": {"grade_signed_pct": 4.0, "confidence": "low", "verification_status": "derived_unverified"}}
    decision = {
        "attribute": "terrain", "value": {"grade_signed_pct": 8.2},
        "verification_status": "field_verified", "verified_at": "2026-09-27",
        "evidence": "survey/segment-1.csv",
    }
    merged = merge_review(record, decision)
    assert merged["terrain"]["grade_signed_pct"] == 8.2
    assert merged["terrain"]["verification_status"] == "field_verified"
```

- [ ] **Step 2: Run and verify the new modules are missing**

Run: `python -m pytest tests/test_edge_attribute_schema.py -q`

Expected: FAIL importing `scripts.network.edge_attribute_paths` or `spatial.edge_attributes`.

- [ ] **Step 3: Implement one correct path resolver**

```python
# scripts/network/edge_attribute_paths.py
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


ROOT = project_root()
DATA_DIR = ROOT / "data"
GRAPH_PATH = DATA_DIR / "whu_road_network.graphml"
LEGACY_ANNOTATIONS_PATH = DATA_DIR / "road_annotations.json"
SOURCES_PATH = DATA_DIR / "edge_attribute_sources.json"
MASTER_PATH = DATA_DIR / "edge_attribute_master.json"
REVIEWS_PATH = DATA_DIR / "edge_attribute_review_decisions.json"
QUALITY_REPORT_PATH = DATA_DIR / "edge_attribute_quality_report.json"
```

Replace every `os.path.dirname(os.path.dirname(__file__))` under `scripts/network/` with this shared module. All commands accept explicit `--graph`, `--input`, and `--output`; defaults use these constants.

- [ ] **Step 4: Implement validation and review precedence**

```python
# spatial/edge_attributes.py
from copy import deepcopy
from hashlib import sha256
import json

CONFIDENCE = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
VERIFICATION = {
    "source_only", "derived_unverified", "field_verified", "institution_verified", "rejected",
}


def edge_key(edge_id):
    if not isinstance(edge_id, (list, tuple)) or len(edge_id) != 3:
        raise ValueError("edge_id must contain u, v, key")
    return f"{edge_id[0]}|{edge_id[1]}|{int(edge_id[2])}"


def validate_attribute(name, value):
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
    if data.get("schema_version") != 1 or not data.get("network_version"):
        raise ValueError("invalid master envelope")
    seen = set()
    for record in data.get("records", []):
        segment_id = record.get("segment_id")
        if not segment_id or segment_id in seen:
            raise ValueError("segment_id must be unique")
        seen.add(segment_id)
        validate_attribute("terrain", record.get("terrain", {}))
        validate_attribute("scenery", record.get("scenery", {}))
    return data


def merge_review(record, decision):
    if decision.get("verification_status") not in {"field_verified", "institution_verified", "rejected"}:
        raise ValueError("review must be independently verified or rejected")
    if not decision.get("evidence") or not decision.get("verified_at"):
        raise ValueError("review requires evidence and verified_at")
    out = deepcopy(record)
    attribute = decision["attribute"]
    out.setdefault(attribute, {}).update(deepcopy(decision.get("value") or {}))
    out[attribute]["verification_status"] = decision["verification_status"]
    out[attribute]["confidence"] = "high"
    return out


def source_manifest_fingerprint(manifest):
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()
```

- [ ] **Step 5: Create valid empty envelopes and add offline raster dependencies**

```json
{
  "schema_version": 1,
  "sources": []
}
```

Use that envelope for `edge_attribute_sources.json`. Use this for reviews:

```json
{
  "schema_version": 1,
  "decisions": []
}
```

Add `numpy>=1.26` and `rasterio>=1.3` to `requirements-dev.txt`; do not add them to production runtime requirements.

- [ ] **Step 6: Disable legacy scripts from publishing facts**

`compute_slope_from_dem.py` and `generate_placeholder_annotations.py` may keep read-only `--audit` behavior, but their default execution exits with a message directing the operator to `build_edge_attribute_master.py`, `derive_terrain_attributes.py`, and `publish_edge_attributes.py`. They must never write `road_annotations.json` directly.

- [ ] **Step 7: Run schema tests**

Run: `python -m pytest tests/test_edge_attribute_schema.py -q`

Expected: PASS.

- [ ] **Step 8: Commit the data contract**

```bash
git add spatial/edge_attributes.py scripts/network/edge_attribute_paths.py scripts/network/export_edges_for_annotation.py scripts/network/compute_slope_from_dem.py scripts/network/generate_placeholder_annotations.py tests/test_edge_attribute_schema.py data/edge_attribute_sources.json data/edge_attribute_review_decisions.json requirements-dev.txt
git commit -m "feat(data): establish edge attribute master schema"
```

---

### Task 2: Audit and Migrate Current Annotations Without Inventing Certainty

**Files:**
- Create: `scripts/network/build_edge_attribute_master.py`
- Create: `tests/test_edge_attribute_migration.py`
- Create: `data/edge_attribute_master.json` through the command
- Modify: `data/edge_attribute_sources.json` through the command

**Interfaces:**
- Produces: `graph_fingerprint(G) -> str`, `geometry_hash(G, u, v, key) -> str`, `segment_id(...) -> str`, `migrate_legacy_record(...) -> dict`.
- CLI: `python scripts/network/build_edge_attribute_master.py --graph data/whu_road_network.graphml --legacy data/road_annotations.json --output data/edge_attribute_master.json --sources data/edge_attribute_sources.json`.

- [ ] **Step 1: Write failing migration tests**

```python
# tests/test_edge_attribute_migration.py
from scripts.network.build_edge_attribute_master import migrate_legacy_record


def test_placeholder_levels_become_unknown():
    legacy = {
        "edge_id": [1, 2, 0], "slope_level": 3, "scenery_level": 3,
        "note": "坡度占位，待实测; 临近某景点 (80m)",
    }
    record = migrate_legacy_record(legacy, binding={"edge_id": [1, 2, 0]})
    assert record["terrain"]["slope_level"] is None
    assert record["terrain"]["confidence"] == "unknown"
    assert record["scenery"]["scenery_level"] is None
    assert record["scenery"]["confidence"] == "unknown"


def test_old_dem_text_is_low_confidence_candidate_not_field_measurement():
    legacy = {
        "edge_id": [1, 2, 0], "slope_level": 4,
        "note": "DEM实测 坡度10.0%（Δelev=5m）",
    }
    terrain = migrate_legacy_record(legacy, binding={"edge_id": [1, 2, 0]})["terrain"]
    assert terrain["grade_abs_pct"] == 10.0
    assert terrain["confidence"] == "low"
    assert terrain["verification_status"] == "derived_unverified"
    assert terrain["method_version"] == "legacy_endpoint_dem_unversioned"
```

- [ ] **Step 2: Run and verify the migration command is missing**

Run: `python -m pytest tests/test_edge_attribute_migration.py -q`

Expected: FAIL importing `scripts.network.build_edge_attribute_master`.

- [ ] **Step 3: Implement stable graph, geometry, and segment fingerprints**

```python
def canonical_geometry_points(G, u, v, key):
    data = G[u][v][key]
    geometry = data.get("geometry")
    if geometry is not None and hasattr(geometry, "coords"):
        points = list(geometry.coords)
    else:
        points = [(float(G.nodes[u]["x"]), float(G.nodes[u]["y"])),
                  (float(G.nodes[v]["x"]), float(G.nodes[v]["y"]))]
    return [[round(float(x), 7), round(float(y), 7)] for x, y in points]


def geometry_hash(G, u, v, key):
    raw = json.dumps(canonical_geometry_points(G, u, v, key), separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


def segment_id(source_refs, geom_hash, direction):
    canonical_refs = sorted(
        json.dumps(ref, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for ref in source_refs
    )
    identity = {"source_refs": canonical_refs, "geometry_hash": geom_hash, "direction": direction}
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return "seg_" + sha256(raw.encode("utf-8")).hexdigest()[:20]
```

`graph_fingerprint()` hashes sorted node coordinates and sorted directed edge source refs/geometry hashes; it does not hash mutable runtime annotations.

- [ ] **Step 4: Implement conservative legacy migration**

Rules are exact:

```python
def migrate_legacy_record(legacy, binding):
    note = str(legacy.get("note") or "")
    placeholder = "占位" in note
    terrain = {
        "elevation_start_m": None, "elevation_end_m": None,
        "grade_signed_pct": None, "grade_abs_pct": None, "grade_p90_pct": None,
        "elevation_gain_m": None, "elevation_loss_m": None,
        "facility_type": None, "slope_level": None,
        "source_refs": ["legacy_road_annotations"],
        "method_version": "legacy_placeholder",
        "confidence": "unknown", "verification_status": "source_only",
    }
    if "DEM实测" in note and not placeholder:
        match = re.search(r"坡度([0-9.]+)%", note)
        if match:
            terrain.update({
                "grade_abs_pct": float(match.group(1)),
                "slope_level": legacy.get("slope_level"),
                "method_version": "legacy_endpoint_dem_unversioned",
                "confidence": "low", "verification_status": "derived_unverified",
            })
    scenery = {
        "greenery": None, "shade": None, "water": None, "heritage": None,
        "quietness": None, "seasonality": None, "viewpoint": None,
        "scenery_score": None, "scenery_level": None,
        "source_refs": ["legacy_road_annotations"],
        "method_version": "legacy_poi_name_heuristic",
        "confidence": "unknown", "verification_status": "source_only",
    }
    return {"network_bindings": [binding], "terrain": terrain, "scenery": scenery}
```

- [ ] **Step 5: Register current graph, POIs, and legacy file with real checksums**

The command calculates SHA-256 from bytes and writes these source IDs:

- `osm_graphml_current`, license `ODbL-1.0`, redistribution `share_alike`;
- `poi_master_current`, with its mixed-source publication scope copied from POI provenance rather than guessed;
- `legacy_road_annotations`, redistribution `internal_audit_only` because it contains unverified derived values.

- [ ] **Step 6: Generate the first conservative master**

Run: `python scripts/network/build_edge_attribute_master.py`

Expected: writes one master record for every current directed graph edge, reports exactly how many legacy records matched, and reports placeholder terrain/scenery as unknown rather than as covered.

- [ ] **Step 7: Run migration tests and deterministic regeneration check**

Run: `python -m pytest tests/test_edge_attribute_migration.py tests/test_edge_attribute_schema.py -q`

Expected: PASS.

Run the builder twice to two temporary files and compare their SHA-256 after excluding only `generated_at`; the normalized contents must match.

- [ ] **Step 8: Commit the migrated master and sources**

```bash
git add scripts/network/build_edge_attribute_master.py tests/test_edge_attribute_migration.py data/edge_attribute_master.json data/edge_attribute_sources.json
git commit -m "data(edges): migrate legacy attributes conservatively"
```

---

### Task 3: Derive Directional Terrain from a Versioned Offline DEM

**Files:**
- Create: `scripts/network/derive_terrain_attributes.py`
- Create: `tests/test_terrain_derivation.py`
- Modify: `data/edge_attribute_sources.json` through the command
- Modify: `data/edge_attribute_master.json` through the command

**Interfaces:**
- Produces: `sample_profile(dataset, points, spacing_m) -> list[float|None]`, `terrain_candidate(profile, length_m, resolution_m, facility_type) -> dict`.
- CLI requires `--dem PATH --source-id ID --version VERSION --license TEXT --redistribution open|restricted` and writes candidates to an explicit master output.

- [ ] **Step 1: Write failing directional terrain tests**

```python
# tests/test_terrain_derivation.py
import pytest
from scripts.network.derive_terrain_attributes import terrain_candidate


def test_direction_reverses_grade_and_gain_loss():
    forward = terrain_candidate([100.0, 102.0, 106.0], length_m=100.0, resolution_m=10.0)
    reverse = terrain_candidate([106.0, 102.0, 100.0], length_m=100.0, resolution_m=10.0)
    assert forward["grade_signed_pct"] == pytest.approx(6.0)
    assert reverse["grade_signed_pct"] == pytest.approx(-6.0)
    assert forward["elevation_gain_m"] == reverse["elevation_loss_m"]
    assert forward["elevation_loss_m"] == reverse["elevation_gain_m"]


def test_30m_dem_short_edge_is_low_confidence():
    result = terrain_candidate([100.0, 104.0], length_m=60.0, resolution_m=30.0)
    assert result["confidence"] == "low"


def test_steps_remain_explicit_facility_fact():
    result = terrain_candidate([100.0, 102.0], length_m=40.0, resolution_m=5.0, facility_type="steps")
    assert result["facility_type"] == "steps"
```

- [ ] **Step 2: Run and verify the terrain module is missing**

Run: `python -m pytest tests/test_terrain_derivation.py -q`

Expected: FAIL importing `scripts.network.derive_terrain_attributes`.

- [ ] **Step 3: Implement continuous terrain metrics**

```python
import numpy

SLOPE_BINS = ((2.0, 1), (5.0, 2), (8.0, 3), (15.0, 4), (float("inf"), 5))


def unknown_terrain(facility_type=None):
    return {
        "elevation_start_m": None, "elevation_end_m": None,
        "grade_signed_pct": None, "grade_abs_pct": None, "grade_p90_pct": None,
        "elevation_gain_m": None, "elevation_loss_m": None,
        "facility_type": facility_type, "slope_level": None,
        "confidence": "unknown", "verification_status": "source_only",
        "method_version": "terrain_profile_v1",
    }


def terrain_candidate(profile, length_m, resolution_m, facility_type=None):
    values = [float(v) for v in profile if v is not None]
    if len(values) < 2 or length_m <= 0:
        return unknown_terrain(facility_type=facility_type)
    deltas = [b - a for a, b in zip(values, values[1:])]
    grade_signed = (values[-1] - values[0]) / length_m * 100.0
    grade_abs = abs(grade_signed)
    sample_run = length_m / max(1, len(values) - 1)
    local_grades = [abs(delta) / sample_run * 100.0 for delta in deltas]
    p90 = float(numpy.percentile(local_grades, 90))
    confidence = "low" if length_m < 3.0 * resolution_m else "medium"
    level = next(level for threshold, level in SLOPE_BINS if grade_abs < threshold)
    return {
        "elevation_start_m": values[0], "elevation_end_m": values[-1],
        "grade_signed_pct": round(grade_signed, 3), "grade_abs_pct": round(grade_abs, 3),
        "grade_p90_pct": round(p90, 3),
        "elevation_gain_m": round(sum(max(0.0, d) for d in deltas), 3),
        "elevation_loss_m": round(sum(max(0.0, -d) for d in deltas), 3),
        "facility_type": facility_type, "slope_level": level,
        "confidence": confidence, "verification_status": "derived_unverified",
        "method_version": "terrain_profile_v1",
    }
```

- [ ] **Step 4: Sample the complete edge geometry at source-aware spacing**

Read the DEM CRS from Rasterio, transform WGS-84 edge points into that CRS, interpolate along the complete line, and use `spacing_m = max(resolution_m, 5.0)`. Preserve nodata as `None`; do not interpolate across a nodata run longer than two samples. Detect `steps`, `ramp`, `elevator`, `escalator`, `bridge`, `tunnel`, and `indoor` from structured graph tags before numeric derivation.

- [ ] **Step 5: Register the DEM before modifying candidates**

The command refuses to run unless `--version`, `--license`, and `--redistribution` are present. It records the file SHA-256, CRS, pixel size, acquisition date, and local path. The master records refer to the source ID and method version; they do not embed the raster.

- [ ] **Step 6: Run terrain tests with a synthetic raster fixture**

Run: `python -m pytest tests/test_terrain_derivation.py -q`

Expected: PASS, including signed reverse directions and the 90 m confidence boundary for a 30 m raster.

- [ ] **Step 7: Run the derivation only when a real versioned DEM file is available**

Use the registered local path and metadata from `data/edge_attribute_sources.json`; for the standard execution layout the command is:

```bash
python scripts/network/derive_terrain_attributes.py --dem cache/dem/whu_dem.tif --source-id whu_dem_current --source-manifest data/edge_attribute_sources.json
```

Expected: refuses to run if `whu_dem_current` lacks version, checksum, resolution, license, or redistribution metadata; otherwise prints counts for medium, low, unknown, nodata, facility types, and high-risk extremes and writes a candidate master without publishing the runtime snapshot.

- [ ] **Step 8: Commit code separately from source-dependent generated data**

```bash
git add scripts/network/derive_terrain_attributes.py tests/test_terrain_derivation.py requirements-dev.txt
git commit -m "feat(data): derive directional terrain attributes"
```

If a redistributable DEM was actually supplied and the generated master diff is reviewed, commit only the JSON master and source manifest in a separate `data(edges): add versioned terrain candidates` commit. Restricted raster files never enter Git.

---

### Task 4: Derive Interpretable Scenery Components

**Files:**
- Create: `scripts/network/derive_scenery_attributes.py`
- Create: `tests/test_scenery_derivation.py`
- Modify: `data/edge_attribute_sources.json` through the command
- Modify: `data/edge_attribute_master.json` through the command

**Interfaces:**
- Produces: `scenery_score(components) -> tuple[float|None, float]`; the second value is weighted feature coverage.
- CLI accepts registered GeoJSON layers for vegetation, canopy/shade, water, heritage, traffic/quietness, seasonal features, and viewpoints. Missing layers remain missing.

- [ ] **Step 1: Write failing formula and missing-value tests**

```python
# tests/test_scenery_derivation.py
import pytest
from scripts.network.derive_scenery_attributes import scenery_score


def test_v1_formula_uses_declared_weights():
    score, coverage = scenery_score({
        "greenery": 1.0, "shade": 0.8, "water": 0.6,
        "heritage": 0.4, "quietness": 0.2, "seasonality": 0.0,
    })
    assert score == pytest.approx(0.25 + 0.16 + 0.09 + 0.06 + 0.03)
    assert coverage == pytest.approx(1.0)


def test_missing_components_are_renormalized_not_zeroed():
    score, coverage = scenery_score({"greenery": 1.0, "water": 0.0})
    assert score == pytest.approx(0.25 / (0.25 + 0.15))
    assert coverage == pytest.approx(0.40)


def test_no_components_stays_unknown():
    assert scenery_score({}) == (None, 0.0)
```

- [ ] **Step 2: Run and verify the scenery module is missing**

Run: `python -m pytest tests/test_scenery_derivation.py -q`

Expected: FAIL importing `scripts.network.derive_scenery_attributes`.

- [ ] **Step 3: Implement the transparent formula**

```python
SCENERY_WEIGHTS = {
    "greenery": 0.25, "shade": 0.20, "water": 0.15,
    "heritage": 0.15, "quietness": 0.15, "seasonality": 0.10,
}


def scenery_score(components):
    present = {
        key: min(1.0, max(0.0, float(components[key])))
        for key in SCENERY_WEIGHTS if components.get(key) is not None
    }
    if not present:
        return None, 0.0
    coverage = sum(SCENERY_WEIGHTS[key] for key in present)
    score = sum(SCENERY_WEIGHTS[key] * value for key, value in present.items()) / coverage
    return round(score, 6), round(coverage, 6)
```

Map score to levels with a versioned rule in the output metadata: `[0.0,0.2)→1`, `[0.2,0.4)→2`, `[0.4,0.6)→3`, `[0.6,0.8)→4`, `[0.8,1.0]→5`. Preserve the continuous score.

- [ ] **Step 4: Implement component-specific derivation rather than one POI-distance score**

- `greenery`: buffered overlap with a registered vegetation or land-cover layer.
- `shade`: registered tree-canopy overlap only; when no canopy layer exists, leave `null`.
- `water`: distance-decay to registered water geometry, reaching zero at the configured evidence radius.
- `heritage`: distance-decay to provenance-bearing heritage polygons/POIs.
- `quietness`: inverse normalized motor-road exposure from structured road class; this may be low confidence without observed traffic.
- `seasonality`: active dated campus POI/layer tags only; store the active month range.
- `viewpoint`: evidence flag stored separately and excluded from v1 arithmetic.

Each component stores its own `source_refs`, `method_version`, and confidence. Road names may create a low-confidence review candidate but may not fill a component value in the published master.

- [ ] **Step 5: Derive confidence from coverage and source strength**

Use `unknown` at zero coverage, `low` below `0.80` weighted coverage or when every present component is inferred, `medium` at coverage `>=0.80` with at least one direct versioned geometry source, and `high` only after field/institution review or independent matching sources. Do not upgrade confidence just because the final score is high.

- [ ] **Step 6: Run scenery tests**

Run: `python -m pytest tests/test_scenery_derivation.py -q`

Expected: PASS; missing components are renormalized and reduce coverage rather than becoming zeros.

- [ ] **Step 7: Generate candidates from currently registered sources**

Run: `python scripts/network/derive_scenery_attributes.py --master data/edge_attribute_master.json --sources data/edge_attribute_sources.json`

Expected: writes only components supported by registered sources, prints per-component known/unknown coverage, and leaves unavailable shade or canopy data as `null`.

- [ ] **Step 8: Commit code and reviewed redistributable candidates**

```bash
git add scripts/network/derive_scenery_attributes.py tests/test_scenery_derivation.py
git commit -m "feat(data): derive explainable scenery components"
```

Commit changed master/source JSON separately only after checking source licenses and the distribution report.

---

### Task 5: Merge Reviews and Publish a Graph-Bound Runtime Snapshot

**Files:**
- Create: `scripts/network/publish_edge_attributes.py`
- Create: `tests/test_edge_attribute_publish.py`
- Create: `data/edge_attribute_quality_report.json` through the command
- Modify: `data/road_annotations.json` through the command
- Modify: `spatial/edge_attributes.py`

**Interfaces:**
- Produces: `publish(master, reviews, graph, sources) -> tuple[snapshot, report]`.
- Runtime snapshot contains `graph_source_sha256`, `attribute_version`, and per-edge terrain/scenery fields plus confidence/status.

- [ ] **Step 1: Write failing publication tests**

```python
# tests/test_edge_attribute_publish.py
import networkx as nx
import pytest
from scripts.network.build_edge_attribute_master import geometry_hash, graph_fingerprint
from scripts.network.publish_edge_attributes import publish


@pytest.fixture
def graph():
    G = nx.MultiDiGraph()
    G.add_node("1", x=114.36, y=30.535)
    G.add_node("2", x=114.361, y=30.535)
    G.add_edge("1", "2", key=0, length=100.0)
    return G


@pytest.fixture
def sources():
    return {"schema_version": 1, "sources": [{
        "source_id": "legacy", "version": "1", "sha256": "x",
        "license": "internal", "redistribution": "internal_audit_only",
    }]}


@pytest.fixture
def master(graph):
    return {"schema_version": 1, "network_version": graph_fingerprint(graph), "records": [{
        "segment_id": "seg-1",
        "network_bindings": [{"edge_id": ["1", "2", 0], "geometry_hash": geometry_hash(graph, "1", "2", 0), "source_refs": []}],
        "terrain": {"slope_level": None, "confidence": "unknown", "verification_status": "source_only"},
        "scenery": {"scenery_level": None, "confidence": "unknown", "verification_status": "source_only"},
    }]}


def test_publication_rejects_wrong_graph_fingerprint(graph, master, sources):
    master["network_version"] = "different-graph"
    with pytest.raises(ValueError, match="network_version"):
        publish(master, {"schema_version": 1, "decisions": []}, graph, sources)


def test_verified_decision_wins_and_unknown_is_not_default_three(graph, master, sources):
    segment_id = master["records"][0]["segment_id"]
    reviews = {"schema_version": 1, "decisions": [{
        "segment_id": segment_id, "attribute": "terrain",
        "value": {"slope_level": 2, "grade_signed_pct": 3.0},
        "verification_status": "field_verified", "verified_at": "2026-09-27",
        "evidence": "survey/test.csv",
    }]}
    snapshot, report = publish(master, reviews, graph, sources)
    assert snapshot["edges"][0]["slope_level"] == 2
    unknown = [edge for edge in snapshot["edges"] if edge["terrain_confidence"] == "unknown"]
    assert all(edge["slope_level"] is None for edge in unknown)
```

- [ ] **Step 2: Run and verify the publisher is missing**

Run: `python -m pytest tests/test_edge_attribute_publish.py -q`

Expected: FAIL importing `scripts.network.publish_edge_attributes`.

- [ ] **Step 3: Implement deterministic review merge and binding**

Index records by `segment_id`, validate every review, apply reviews after automatic candidates, then bind by exact `network_version + edge_id + geometry_hash`. Never use the old “same u/v, first key” fallback for publication. Put unmatched, ambiguous, or hash-changed records into explicit report arrays.

```python
runtime_edge = {
    "edge_id": binding["edge_id"],
    "segment_id": record["segment_id"],
    "slope_level": terrain.get("slope_level"),
    "grade_signed_pct": terrain.get("grade_signed_pct"),
    "grade_p90_pct": terrain.get("grade_p90_pct"),
    "elevation_gain_m": terrain.get("elevation_gain_m"),
    "elevation_loss_m": terrain.get("elevation_loss_m"),
    "facility_type": terrain.get("facility_type"),
    "terrain_confidence": terrain.get("confidence", "unknown"),
    "terrain_verification_status": terrain.get("verification_status", "source_only"),
    "scenery_level": scenery.get("scenery_level"),
    "scenery_score": scenery.get("scenery_score"),
    "scenery_features": {key: scenery.get(key) for key in SCENERY_WEIGHTS},
    "scenery_feature_coverage": scenery.get("feature_coverage", 0.0),
    "scenery_confidence": scenery.get("confidence", "unknown"),
    "scenery_verification_status": scenery.get("verification_status", "source_only"),
    "method_versions": [terrain.get("method_version"), scenery.get("method_version")],
}
```

- [ ] **Step 4: Calculate report metrics by edge count and length**

Report terrain/scenery known, low, medium, high, unknown, reviewed, unbound, and rejected counts and meters. Include distributions of continuous grade, component coverage, and levels. Create prioritized queues for representative OD corridors, mountain/steps, cross-division links, gates including 澄波门 and 玉兰2门, high-score islands, extreme DEM jumps, and changed geometry.

- [ ] **Step 5: Enforce publication gates without deleting useful candidates**

The publisher always writes the report. It writes the runtime snapshot only when structure and graph binding pass. It marks `flat_formal_ready` and `scenery_formal_ready` separately; failure leaves candidate master data intact and prevents only formal claims/experiments.

- [ ] **Step 6: Run publication tests and publish the current snapshot**

Run: `python -m pytest tests/test_edge_attribute_publish.py tests/test_edge_attribute_schema.py -q`

Expected: PASS.

Run: `python scripts/network/publish_edge_attributes.py`

Expected: writes deterministic `road_annotations.json` and `edge_attribute_quality_report.json`; unknown values stay null; every rejected/unbound candidate appears in the report.

- [ ] **Step 7: Commit publisher and generated snapshot together**

```bash
git add scripts/network/publish_edge_attributes.py tests/test_edge_attribute_publish.py data/road_annotations.json data/edge_attribute_quality_report.json
git commit -m "feat(data): publish graph-bound edge attributes"
```

---

### Task 6: Load Confidence-Aware Attributes and Report Route Quality

**Files:**
- Modify: `spatial/network.py:18-129,331-442`
- Modify: `spatial/edge_attributes.py`
- Modify: `spatial/routing.py:727-832,1017-1287`
- Modify: `api/routes.py:782-808,1105-1144`
- Modify: `agents/tools.py:520-610`
- Modify: `tests/test_network.py`
- Modify: `tests/test_routing.py`

**Interfaces:**
- Produces: `get_attribute_coverage() -> {terrain: float, scenery: float}` and `summarize_route_quality(G, route_edges) -> dict`.
- Route response adds `data_quality.terrain` and `data_quality.scenery` with known/unknown length ratios, confidence distribution, and degraded flags.

- [ ] **Step 1: Write failing runtime quality tests**

```python
# tests/test_network.py
def test_unknown_attributes_are_not_merged_as_level_three(tmp_path):
    G = _make_graph(1)
    snapshot = tmp_path / "road_annotations.json"
    snapshot.write_text(json.dumps({
        "graph_source_sha256": "test",
        "edges": [{"edge_id": ["0", "1", 0], "slope_level": None,
                   "terrain_confidence": "unknown", "scenery_level": None,
                   "scenery_confidence": "unknown"}],
    }), encoding="utf-8")
    network._merge_annotations(G, str(snapshot), expected_graph_hash="test")
    assert "slope_level" not in G["0"]["1"][0]
    assert G["0"]["1"][0]["terrain_confidence"] == "unknown"
```

```python
# tests/test_routing.py
@pytest.fixture
def quality_graph():
    G = nx.MultiDiGraph()
    for node in (1, 2, 3):
        G.add_node(node, x=114.36, y=30.535)
    for u, v in ((1, 2), (2, 3)):
        G.add_edge(u, v, key=0, length=100.0,
                   terrain_confidence="unknown", slope_level=None,
                   scenery_confidence="low", scenery_feature_coverage=0.40,
                   scenery_level=None)
    return G


def test_route_quality_degrades_flat_when_unknown_exceeds_twenty_percent(quality_graph):
    quality = summarize_route_quality(quality_graph, [(1, 2, 0), (2, 3, 0)])
    assert quality["terrain"]["unknown_length_ratio"] > 0.20
    assert quality["terrain"]["status"] == "terrain_quality_degraded"


def test_route_quality_degrades_scenery_below_eighty_percent(quality_graph):
    quality = summarize_route_quality(quality_graph, [(1, 2, 0), (2, 3, 0)])
    assert quality["scenery"]["valid_feature_length_ratio"] < 0.80
    assert quality["scenery"]["status"] == "scenery_quality_degraded"
```

- [ ] **Step 2: Run and verify runtime behavior is still legacy**

Run: `python -m pytest tests/test_network.py tests/test_routing.py -k "unknown_attributes or route_quality" -q`

Expected: FAIL because the merge does not expose confidence-aware coverage or route quality.

- [ ] **Step 3: Make runtime merge exact and version-bound**

Change the signature to `_merge_annotations(G, annotations_path, expected_graph_hash=None)`. It receives the loaded graph fingerprint, rejects a mismatched snapshot, matches exact `(u,v,key)` only, and copies level values only when non-null. It always copies confidence, verification, continuous metrics, and component coverage. Replace `_annotation_coverage_rate` with separate terrain/scenery coverage dictionaries.

- [ ] **Step 4: Add uncertainty-aware route summaries**

```python
def summarize_route_quality(G, route_edges):
    totals = {"length": 0.0, "terrain_unknown": 0.0, "scenery_valid": 0.0}
    confidence = {"terrain": {}, "scenery": {}}
    for u, v, key in route_edges:
        edge = G[u][v][key]
        length = float(edge.get("length", 0) or 0)
        totals["length"] += length
        terrain_conf = edge.get("terrain_confidence", "unknown")
        scenery_conf = edge.get("scenery_confidence", "unknown")
        confidence["terrain"][terrain_conf] = confidence["terrain"].get(terrain_conf, 0.0) + length
        confidence["scenery"][scenery_conf] = confidence["scenery"].get(scenery_conf, 0.0) + length
        if terrain_conf == "unknown" or edge.get("slope_level") is None:
            totals["terrain_unknown"] += length
        if float(edge.get("scenery_feature_coverage", 0.0) or 0.0) >= 0.80:
            totals["scenery_valid"] += length
    total = totals["length"] or 1.0
    terrain_unknown = totals["terrain_unknown"] / total
    scenery_valid = totals["scenery_valid"] / total
    return {
        "terrain": {
            "unknown_length_ratio": round(terrain_unknown, 4),
            "status": "terrain_quality_degraded" if terrain_unknown > 0.20 else "ok",
            "confidence_length_m": confidence["terrain"],
        },
        "scenery": {
            "valid_feature_length_ratio": round(scenery_valid, 4),
            "status": "scenery_quality_degraded" if scenery_valid < 0.80 else "ok",
            "confidence_length_m": confidence["scenery"],
        },
    }
```

- [ ] **Step 5: Use unknown penalties without fabricating values**

For flat or scenery strategies, add a small explicit uncertainty cost based on confidence. Do not substitute level 3. Hard accessibility rules remain separate. Exact shortest ignores soft slope/scenery uncertainty, while its response still reports quality.

```python
CONFIDENCE_PENALTY = {"high": 1.0, "medium": 1.02, "low": 1.08, "unknown": 1.15}
```

Apply terrain confidence only to the slope component and scenery confidence only to the scenery component; never multiply the distance-only shortest branch.

- [ ] **Step 6: Return evidence-backed quality fields**

Attach `data_quality` to direct and compound route responses. Explanations may say “部分路段坡度数据不足，平坦偏好已降级” only when the corresponding status is present. They may mention greenery, water, heritage, or shade only when those component values exist on the chosen edges.

- [ ] **Step 7: Run runtime regression tests**

Run: `python -m pytest tests/test_network.py tests/test_routing.py tests/test_end_to_end.py -q`

Expected: PASS; unknown fields are never loaded as level 3 and route responses expose both quality ratios.

- [ ] **Step 8: Commit runtime quality integration**

```bash
git add spatial/network.py spatial/edge_attributes.py spatial/routing.py api/routes.py agents/tools.py tests/test_network.py tests/test_routing.py tests/test_end_to_end.py
git commit -m "feat(routing): report edge attribute uncertainty"
```

---

### Task 7: Validate the Current Campus Data and Wire Deployment Gates

**Files:**
- Create: `scripts/validate/validate_edge_attributes.py`
- Modify: `scripts/validate/check_deployment_readiness.py`
- Modify: `tests/test_deployment_readiness.py`
- Modify: `docs/development/13_校园空间数据主库与核查.md` if that tracked document exists; otherwise update the nearest tracked data-maintenance document discovered with `rg --files docs`.

**Interfaces:**
- CLI: `python scripts/validate/validate_edge_attributes.py --graph data/whu_road_network.graphml --master data/edge_attribute_master.json --snapshot data/road_annotations.json --report data/edge_attribute_quality_report.json`.
- Exit nonzero on structural, binding, provenance, direction, or explanation-integrity failures. Quality shortfalls are reported as explicit readiness flags rather than hidden.

- [ ] **Step 1: Write failing deployment-gate tests**

```python
# tests/test_deployment_readiness.py
def valid_report():
    return {"representative_routes": [{
        "terrain_unknown_length_ratio": 0.10,
        "scenery_valid_feature_length_ratio": 0.90,
        "critical_edges_resolved": True,
    }]}


def test_flat_formal_readiness_fails_when_unknown_route_share_is_high(tmp_path):
    report = valid_report()
    report["representative_routes"][0]["terrain_unknown_length_ratio"] = 0.21
    result = evaluate_edge_attribute_readiness(report)
    assert result["flat_formal_ready"] is False


def test_scenery_formal_readiness_requires_eighty_percent_feature_coverage():
    report = valid_report()
    report["representative_routes"][0]["scenery_valid_feature_length_ratio"] = 0.79
    result = evaluate_edge_attribute_readiness(report)
    assert result["scenery_formal_ready"] is False
```

- [ ] **Step 2: Run and verify the new readiness evaluator is absent**

Run: `python -m pytest tests/test_deployment_readiness.py -k "flat_formal or scenery_formal" -q`

Expected: FAIL importing or calling `evaluate_edge_attribute_readiness`.

- [ ] **Step 3: Implement comprehensive validation checks**

The readiness helper used by the tests is deterministic and is also imported by the deployment checker:

```python
def evaluate_edge_attribute_readiness(report):
    routes = report.get("representative_routes", [])
    flat_ok = bool(routes) and all(
        route.get("terrain_unknown_length_ratio", 1.0) <= 0.20 for route in routes
    )
    scenery_ok = bool(routes) and all(
        route.get("scenery_valid_feature_length_ratio", 0.0) >= 0.80 for route in routes
    )
    critical_ok = bool(routes) and all(route.get("critical_edges_resolved") for route in routes)
    return {
        "shortest_product_ready": critical_ok,
        "flat_product_ready": critical_ok,
        "scenery_product_ready": critical_ok,
        "flat_formal_ready": flat_ok and critical_ok,
        "scenery_formal_ready": scenery_ok and critical_ok,
    }
```

The validator checks:

1. one exact binding or explicit unbound record for every graph edge;
2. unique segment IDs and exact geometry hashes;
3. source IDs all exist and carry checksum/license/redistribution data;
4. no non-null level has unknown confidence;
5. reverse directions have opposite signed grade and swapped gain/loss within tolerance;
6. facility facts survive publication;
7. no automated process overwrote field/institution decisions;
8. scenery score reproduces from stored components and `scenery_v1` weights;
9. unknown is not included in known coverage;
10. every high-risk edge is in a reviewed, pending, or rejected queue with a reason;
11. runtime snapshot graph fingerprint equals the graph input;
12. representative route reports include both data-quality ratios.

- [ ] **Step 4: Build the high-impact verification queue from actual routes**

Run representative OD pairs through shortest, flat, and scenery candidates. Sort unresolved edges by routed meters and strategy sensitivity. Include named checks for 珞珈山/樱顶 steps, information-science to arts-and-sciences links, 澄波门, 玉兰2门, campus gates, lakefront roads, and geometry changes. The queue stores a reason and evidence needed; it does not mark passability or quality without evidence.

- [ ] **Step 5: Connect separate product and formal-paper gates**

The application may ship shortest routes when structural graph and route-line checks pass. `flat` and `scenery` may remain visible with a degraded explanation during development. `check_deployment_readiness.py` exposes separate booleans:

```python
{
    "shortest_product_ready": True,
    "flat_product_ready": True,
    "scenery_product_ready": True,
    "flat_formal_ready": False,
    "scenery_formal_ready": False,
}
```

Formal flags become true only when every representative formal route meets the 20% terrain-unknown and 80% scenery-feature thresholds and all critical passability edges are resolved.

- [ ] **Step 6: Run the data validation and targeted regressions**

Run: `python scripts/validate/validate_edge_attributes.py`

Expected: exits zero for structural integrity, prints current readiness flags, and writes no files unless `--write-report` is supplied.

Run: `python -m pytest tests/test_edge_attribute_schema.py tests/test_edge_attribute_migration.py tests/test_terrain_derivation.py tests/test_scenery_derivation.py tests/test_edge_attribute_publish.py tests/test_network.py tests/test_routing.py tests/test_deployment_readiness.py -q`

Expected: PASS.

- [ ] **Step 7: Run one final full verification after fixing every failure**

Run: `python -m pytest -q`

Expected: PASS with no failure or error.

Run: `python scripts/validate/check_deployment_readiness.py`

Expected: structural and shortest product readiness pass; flat/scenery formal readiness accurately reflects current data and is never forced green by placeholder coverage.

- [ ] **Step 8: Commit validator, report, and documentation**

```bash
git add scripts/validate/validate_edge_attributes.py scripts/validate/check_deployment_readiness.py tests/test_deployment_readiness.py data/edge_attribute_quality_report.json docs
git commit -m "feat(data): enforce edge attribute quality gates"
```

---

## Completion Gate

The data work is complete for this phase only when:

1. Every current graph edge is exactly bound to a master record or appears in a reasoned unbound list.
2. Every non-null slope/scenery value has source references, method version, confidence, and verification status.
3. All 9,510 legacy placeholder records are represented as unknown candidates unless replaced by stronger evidence.
4. Directional terrain values pass reverse-edge consistency checks.
5. DEM-derived results record source resolution and do not claim medium/high confidence for 30 m DEM edges below 90 m.
6. Scenery scores reproduce from component data and no missing component is treated as zero.
7. Human and institution decisions survive every rebuild unchanged.
8. The runtime snapshot matches the graph fingerprint and contains no fallback-to-first-parallel-edge binding.
9. Each returned route reports terrain and scenery data quality by routed length.
10. Formal flat/scenery readiness stays false until representative routes meet the documented thresholds.
11. Source licensing and redistribution fields are complete enough to separate open publication data from restricted evidence.
12. The complete test suite and data validator pass after all discovered defects are fixed.
