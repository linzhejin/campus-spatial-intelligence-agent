"""发布门槛只接受有来源和现场证据的数据。"""

from scripts.validate.check_deployment_readiness import assess
from scripts.validate.check_agent_research_readiness import assess_research_readiness


def _verified_inputs():
    validation = {
        "poi_count_matches_declared": True,
        "annotations": {"coverage_pct_of_edges": 100},
    }
    audit = {
        "poi": {"osm_candidate_source_available": True,
                "missing_open_candidates": [],
                "by_verification_status": {"field_verified": 1}},
        "road": {"placeholder_annotation_count": 0,
                 "review_queue": [], "graph": {"poi_snap_over_50m": []}},
    }
    samples = {"samples": [{"id": "od_01", "field_verification": {
        "status": "field_verified", "evidence": "现场记录-001"}}]}
    comparison = {"samples": [{"id": "od_01", "status": "compared"}]}
    return validation, audit, comparison, samples, {}


def test_runtime_flask_floor_supports_per_request_upload_limits():
    from pathlib import Path
    requirements = Path(__file__).resolve().parents[1] / "requirements.txt"
    flask_requirement = next(
        line.strip().lower() for line in requirements.read_text(encoding="utf-8").splitlines()
        if line.strip().lower().startswith("flask>=")
    )
    assert flask_requirement.startswith("flask>=3.1")


def test_agent_worker_has_a_managed_service_and_deploy_installs_it():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    worker_unit = root / "ops" / "systemd" / "whu-agent-worker.service"
    assert worker_unit.exists(), "Agent queue needs a systemd worker unit"
    unit_text = worker_unit.read_text(encoding="utf-8")
    assert "python -m jobs.worker" in unit_text
    assert "@APP_DIR@" in unit_text
    deploy_script = (root / "deploy.sh").read_text(encoding="utf-8")
    assert "whu-agent-worker.service" in deploy_script
    assert "systemctl enable --now" in deploy_script
    assert "DATABASE_URL" in deploy_script
    assert "SECRET_KEY" in deploy_script
    worker_source = (root / "jobs" / "worker.py").read_text(encoding="utf-8")
    assert "load_dotenv" in worker_source


def test_vision_readiness_and_worker_share_optional_runtime_dependencies():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    optional_requirements = (root / "requirements-vision.txt").read_text(encoding="utf-8")
    deployment_guide = (root / "docs" / "03_部署指南.md").read_text(encoding="utf-8")
    assert "same venv" in optional_requirements
    assert "Web 共用的虚拟环境" in deployment_guide
    assert "venv/bin/pip install -r requirements-vision.txt" in deployment_guide
    vision_unit = root / "ops" / "systemd" / "whu-vision-worker.service"
    assert vision_unit.exists()
    vision_unit_text = vision_unit.read_text(encoding="utf-8")
    assert "python -m vision.worker" in vision_unit_text
    assert "TimeoutStopSec=300" in vision_unit_text
    assert "manager_vision_worker" in (root / "storage" / "migrations" / "006_vision_worker_heartbeat.sql").read_text(encoding="utf-8")
    deploy_script = (root / "deploy.sh").read_text(encoding="utf-8")
    assert "VISION_WORKER_ENABLED" in deploy_script
    assert 'systemctl restart "${SERVICE_NAME}" "${AGENT_SERVICE_NAME}"' in deploy_script


def test_verified_campus_data_passes_readiness_gate():
    assert assess(*_verified_inputs())["ready"] is True


def test_complete_attribute_master_allows_explicit_unknowns_on_new_edges():
    validation, audit, comparison, samples, reviews = _verified_inputs()
    validation["annotations"]["coverage_pct_of_edges"] = 99.9
    validation["edge_attribute_master"] = {
        "current_graph_edges": 12568,
        "bound_current_edges": 12568,
        "coverage_pct": 100.0,
        "duplicate_bindings": 0,
        "stale_bindings": 0,
        "geometry_binding_mismatches": 0,
        "network_version_matches_current_graph": True,
    }

    result = assess(validation, audit, comparison, samples, reviews)

    assert "road_annotation_gap" not in {row["code"] for row in result["blockers"]}


def test_unreviewed_candidate_and_route_block_deployment():
    validation, audit, comparison, samples, reviews = _verified_inputs()
    audit["poi"]["missing_open_candidates"] = [{"osm_id": "way/123"}]
    samples["samples"][0]["field_verification"] = None
    result = assess(validation, audit, comparison, samples, reviews)
    assert result["ready"] is False
    assert {row["code"] for row in result["blockers"]} >= {
        "unreviewed_open_candidates", "unverified_reference_routes"}


def test_candidate_network_losing_existing_poi_coverage_blocks_deployment():
    validation, audit, comparison, samples, reviews = _verified_inputs()
    audit["road"]["graph"]["candidate_graph_regressions"] = [{
        "poi_id": "poi_001", "production_snap_m": 10.0,
        "candidate_snap_m": 120.0,
    }]
    result = assess(validation, audit, comparison, samples, reviews)
    assert result["ready"] is False
    assert "candidate_graph_coverage_regression" in {
        row["code"] for row in result["blockers"]}


def test_foreign_university_poi_blocks_deployment():
    validation, audit, comparison, samples, reviews = _verified_inputs()
    audit["poi"]["foreign_campus_conflicts"] = [{"poi_id": "poi_001"}]
    result = assess(validation, audit, comparison, samples, reviews)
    assert "foreign_campus_poi_conflicts" in {
        row["code"] for row in result["blockers"]}


def test_unreviewed_large_provider_route_disagreement_blocks_deployment():
    validation, audit, comparison, samples, reviews = _verified_inputs()
    samples["samples"][0]["field_verification"] = None
    comparison["samples"][0].update({
        "geometry_deviation": {"p95_m": 180.0},
        "distance_difference_m": 220.0,
    })
    result = assess(validation, audit, comparison, samples, reviews)
    assert "unresolved_route_disagreements" in {
        row["code"] for row in result["blockers"]}


def test_release_route_without_building_geometry_check_blocks_deployment():
    validation, audit, comparison, samples, reviews = _verified_inputs()
    comparison["own_network"] = {"course_release_fingerprint": "release-123"}
    comparison["samples"][0]["hazard_assessment"] = {
        "status": "building_layer_missing",
    }

    result = assess(validation, audit, comparison, samples, reviews)

    assert "unassessed_route_geometry_hazards" in {
        row["code"] for row in result["blockers"]}


def test_unreviewed_building_crossing_edge_blocks_release_route():
    validation, audit, comparison, samples, reviews = _verified_inputs()
    comparison["own_network"] = {"course_release_fingerprint": "release-123"}
    comparison["samples"][0]["hazard_assessment"] = {
        "status": "checked",
        "suspected_crossing_edge_ids": [{"edge_id": [1, 2, 0]}],
    }

    result = assess(validation, audit, comparison, samples, reviews)

    assert "unreviewed_route_building_crossings" in {
        row["code"] for row in result["blockers"]}


def test_evidence_backed_review_resolves_building_crossing_gate():
    validation, audit, comparison, samples, reviews = _verified_inputs()
    comparison["own_network"] = {"course_release_fingerprint": "release-123"}
    comparison["samples"][0]["hazard_assessment"] = {
        "status": "checked",
        "suspected_crossing_edge_ids": [{"edge_id": [1, 2, 0]}],
    }
    reviews["road_decisions"] = [{
        "edge_id": [1, 2, 0], "verification_status": "institution_verified",
        "evidence": "校方通行说明", "verified_at": "2026-09-28",
    }]

    result = assess(validation, audit, comparison, samples, reviews)

    assert "unreviewed_route_building_crossings" not in {
        row["code"] for row in result["blockers"]}


def test_candidate_graph_deviation_and_building_crossing_block_replacement():
    validation, audit, comparison, samples, reviews = _verified_inputs()
    candidate = {"samples": [{"id": "od_01", "status": "compared",
               "geometry_deviation": {"p95_m": 180.0},
               "distance_difference_m": 200.0,
               "hazard_assessment": {"suspected_crossing_edge_ids": [
                   {"edge_id": [1, 2, 3]}]}}]}
    samples["samples"][0]["field_verification"] = None

    result = assess(validation, audit, comparison, samples, reviews, candidate)

    assert {"candidate_route_disagreements", "candidate_building_crossing_suspicions"} <= {
        row["code"] for row in result["blockers"]}


def test_research_readiness_requires_code_checks_data_and_attributes():
    ready_checks = {
        "routing_policy": {"passed": True},
        "route_state": {"passed": True},
        "profile_telemetry": {"passed": True},
        "routing": {"passed": True},
        "frontend_state": {"passed": True},
    }
    result = assess_research_readiness(
        ready_checks,
        deployment={"ready": True, "blockers": []},
        attributes={"ready": False, "degraded": True, "blockers": ["placeholder data"]},
    )
    assert result["ready"] is False
    assert "attribute_master_data" in result["blockers"]


def test_research_readiness_passes_only_when_every_gate_passes():
    checks = {name: {"passed": True} for name in (
        "routing_policy", "route_state", "profile_telemetry", "routing", "frontend_state"
    )}
    result = assess_research_readiness(
        checks,
        deployment={"ready": True, "blockers": []},
        attributes={"ready": True, "degraded": False, "blockers": []},
    )
    assert result["ready"] is True
    assert result["blockers"] == {}
