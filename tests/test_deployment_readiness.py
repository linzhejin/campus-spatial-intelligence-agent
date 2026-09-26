"""发布门槛只接受有来源和现场证据的数据。"""

from scripts.validate.check_deployment_readiness import assess


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


def test_verified_campus_data_passes_readiness_gate():
    assert assess(*_verified_inputs())["ready"] is True


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
