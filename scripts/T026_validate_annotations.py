"""
T-026 验收自检脚本
3 条验收标准：
  1) 覆盖率 ≥ 80% → routing 完整模式生效；
  2) 覆盖率 < 80% → 降级为仅距离成本；
  3) 降级时 filter_status 附加 "degraded_annotations" 或 "no_annotations" 标记
"""

import json
import os
import sys
import tempfile
import shutil
from typing import Optional

import networkx as nx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def build_test_graph(n_edges: int = 10) -> nx.MultiDiGraph:
    G = nx.MultiDiGraph()
    for i in range(n_edges + 1):
        G.add_node(str(i), x=114.35 + i * 0.001, y=30.53 + i * 0.001)
    for i in range(n_edges):
        u, v = str(i), str(i + 1)
        G.add_edge(u, v, length=50.0 + i * 10, highway="footway")
    return G


def create_temp_annotations(G: nx.MultiDiGraph, coverage_ratio: float) -> str:
    edges_list = list(G.edges(keys=True, data=True))
    n_annotated = int(len(edges_list) * coverage_ratio)
    edges_annotations = []
    for idx, (u, v, k, data) in enumerate(edges_list[:n_annotated]):
        edges_annotations.append({
            "edge_id": [u, v, k],
            "u": u,
            "v": v,
            "name": f"路段_{idx}",
            "length_m": data.get("length", 0),
            "slope_level": 1 + (idx % 5),
            "scenery_level": 5 - (idx % 5),
        })
    
    tmpdir = tempfile.mkdtemp()
    ann_path = os.path.join(tmpdir, "road_annotations.json")
    with open(ann_path, "w", encoding="utf-8") as f:
        json.dump({
            "version": "1.0",
            "annotator": "test",
            "annotated_at": "",
            "coverage": "test",
            "coverage_rate": coverage_ratio,
            "edges": edges_annotations,
        }, f, ensure_ascii=False, indent=2)
    
    return ann_path, tmpdir


def test_check_1_coverage_high_full_mode():
    """验收 1: 覆盖率 ≥ 80% → routing 完整模式生效"""
    print("\n[CHECK 1] 覆盖率 ≥ 80% → 完整模式")
    from spatial import network as net_mod

    G = build_test_graph(10)
    ann_path, tmpdir = create_temp_annotations(G, 0.9)

    try:
        rate = net_mod._merge_annotations(G, ann_path)
        print(f"  coverage_rate = {rate:.3f} (expected ≥ 0.8)")
        assert rate >= 0.8, f"覆盖率 {rate:.3f} < 0.8"

        annotated_count = 0
        total_count = 0
        for u, v, k, data in G.edges(keys=True, data=True):
            total_count += 1
            if "slope_level" in data and "scenery_level" in data:
                annotated_count += 1
        actual_coverage = annotated_count / total_count if total_count > 0 else 0
        print(f"  实际标注边数: {annotated_count}/{total_count} = {actual_coverage:.3f}")
        assert actual_coverage >= 0.8

        net_mod._annotation_coverage_rate = rate
        exposed_rate = net_mod.get_annotation_coverage_rate()
        print(f"  get_annotation_coverage_rate() = {exposed_rate:.3f}")
        assert abs(exposed_rate - rate) < 1e-9

        from spatial import routing as rt_mod
        degraded_tag = rt_mod._should_degrade_annotations()
        print(f"  should_degrade = {degraded_tag} (expected None/full mode)")
        assert degraded_tag is None, f"覆盖率 {rate:.3f} 不应降级，但得到 {degraded_tag}"

        print("[CHECK 1] PASS ✓")
        return True
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        net_mod._annotation_coverage_rate = None


def test_check_2_coverage_low_degraded():
    """验收 2: 覆盖率 < 80% → 降级为仅距离成本"""
    print("\n[CHECK 2] 覆盖率 < 80% → 降级模式")
    from spatial import network as net_mod
    from spatial import routing as rt_mod

    G = build_test_graph(10)
    ann_path, tmpdir = create_temp_annotations(G, 0.5)

    try:
        rate = net_mod._merge_annotations(G, ann_path)
        print(f"  coverage_rate = {rate:.3f} (expected < 0.8)")
        assert rate < 0.8, f"覆盖率 {rate:.3f} ≥ 0.8"

        net_mod._annotation_coverage_rate = rate
        degraded_tag = rt_mod._should_degrade_annotations()
        print(f"  should_degrade = {degraded_tag} (expected 'degraded_annotations')")
        assert degraded_tag == "degraded_annotations", f"期望 degraded_annotations，得到 {degraded_tag}"

        start_node = list(G.nodes())[0]
        end_node = list(G.nodes())[-1]
        result = rt_mod.compute_route(G, start_node, end_node)
        filter_status = result.get("filter_status", "")
        print(f"  filter_status = '{filter_status}' (expected contains 'degraded_annotations')")
        assert "degraded_annotations" in filter_status or result.get("_annotation_degraded") is True

        print("[CHECK 2] PASS ✓")
        return True
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        net_mod._annotation_coverage_rate = None


def test_check_3_no_annotations_degraded():
    """验收 3: 完全无标注 → no_annotations 标记"""
    print("\n[CHECK 3] 完全无标注 → no_annotations 标记")
    from spatial import network as net_mod
    from spatial import routing as rt_mod

    G = build_test_graph(10)
    ann_path, tmpdir = create_temp_annotations(G, 0.0)

    try:
        rate = net_mod._merge_annotations(G, ann_path)
        print(f"  coverage_rate = {rate:.3f} (expected = 0.0)")
        assert abs(rate - 0.0) < 1e-9

        net_mod._annotation_coverage_rate = rate
        degraded_tag = rt_mod._should_degrade_annotations()
        print(f"  should_degrade = {degraded_tag} (expected 'no_annotations')")
        assert degraded_tag == "no_annotations", f"期望 no_annotations，得到 {degraded_tag}"

        start_node = list(G.nodes())[0]
        end_node = list(G.nodes())[-1]
        result = rt_mod.compute_route(G, start_node, end_node)
        filter_status = result.get("filter_status", "")
        print(f"  filter_status = '{filter_status}'")
        assert "no_annotations" in filter_status or "degraded_annotations" in filter_status or result.get("_annotation_degraded") is True

        print("[CHECK 3] PASS ✓")
        return True
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        net_mod._annotation_coverage_rate = None


def main():
    print("=" * 60)
    print("T-026 标注数据加载与验证 自检脚本")
    print("=" * 60)

    results = []
    try:
        results.append(("CHECK 1 (覆盖率≥80%→完整模式)", test_check_1_coverage_high_full_mode()))
    except Exception as e:
        print(f"[CHECK 1] FAIL ✗: {e}")
        import traceback
        traceback.print_exc()
        results.append(("CHECK 1", False))

    try:
        results.append(("CHECK 2 (覆盖率<80%→降级)", test_check_2_coverage_low_degraded()))
    except Exception as e:
        print(f"[CHECK 2] FAIL ✗: {e}")
        import traceback
        traceback.print_exc()
        results.append(("CHECK 2", False))

    try:
        results.append(("CHECK 3 (无标注→no_annotations)", test_check_3_no_annotations_degraded()))
    except Exception as e:
        print(f"[CHECK 3] FAIL ✗: {e}")
        import traceback
        traceback.print_exc()
        results.append(("CHECK 3", False))

    print("\n" + "=" * 60)
    print("自检结果汇总")
    print("=" * 60)
    passed = 0
    for name, ok in results:
        status = "PASS ✓" if ok else "FAIL ✗"
        print(f"  {name}: {status}")
        if ok:
            passed += 1
    print(f"\n总计: {passed}/{len(results)} 通过")
    print(f"coverage_rate (实际空标注文件): 0.0 (后续修改后会更新)")
    return passed == len(results)


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
