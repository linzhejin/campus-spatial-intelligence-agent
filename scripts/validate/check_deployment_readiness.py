"""依据结构校验、来源对账与现场核实记录判断校园数据能否发布。

用法：先运行 validate_all_data.py、audit_campus_master.py、
compare_walk_reference.py，再运行本脚本。退出码 0 表示当前验收项有证据支持；
退出码 1 表示仍有待核查项。地图商差异只产生核查项，不把地图商当作真值。
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "scripts" / "audit_output"


def read_json(path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def assess(validation, audit, comparison, samples, reviews, candidate_comparison=None):
    blockers = []

    def add(code, count, detail):
        if count:
            blockers.append({"code": code, "count": count, "detail": detail})

    if validation is None:
        add("structural_validation_missing", 1, "尚未运行数据结构校验")
    else:
        fields = ("missing_fields", "bad_range", "bad_type", "bad_campus",
                  "campus_mismatch", "outside_all_polys", "duplicate_names",
                  "alias_conflicts", "alias_name_clash")
        n = sum(len(validation.get(field) or []) for field in fields)
        add("structural_errors", n, "POI 字段、位置或名称存在结构错误")
        add("count_mismatch", int(not validation.get("poi_count_matches_declared")),
            "正式库声明条数与实际不符")
        add("road_annotation_gap", int(validation.get("annotations", {}).get(
            "coverage_pct_of_edges", 0) < 100), "路网边缺少标注")

    if audit is None:
        add("master_audit_missing", 1, "尚未生成校园主数据审计")
    else:
        poi = audit.get("poi", {})
        road = audit.get("road", {})
        add("open_candidate_source_missing", int(not poi.get("osm_candidate_source_available")),
            "没有开放来源候选清单，无法完成来源到正式库的对账")
        add("foreign_campus_poi_conflicts", len(poi.get("foreign_campus_conflicts", [])),
            "正式地点位于其他学校或机构边界内，归属需复核")
        decisions = {(item.get("source"), item.get("source_id")): item
                     for item in reviews.get("poi_decisions", [])}
        unresolved = [item for item in poi.get("missing_open_candidates", [])
                      if decisions.get(("OpenStreetMap", item.get("osm_id")), {}).get("decision")
                      not in {"included", "excluded"}]
        add("unreviewed_open_candidates", len(unresolved),
            "来源有名称而正式库未匹配的候选缺少逐项纳入或排除结论")
        statuses = poi.get("by_verification_status", {})
        add("unverified_pois", sum(count for status, count in statuses.items()
                                   if status not in {"field_verified", "institution_verified"}),
            "正式地点仍缺少现场或校方核实记录")
        add("placeholder_road_annotations", road.get("placeholder_annotation_count", 0),
            "路段坡度或景观标注仍含占位说明")
        queue = road.get("review_queue", [])
        add("unreviewed_risk_edges", sum(item.get("verification_status") not in
                                         {"field_verified", "institution_verified"}
                                         for item in queue),
            "建筑相交或台阶附近路段尚无通行性裁决")
        add("poi_snap_over_50m", len(road.get("graph", {}).get("poi_snap_over_50m", [])),
            "目的地点到路网节点超过 50 米，导航落点待核实")
        add("candidate_graph_coverage_regression", len(road.get("graph", {}).get(
            "candidate_graph_regressions", [])),
            "候选路网使原先可贴近道路的目的地超过 50 米，替换前须补齐范围或纠正地点")

    sample_rows = samples.get("samples", []) if samples else []
    add("reference_samples_missing", int(not sample_rows), "没有代表性起终点样本")
    add("unverified_reference_routes", sum(
        not isinstance(row.get("field_verification"), dict)
        or row["field_verification"].get("status") not in
        {"field_verified", "institution_verified"}
        or not row["field_verification"].get("evidence")
        for row in sample_rows), "代表性路线缺少现场或校方可通行性证据")
    if comparison is None:
        add("commercial_reference_missing", 1, "尚未完成独立步行路线对照")
    else:
        compared = {row.get("id"): row for row in comparison.get("samples", [])}
        add("reference_comparison_missing", sum(
            compared.get(row.get("id"), {}).get("status") != "compared"
            for row in sample_rows), "代表性路线未完成地图商几何对照")
        unresolved_differences = 0
        for sample in sample_rows:
            metrics = compared.get(sample.get("id"), {})
            if metrics.get("status") != "compared":
                continue
            p95 = metrics.get("geometry_deviation", {}).get("p95_m") or 0
            length_difference = metrics.get("distance_difference_m") or 0
            verified = sample.get("field_verification") or {}
            field_resolved = (verified.get("status") in
                              {"field_verified", "institution_verified"}
                              and bool(verified.get("evidence")))
            if (p95 > 100 or abs(length_difference) > 150) and not field_resolved:
                unresolved_differences += 1
        add("unresolved_route_disagreements", unresolved_differences,
            "自有路线与地图商路线差异明显，需逐段核实，不直接判定任一方错误")

    if candidate_comparison is not None:
        compared = {row.get("id"): row for row in candidate_comparison.get("samples", [])}
        add("candidate_reference_comparison_missing", sum(
            compared.get(row.get("id"), {}).get("status") != "compared"
            for row in sample_rows), "候选路网缺少代表性路线的独立几何对照")
        disagreements = 0
        crossings = 0
        for sample in sample_rows:
            row = compared.get(sample.get("id"), {})
            if row.get("status") != "compared":
                continue
            field = sample.get("field_verification") or {}
            verified = (field.get("status") in {"field_verified", "institution_verified"}
                        and bool(field.get("evidence")))
            p95 = row.get("geometry_deviation", {}).get("p95_m") or 0
            distance_delta = row.get("distance_difference_m") or 0
            if (p95 > 100 or abs(distance_delta) > 150) and not verified:
                disagreements += 1
            suspected = row.get("hazard_assessment", {}).get(
                "suspected_crossing_edge_ids", [])
            reviewed = {tuple(item.get("edge_id", [])) for item in
                        row.get("hazard_assessment", {}).get("crossing_reviews", [])
                        if item.get("status") in {"field_verified", "institution_verified"}
                        and item.get("evidence")}
            crossings += sum(tuple(item.get("edge_id", item)) not in reviewed
                             for item in suspected)
        add("candidate_route_disagreements", disagreements,
            "候选路网与地图商路线差异明显，替换前须逐段核实")
        add("candidate_building_crossing_suspicions", crossings,
            "候选路线与建筑轮廓相交，缺少针对具体边的现场或校方证据")

    return {"ready": not blockers, "blockers": blockers,
            "interpretation": "自动检查不能替代现场核实；地图商路线仅作独立参照。"}


def main():
    result = assess(
        read_json(ROOT / "data_validation_report.json"),
        read_json(AUDIT / "campus_spatial_quality.json"),
        read_json(AUDIT / "amap_comparison_metrics.json"),
        read_json(ROOT / "data" / "route_reference_samples.json"),
        read_json(ROOT / "data" / "campus_review_decisions.json") or {},
        read_json(AUDIT / "amap_comparison_candidate.json"),
    )
    output = AUDIT / "deployment_readiness.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"发布数据验收: {'通过' if result['ready'] else '未通过'}；报告: {output}")
    for row in result["blockers"]:
        print(f"- {row['code']}: {row['count']}（{row['detail']}）")
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
