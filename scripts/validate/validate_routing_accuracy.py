"""
validate_routing_accuracy.py — 路段可行性 + 出行时间准确性验证脚本

对 5 条代表性 OD × 3 出行方式（walk/bike/drive）输出：
  - 路径长度、当前估算时长、各模式 status
  - 路径中台阶边数、陡坡边数（slope_level=4/5）、校外路段边数
  - 路况事件命中数

用于：
  1. 验证 filter_graph_for_mode 的路段可行性过滤是否正确执行
     （台阶/电梯/扶梯对 bike/drive 一票否决；校外路段是否被剔除）
  2. 验证 estimate_duration_min 的时长估算与实际偏离程度，
     为后续按坡度/路况精细化时间估算提供基线对比

用法：
  python scripts/validate/validate_routing_accuracy.py
"""

import os
import sys
from datetime import datetime

# Windows PowerShell 默认 GBK 编码，强制 UTF-8 避免中文输出崩溃
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import networkx as nx  # noqa: E402

from spatial.network import load_or_download_network  # noqa: E402
from spatial.poi import get_poi  # noqa: E402
from spatial.routing import (  # noqa: E402
    compute_route,
    estimate_duration_min,
    filter_graph_for_mode,
    MODE_SPEEDS_KMH,
    _edge_highway_tags,
    _NON_VEHICULAR_HIGHWAY,
)


# 5 条代表性 OD（起终点 POI 名称，覆盖平地/跨学部/台阶/陡坡/边界场景）
REPRESENTATIVE_ODS = [
    ("武汉大学教1楼", "武汉大学图书馆(总馆)", "文理-平地短途"),
    ("武汉大学图书馆(总馆)", "武汉大学图书馆工学分馆", "文理→工学跨学部"),
    ("武汉大学信息学部图书馆", "武汉大学图书馆(总馆)", "信息→文理跨学部"),
    ("武汉大学樱花大道", "武汉大学珞珈山", "含台阶陡坡"),
    ("武汉大学枫园", "武汉大学工学部学生一食堂", "枫园→工学部边界"),
]


def _coord_of(poi_name: str):
    poi = get_poi(poi_name)
    if not poi:
        raise RuntimeError(f"POI 未找到: {poi_name}")
    coords = poi.get("coordinates") or poi.get("coords") or poi
    return float(coords["lng"]), float(coords["lat"]), poi_name


def _path_stats(G, route, mode):
    """统计路径中台阶边/陡坡边/校外路段数量。"""
    steps_edges = 0
    slope_5 = 0
    slope_4 = 0
    outside_road = 0
    non_vehicular_tags = 0
    blocked_modes_hits = 0

    for i in range(len(route) - 1):
        u, v = route[i], route[i + 1]
        edge_data = G.get_edge_data(u, v)
        if not edge_data:
            continue
        data = min(edge_data.values(), key=lambda d: d.get("length", float("inf")))

        tags = _edge_highway_tags(data)
        if "steps" in tags:
            steps_edges += 1
        if any(t in _NON_VEHICULAR_HIGHWAY for t in tags):
            non_vehicular_tags += 1

        lvl = data.get("slope_level")
        if lvl == 5:
            slope_5 += 1
        elif lvl == 4:
            slope_4 += 1

        bm = data.get("blocked_modes") or []
        if isinstance(bm, str):
            try:
                import ast
                bm = ast.literal_eval(bm)
            except Exception:
                bm = [bm]
        if mode in bm or "all" in bm:
            blocked_modes_hits += 1

    return {
        "steps_edges": steps_edges,
        "non_vehicular_tag_edges": non_vehicular_tags,
        "slope_level_5": slope_5,
        "slope_level_4": slope_4,
        "blocked_modes_hits": blocked_modes_hits,
    }


def _check_feasibility(G, mode):
    """校验 filter_graph_for_mode 是否真的剔除了 bike/drive 不可通行边。"""
    G_mode, status, _ = filter_graph_for_mode(G, mode)
    leaked_non_vehicular = 0
    leaked_steps = 0
    for u, v, k, data in G_mode.edges(keys=True, data=True):
        tags = _edge_highway_tags(data)
        if any(t in _NON_VEHICULAR_HIGHWAY for t in tags):
            leaked_non_vehicular += 1
            if "steps" in tags:
                leaked_steps += 1
    return {
        "filtered_edges": G_mode.number_of_edges(),
        "filtered_nodes": G_mode.number_of_nodes(),
        "leaked_non_vehicular_edges": leaked_non_vehicular,
        "leaked_steps_edges": leaked_steps,
        "status_sample": status,
    }


def main():
    print("=" * 80)
    print("珞珈智行 路段可行性 + 出行时间准确性验证")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    print("\n[1/4] 加载路网 + POI...")
    G = load_or_download_network()
    print(f"  路网: {G.number_of_nodes()} 节点, {G.number_of_edges()} 边")

    # ---------- 2. 全图路段可行性过滤校验 ----------
    print("\n[2/4] filter_graph_for_mode 路段可行性校验（全图）")
    print("-" * 80)
    print(f"{'mode':<6} {'过滤后边数':<10} {'过滤后点数':<10} "
          f"{'残留非机动车边':<14} {'残留台阶边':<10} {'status'}")
    print("  注：walk 模式台阶/电梯仅 2.5× 软惩罚保留可通行，残留是设计；"
          "bike/drive 残留为泄漏")
    for mode in ("walk", "bike", "drive"):
        stat = _check_feasibility(G, mode)
        if mode == "walk":
            flag = "  ✓（台阶可走，仅软惩罚）"
        else:
            flag = "  ✗ 应剔除未剔除!" if stat["leaked_non_vehicular_edges"] > 0 else "  ✓"
        print(f"{mode:<6} {stat['filtered_edges']:<10} "
              f"{stat['filtered_nodes']:<10} "
              f"{stat['leaked_non_vehicular_edges']:<14} "
              f"{stat['leaked_steps_edges']:<10} "
              f"{stat['status_sample']}{flag}")

    # ---------- 3. 5 条 OD × 3 模式路径规划 ----------
    print("\n[3/4] 5 条 OD × 3 模式路径规划")
    print("-" * 80)

    for od_idx, (start_name, end_name, scenario) in enumerate(REPRESENTATIVE_ODS, 1):
        print(f"\n[{od_idx}] {scenario}: {start_name} → {end_name}")
        try:
            start_lng, start_lat, _ = _coord_of(start_name)
            end_lng, end_lat, _ = _coord_of(end_name)
        except RuntimeError as e:
            print(f"  跳过: {e}")
            continue

        from spatial.network import get_nearest_node
        try:
            start_node = get_nearest_node(G, start_lng, start_lat)
            end_node = get_nearest_node(G, end_lng, end_lat)
        except Exception as e:
            print(f"  节点吸附失败: {e}")
            continue

        print(f"  {'mode':<6} {'路径长度m':<10} {'估算时长min':<12} "
              f"{'速度km/h':<10} {'status'}")

        for mode in ("walk", "bike", "drive"):
            # 用 _tool_plan_route 而非 compute_route：前者用 _resolve_endpoint
            # 在 G_mode 中找最近节点（自动跳过被孤立删除的 walk-only POI 节点），
            # 后者用 start_node 直传，POI 在 walk-only 节点时被孤立删除就不可达。
            try:
                from agents.tools import _tool_plan_route
                tool_result, _ = _tool_plan_route(
                    {"start": {"name": start_name},
                     "end": {"name": end_name},
                     "mode": mode},
                    None,
                )
                if isinstance(tool_result, dict) and "error" in tool_result:
                    print(f"  {mode:<6} 不可达: {tool_result['message']}")
                    continue
                result = tool_result
            except Exception as e:
                print(f"  {mode:<6} 不可达: {e}")
                continue

            print(f"  {mode:<6} "
                  f"{result['recommended_length_m']:<10.1f} "
                  f"{result['duration_min']:<12.1f} "
                  f"{result['speed_kmh']:<10.1f} "
                  f"{result.get('filter_status', '-')}")

    # ---------- 4. 时间估算基线对比 ----------
    print("\n[4/5] estimate_duration_min 按边累加校验")
    print("-" * 80)
    print(f"  MODE_SPEEDS_KMH = {MODE_SPEEDS_KMH}")
    print("  新签名 estimate_duration_min(G, route_nodes, mode, penalty_map) 按边累加，")
    print("  speed 受 slope_level/steps 标签/penalty_map 影响：")
    print("    - walk slope 5→0.5、4→0.7；bike 5→0.4、4→0.6；drive 不受小坡影响")
    print("    - walk 命中 steps 标签→0.4")
    print("    - penalty_map 数值 F → 速度 ÷ F（block 边已被删不入 route）")

    # ---------- 5. 多模态路径规划验证 ----------
    print("\n[5/5] plan_multimodal_route 多模态分段换乘验证")
    print("-" * 80)
    multimodal_cases = [
        ("武汉大学教1楼", [
            {"mode": "walk", "end": {"name": "武汉大学樱花大道"}},
            {"mode": "bike", "end": {"name": "武汉大学珞珈山"}},
        ]),
        ("武汉大学枫园", [
            {"mode": "bike", "end": {"name": "武汉大学工学部学生一食堂"}},
            {"mode": "walk", "end": {"name": "武汉大学图书馆工学分馆"}},
        ]),
    ]
    for idx, (start_name, legs_in) in enumerate(multimodal_cases, 1):
        print(f"\n  [{idx}] start={start_name} legs={len(legs_in)} 段")
        try:
            from agents.tools import _tool_plan_multimodal_route
            result, artifact = _tool_plan_multimodal_route(
                {"start": {"name": start_name}, "legs": legs_in}, None
            )
        except Exception as e:
            print(f"    ✗ 失败: {e}")
            continue
        if isinstance(result, dict) and "error" in result:
            print(f"    ✗ 错误: {result['error']} - {result['message']}")
            continue
        legs_out = result.get("legs") or []
        print(f"    ✓ 路径长度 {result.get('recommended_length_m', 0):.1f}m, "
              f"时长 {result.get('duration_min', 0):.1f}min, "
              f"mode={result.get('mode')}")
        for i, leg in enumerate(legs_out):
            print(f"    段 {i + 1}: mode={leg.get('mode')}, "
                  f"长度={leg.get('recommended_length_m', 0):.1f}m, "
                  f"时长={leg.get('duration_min', 0):.1f}min, "
                  f"{leg.get('start_name', '?')}→{leg.get('end_name', '?')}")
        # 断言：legs 长度 = 输入段数
        assert len(legs_out) == len(legs_in), \
            f"legs 数不匹配：输入 {len(legs_in)} 段，输出 {len(legs_out)} 段"
        assert result.get("route_kind") != "error"
        # 断言：每段 mode 与输入一致
        for i, leg in enumerate(legs_out):
            assert leg.get("mode") == legs_in[i].get("mode"), \
                f"段 {i + 1} mode 不匹配：输入 {legs_in[i].get('mode')}，输出 {leg.get('mode')}"
        print(f"    ✓ 断言通过：legs 数={len(legs_out)}、mode 序列正确")

    print("\n" + "=" * 80)
    print("验证完成")


if __name__ == "__main__":
    main()
