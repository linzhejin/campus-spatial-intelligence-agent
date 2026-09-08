"""
validate_osm_network.py — OSM 路网覆盖率校验脚本 (P0)
验证武汉大学校园 OSM footway/path 路网的完整性、连通性和覆盖率
"""

import json
import os
import sys
from datetime import datetime

# Windows PowerShell 默认 GBK 编码，强制 UTF-8 避免 emoji 输出崩溃
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import networkx as nx
import osmnx as ox

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import WHU_BBOX, OUTPUT_DIR, DATA_DIR


def load_pois():
    """从 data/pois.json 加载 POI 列表（T-004 已迁移，15 个 POI）"""
    pois_path = os.path.join(DATA_DIR, "pois.json")
    with open(pois_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    pois = {}
    for poi in data["pois"]:
        name = poi["name"]
        coords = poi["coordinates"]
        pois[name] = {"lat": coords["lat"], "lon": coords["lng"]}
    return pois


WHU_POIS = load_pois()


def download_road_network():
    print("[1/5] 下载武大校园步行道网络...")
    try:
        G = ox.graph_from_bbox(
            bbox=(WHU_BBOX["west"], WHU_BBOX["south"], WHU_BBOX["east"], WHU_BBOX["north"]),
            network_type="walk",
            simplify=True,
            retain_all=False,
            truncate_by_edge=True,
        )
        print(f"  ✓ 下载完成: {len(G.nodes)} 节点, {len(G.edges)} 条边")
        return G
    except Exception as e:
        print(f"  ✗ 下载失败: {e}")
        raise


def filter_pedestrian_edges(G):
    # DEC-012: 取消 footway/path 二次筛选，直接使用 network_type="walk" 全量路网
    # 原因: OSM 武大校园 footway/path 标注不全，筛选后路网碎片化（235 个连通分量）
    print("[2/5] 全量 walk 路网（含 residential/service/pedestrian/steps 等，跳过 footway/path 筛选）...")
    isolated = list(nx.isolates(G))
    if isolated:
        G = G.copy()
        G.remove_nodes_from(isolated)
    print(f"  ✓ 保留 {len(G.edges)} 条路段, 删除 {len(isolated)} 个孤立节点")
    return G


def compute_network_stats(G):
    print("[3/5] 计算路网统计...")
    edges = []
    for u, v, k, data in G.edges(data=True, keys=True):
        length = data.get("length", 0)
        edges.append({"u": u, "v": v, "k": k, "length": length})

    if not edges:
        return {"error": "没有找到任何 walk 类型路段"}

    lengths = [e["length"] for e in edges]
    total_length = sum(lengths)

    stats = {
        "total_segments": len(edges),
        "total_length_m": round(total_length, 1),
        "avg_length_m": round(total_length / len(edges), 1),
        "min_length_m": round(min(lengths), 1),
        "max_length_m": round(max(lengths), 1),
        "node_count": len(G.nodes),
    }

    print(f"  ✓ 路段数: {stats['total_segments']}")
    print(f"  ✓ 总长度: {stats['total_length_m']} 米")
    print(f"  ✓ 平均: {stats['avg_length_m']} 米, 最短: {stats['min_length_m']} 米, 最长: {stats['max_length_m']} 米")
    return stats


def _find_nearest_nodes(G, poi_names):
    poi_nodes = {}
    for name in poi_names:
        if name not in WHU_POIS:
            continue
        poi_info = WHU_POIS[name]
        lat, lon = poi_info["lat"], poi_info["lon"]
        try:
            nearest_node = ox.nearest_nodes(G, X=lon, Y=lat)
            poi_nodes[name] = int(nearest_node)
        except Exception as e:
            print(f"  ⚠ {name} 定位失败: {e}")
    return poi_nodes


def check_connectivity(G):
    print("[4/5] 检查路网连通性...")
    # osmnx 下载的是 MultiDiGraph，连通性检测需转无向图
    G_undirected = nx.Graph(G) if G.is_directed() else G
    is_connected = nx.is_connected(G_undirected)
    components = list(nx.connected_components(G_undirected))

    connectivity = {
        "is_fully_connected": is_connected,
        "num_components": len(components),
        "largest_component_size": max(len(c) for c in components) if components else 0,
    }

    if not is_connected:
        print(f"  ⚠ 路网不连通: {len(components)} 个连通分量, 最大分量 {connectivity['largest_component_size']} 个节点")
    else:
        print(f"  ✓ 路网完全连通")

    poi_names = list(WHU_POIS.keys())
    poi_nodes = _find_nearest_nodes(G, poi_names)

    print(f"  已定位 {len(poi_nodes)}/{len(poi_names)} 个 POI 到最近路网节点")

    reachability = {}
    reachable_pairs = 0
    total_pairs = 0

    poi_items = list(poi_nodes.items())
    for i, (src_name, src_node) in enumerate(poi_items):
        for j, (dst_name, dst_node) in enumerate(poi_items):
            if i >= j:
                continue
            total_pairs += 1
            try:
                path_length = nx.shortest_path_length(G_undirected, src_node, dst_node, weight="length")
                reachability[f"{src_name}→{dst_name}"] = {
                    "reachable": True,
                    "distance_m": round(path_length, 1),
                }
                reachable_pairs += 1
            except nx.NetworkXNoPath:
                reachability[f"{src_name}→{dst_name}"] = {"reachable": False, "error": "无可达路径"}
            except Exception as e:
                reachability[f"{src_name}→{dst_name}"] = {"reachable": False, "error": str(e)}

    connectivity["poi_reachability"] = reachability
    connectivity["poi_reachable_pairs"] = reachable_pairs
    connectivity["poi_total_pairs"] = total_pairs
    connectivity["poi_reachability_rate"] = round(reachable_pairs / total_pairs * 100, 1) if total_pairs > 0 else 0

    print(f"  ✓ POI 可达率: {connectivity['poi_reachability_rate']}% ({reachable_pairs}/{total_pairs})")

    key_paths = [
        ("牌坊", "樱顶"),
        ("樱顶", "老图书馆"),
        ("教五", "图书馆"),
        ("梅园", "桂园"),
    ]

    key_path_results = {}
    for src, dst in key_paths:
        src_node = poi_nodes.get(src)
        dst_node = poi_nodes.get(dst)
        if src_node is None or dst_node is None:
            key_path_results[f"{src}→{dst}"] = {"error": "POI 未找到最近节点"}
            continue
        try:
            path_length = nx.shortest_path_length(G_undirected, src_node, dst_node, weight="length")
            key_path_results[f"{src}→{dst}"] = {
                "reachable": True,
                "distance_m": round(path_length, 1),
            }
            print(f"  ✓ {src}→{dst}: {round(path_length, 1)} 米")
        except nx.NetworkXNoPath:
            key_path_results[f"{src}→{dst}"] = {"reachable": False, "error": "无可达路径"}
            print(f"  ✗ {src}→{dst}: 无可达路径")

    connectivity["key_paths"] = key_path_results
    return connectivity


def estimate_coverage(G):
    print("[5/5] 估算覆盖率...")
    distances = []
    for name, poi in WHU_POIS.items():
        lat, lon = poi["lat"], poi["lon"]
        try:
            nearest_node = ox.nearest_nodes(G, lon, lat)
            node_data = G.nodes[nearest_node]
            node_lat = node_data.get("y", node_data.get("lat", 0))
            node_lon = node_data.get("x", node_data.get("lon", 0))

            lat_diff = abs(lat - node_lat) * 111000
            lon_diff = abs(lon - node_lon) * 111000 * 0.86
            dist = (lat_diff ** 2 + lon_diff ** 2) ** 0.5
            distances.append(dist)
        except Exception:
            pass

    if not distances:
        return {"coverage_estimate": "无法估算", "note": "POI 距离计算失败"}

    avg_distance = sum(distances) / len(distances)
    max_distance = max(distances)

    if avg_distance < 20:
        rate, level = 0.95, "优秀"
    elif avg_distance < 50:
        rate, level = 0.85, "良好"
    elif avg_distance < 100:
        rate, level = 0.70, "一般"
    else:
        rate, level = 0.50, "较差"

    coverage = {
        "avg_poi_to_node_distance_m": round(avg_distance, 1),
        "max_poi_to_node_distance_m": round(max_distance, 1),
        "coverage_rate": rate,
        "coverage_level": level,
        "note": f"基于 {len(distances)} 个 POI 到最近路网节点的直线距离估算",
    }

    print(f"  ✓ 平均 POI-节点距离: {avg_distance:.1f} 米")
    print(f"  ✓ 覆盖率: {rate:.0%} ({level})")
    return coverage


def generate_report(stats, connectivity, coverage):
    coverage_rate = coverage.get("coverage_rate", 0)
    if coverage_rate >= 0.80:
        verdict = "PASS"
        next_step = "覆盖率达标，可进入后续阶段"
    else:
        verdict = "FAIL"
        next_step = "覆盖率不足 80%，触发降级方案：手动补段或降级为高德路径 API"

    report = {
        "script": "validate_osm_network.py",
        "timestamp": datetime.now().isoformat(),
        "project": "漫步珞珈 WHU-Walker",
        "bbox": WHU_BBOX,
        "stats": stats,
        "connectivity": connectivity,
        "coverage": coverage,
        "verdict": verdict,
        "next_step": next_step,
    }

    print("\n" + "=" * 60)
    print("  OSM 路网验证报告 — 武汉大学步行道网络")
    print("=" * 60)

    print(f"\n📊 统计信息:")
    print(f"  路段总数: {stats.get('total_segments', 'N/A')}")
    print(f"  总长度: {stats.get('total_length_m', 'N/A')} 米")
    print(f"  平均路段: {stats.get('avg_length_m', 'N/A')} 米")
    print(f"  最短路段: {stats.get('min_length_m', 'N/A')} 米")
    print(f"  最长路段: {stats.get('max_length_m', 'N/A')} 米")

    print(f"\n🔗 连通性:")
    print(f"  路网完全连通: {'是' if connectivity.get('is_fully_connected') else '否'}")
    print(f"  连通分量数: {connectivity.get('num_components', 'N/A')}")
    print(f"  POI 可达率: {connectivity.get('poi_reachability_rate', 'N/A')}%")

    print(f"\n🚶 关键路径:")
    for path_name, path_info in connectivity.get("key_paths", {}).items():
        if path_info.get("reachable"):
            print(f"  ✓ {path_name}: {path_info['distance_m']} 米")
        else:
            print(f"  ✗ {path_name}: {path_info.get('error', '未知错误')}")

    print(f"\n📈 覆盖率:")
    print(f"  平均 POI 距离: {coverage.get('avg_poi_to_node_distance_m', 'N/A')} 米")
    print(f"  覆盖率: {coverage_rate:.0%}")
    print(f"  评价: {coverage.get('coverage_level', 'N/A')}")

    print(f"\n✅ 验收结论: {verdict}")
    print(f"  {next_step}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, "osm_validation_report.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n💾 报告已保存: {output_path}")
    print("=" * 60)
    return report


def main():
    print("=" * 60)
    print("  武汉大学 OSM 步行道网络验证工具")
    print("=" * 60)
    print()

    try:
        G = download_road_network()
        G_ped = filter_pedestrian_edges(G)

        if len(G_ped.edges) == 0:
            print("\n❌ 错误: 没有找到任何 footway/path 路段")
            print("请检查 BBOX 范围或 OSM 数据覆盖情况")
            return 1

        stats = compute_network_stats(G_ped)
        connectivity = check_connectivity(G_ped)
        coverage = estimate_coverage(G_ped)
        generate_report(stats, connectivity, coverage)

        print("\n✅ 验证完成!")
        return 0

    except Exception as e:
        print(f"\n❌ 验证失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())