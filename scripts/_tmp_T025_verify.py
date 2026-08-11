"""验证 T-025 road_annotations.json 合法性和验收标准"""
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JSON_PATH = os.path.join(PROJECT_ROOT, "data", "road_annotations.json")

print("=" * 60)
print("  T-025 验收验证")
print("=" * 60)

try:
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    print("  ✓ JSON 格式合法")
except Exception as e:
    print(f"  ✗ JSON 格式错误: {e}")
    sys.exit(1)

edges = data.get("edges", [])
n = len(edges)
coverage_rate = data.get("coverage_rate", 0.0)

print(f"\n  元数据字段检查:")
for k in ["version", "annotator", "annotated_at", "coverage", "coverage_rate", "edges"]:
    ok = k in data
    print(f"    {k}: {'✓' if ok else '✗'}")

print(f"\n  数值验收:")
print(f"    edges 总数 N = {n}")
print(f"      N >= 80 ?  {'✓ YES' if n >= 80 else '✗ NO'}")
print(f"    coverage_rate C = {coverage_rate}")
print(f"      C >= 0.8 ?  {'✓ YES' if coverage_rate >= 0.8 else '✗ NO'}")

actual_annotated = 0
valid_levels = True
unique_edge_ids = set()
for i, e in enumerate(edges):
    eid = tuple(e.get("edge_id", []))
    if eid in unique_edge_ids:
        print(f"    ✗ 重复 edge_id 在第 {i} 条: {eid}")
    unique_edge_ids.add(eid)

    s = e.get("slope_level")
    c = e.get("scenery_level")
    if s is not None and c is not None:
        actual_annotated += 1
    if s is not None and not (1 <= s <= 5):
        print(f"    ✗ 第 {i} 条 slope_level 越界: {s}")
        valid_levels = False
    if c is not None and not (1 <= c <= 5):
        print(f"    ✗ 第 {i} 条 scenery_level 越界: {c}")
        valid_levels = False
    for req in ["edge_id", "u", "v", "length_m"]:
        if req not in e:
            print(f"    ✗ 第 {i} 条缺字段 {req}")
            valid_levels = False

actual_coverage = round(actual_annotated / n, 4) if n > 0 else 0
print(f"\n  实际校验:")
print(f"    双字段(slope+scenery)标注非空: {actual_annotated}/{n}")
print(f"    实际计算覆盖率 = {actual_coverage} ({actual_coverage*100:.2f}%)")
print(f"    coverage_rate 字段匹配实际? {'✓ YES' if abs(actual_coverage - coverage_rate) < 0.01 else '✗ NO (字段=' + str(coverage_rate) + ' 实际=' + str(actual_coverage) + ')'}")
print(f"    所有 edge_id 唯一?       {'✓ YES' if len(unique_edge_ids) == n else '✗ NO (' + str(n - len(unique_edge_ids)) + ' duplicates)'}")
print(f"    slope/scenery ∈ [1,5]?   {'✓ YES' if valid_levels else '✗ NO'}")

core_pois_covered = True
core_zones = ["樱顶", "老图", "珞珈山", "樱花大道", "行政楼", "凌波门", "牌坊"]
covered = {z: any(z in e.get("name", "") or z in e.get("note", "") for e in edges) for z in core_zones}
print(f"\n  核心 POI 周边覆盖:")
for z, ok in covered.items():
    print(f"    {z}: {'✓ YES' if ok else '✗ NO'}")
    if not ok:
        core_pois_covered = False

all_pass = (
    n >= 80
    and coverage_rate >= 0.8
    and actual_annotated == n
    and len(unique_edge_ids) == n
    and valid_levels
    and core_pois_covered
)
print(f"\n" + "=" * 60)
print(f"  总体结论: {'✓ 全部验收通过' if all_pass else '✗ 存在未通过项'}")
print("=" * 60)

sys.exit(0 if all_pass else 1)
