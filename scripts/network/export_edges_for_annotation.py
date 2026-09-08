"""
export_edges_for_annotation.py — 导出路网边用于手动标注 (TDD §11)

步骤：
  1. 加载武大校园步行道网络
  2. 筛选 footway/path 路段
  3. 导出 JSON + CSV 标注清单
  4. 生成 folium HTML 地图可视化（含坡度/景观等级配色方案）
  5. 打印统计摘要
"""

import csv
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import networkx as nx

from spatial.network import load_or_download_network, get_node_coords


OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data")


def filter_pedestrian_edges(G):
    edges_to_remove = []
    for u, v, k, data in G.edges(data=True, keys=True):
        highway = data.get("highway", "")
        if isinstance(highway, list):
            if not any(h in ("footway", "path") for h in highway):
                edges_to_remove.append((u, v, k))
        elif highway not in ("footway", "path"):
            edges_to_remove.append((u, v, k))

    G_filtered = G.copy()
    G_filtered.remove_edges_from(edges_to_remove)
    isolated = list(nx.isolates(G_filtered))
    G_filtered.remove_nodes_from(isolated)

    return G_filtered, len(edges_to_remove), len(isolated)


def extract_edge_data(G):
    edges = []
    for u, v, k, data in G.edges(data=True, keys=True):
        length = data.get("length", 0)
        if length == 0:
            n1 = G.nodes.get(u, {})
            n2 = G.nodes.get(v, {})
            x1, y1 = float(n1.get("x", 0)), float(n1.get("y", 0))
            x2, y2 = float(n2.get("x", 0)), float(n2.get("y", 0))
            length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 * 111000  # 近似米
            data["length"] = length

        edges.append({
            "edge_id": [int(u), int(v), int(k)],
            "u": int(u),
            "v": int(v),
            "name": data.get("name", "") if isinstance(data.get("name", ""), str) else "",
            "length_m": round(float(length), 1),
            "highway": data.get("highway", ""),
            "slope_level": data.get("slope_level"),
            "scenery_level": data.get("scenery_level"),
        })
    return edges


def export_json(edges, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"edges": edges}, f, ensure_ascii=False, indent=2)


def export_csv(edges, path):
    fieldnames = ["edge_id", "u", "v", "name", "length_m", "highway",
                  "slope_level", "scenery_level", "note"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for edge in edges:
            row = {k: edge.get(k, "") for k in fieldnames}
            row["edge_id"] = str(edge["edge_id"])
            row["slope_level"] = ""
            row["scenery_level"] = ""
            row["note"] = ""
            writer.writerow(row)


def _slope_color(level):
    colors = {
        1: "#2ecc71",
        2: "#82e0aa",
        3: "#f7dc6f",
        4: "#f39c12",
        5: "#e74c3c",
    }
    return colors.get(level, "#95a5a6")


def _scenery_color(level):
    colors = {
        1: "#95a5a6",
        2: "#85c1e9",
        3: "#5dade2",
        4: "#5499c7",
        5: "#2874a6",
    }
    return colors.get(level, "#bdc3c7")


def generate_folium_map(G, edges, html_path):
    try:
        import folium
        from folium import Element
    except ImportError:
        print("  ⚠ folium 未安装，跳过 HTML 地图生成")
        print("     安装命令: pip install folium")
        return False

    try:
        from config import MAP_CENTER
        center_lat = MAP_CENTER["lat"]
        center_lng = MAP_CENTER["lng"]
    except ImportError:
        center_lat = 30.5365
        center_lng = 114.3630

    m = folium.Map(location=[center_lat, center_lng], zoom_start=16,
                   tiles="https://webrd0{1-4}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}",
                   attr="© 高德地图")

    for idx, edge in enumerate(edges):
        u, v = edge["u"], edge["v"]
        try:
            lng1, lat1 = get_node_coords(G, u)
            lng2, lat2 = get_node_coords(G, v)
        except Exception:
            continue

        slope = edge.get("slope_level") or 3
        color = _slope_color(slope)

        polyline = folium.PolyLine(
            locations=[[lat1, lng1], [lat2, lng2]],
            color=color,
            weight=4,
            opacity=0.8,
            tooltip=f"#{idx} | 长度: {edge['length_m']}m | 坡度: {slope} | 景观: {edge.get('scenery_level', '未标注')}",
        )
        polyline.add_to(m)

        mid_lat = (lat1 + lat2) / 2
        mid_lng = (lng1 + lng2) / 2
        folium.Marker(
            location=[mid_lat, mid_lng],
            icon=folium.DivIcon(
                html=f'<div style="font-size:10px;color:#333;background:rgba(255,255,255,0.8);'
                     f'padding:1px 3px;border-radius:3px;">{idx}</div>',
            ),
        ).add_to(m)

    legend_html = """
    <div style="position: fixed; bottom: 50px; right: 10px; z-index: 9999;
                background: rgba(255,255,255,0.95); padding: 12px 15px;
                border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.15);
                font-size: 12px; line-height: 1.6;">
      <div style="font-weight: bold; margin-bottom: 6px;">坡度等级</div>
      <div><span style="color:#2ecc71;">■</span> 1级 (平坦)</div>
      <div><span style="color:#82e0aa;">■</span> 2级 (微坡)</div>
      <div><span style="color:#f7dc6f;">■</span> 3级 (中等)</div>
      <div><span style="color:#f39c12;">■</span> 4级 (较陡)</div>
      <div><span style="color:#e74c3c;">■</span> 5级 (陡坡)</div>
      <hr style="margin: 6px 0;">
      <div style="font-weight: bold; margin-bottom: 6px;">景观等级</div>
      <div><span style="color:#bdc3c7;">■</span> 1级 (普通)</div>
      <div><span style="color:#85c1e9;">■</span> 2级 (一般)</div>
      <div><span style="color:#5dade2;">■</span> 3级 (中等)</div>
      <div><span style="color:#5499c7;">■</span> 4级 (优美)</div>
      <div><span style="color:#2874a6;">■</span> 5级 (极佳)</div>
    </div>
    """
    m.get_root().html.add_child(Element(legend_html))

    m.save(html_path)
    return True


def print_summary(edges, G_filtered):
    total_edges = len(edges)
    total_length = sum(e["length_m"] for e in edges)

    print("\n" + "=" * 55)
    print("  路段标注清单导出 — 统计摘要")
    print("=" * 55)
    print(f"  总路段数:      {total_edges}")
    print(f"  总长度:        {total_length:.1f} 米 ({total_length / 1000:.2f} 公里)")
    if total_edges > 0:
        avg_len = total_length / total_edges
        print(f"  平均路段长度:  {avg_len:.1f} 米")
        min_len = min(e["length_m"] for e in edges)
        max_len = max(e["length_m"] for e in edges)
        print(f"  最短路段:      {min_len} 米")
        print(f"  最长路段:      {max_len} 米")

    has_annotation = sum(1 for e in edges
                        if e.get("slope_level") is not None
                        and e.get("scenery_level") is not None)
    coverage_rate = has_annotation / total_edges if total_edges > 0 else 0
    print(f"  已标注路段:    {has_annotation}/{total_edges} ({coverage_rate:.1%})")

    pois_count = 0
    try:
        from spatial.poi import load_pois
        pois_count = len(load_pois())
    except Exception:
        pass
    print(f"  POI 数量:      {pois_count}")

    print("\n  输出文件:")
    print(f"    JSON: {os.path.join(OUTPUT_DIR, 'edges_to_annotate.json')}")
    print(f"    CSV:  {os.path.join(OUTPUT_DIR, 'edges_to_annotate.csv')}")
    html_path = os.path.join(OUTPUT_DIR, "edges_map.html")
    if os.path.exists(html_path):
        print(f"    HTML: {html_path}")
    print("=" * 55)


def main():
    print("=" * 55)
    print("  武大校园步行道路网 — 标注清单导出工具")
    print("=" * 55)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("\n[1/4] 加载路网...")
    try:
        G = load_or_download_network()
    except RuntimeError as e:
        print(f"  ✗ 路网加载失败: {e}")
        return 1
    print(f"  ✓ 路网已加载: {G.number_of_nodes()} 节点, {G.number_of_edges()} 条边")

    print("\n[2/4] 筛选 footway/path 路段...")
    G_filtered, removed, isolated = filter_pedestrian_edges(G)
    print(f"  ✓ 保留 {G_filtered.number_of_edges()} 条路段, 删除 {removed} 条非步行路段, {isolated} 个孤立节点")

    if G_filtered.number_of_edges() == 0:
        print("\n❌ 错误: 没有找到任何 footway/path 路段")
        return 1

    print("\n[3/4] 导出标注清单 (JSON + CSV)...")
    edges = extract_edge_data(G_filtered)

    json_path = os.path.join(OUTPUT_DIR, "edges_to_annotate.json")
    csv_path = os.path.join(OUTPUT_DIR, "edges_to_annotate.csv")

    export_json(edges, json_path)
    print(f"  ✓ JSON 已保存: {json_path}")

    export_csv(edges, csv_path)
    print(f"  ✓ CSV 已保存: {csv_path}")

    print("\n[4/4] 生成 HTML 地图可视化...")
    html_path = os.path.join(OUTPUT_DIR, "edges_map.html")
    ok = generate_folium_map(G_filtered, edges, html_path)
    if ok:
        print(f"  ✓ HTML 地图已生成: {html_path}")

    print_summary(edges, G_filtered)
    return 0


if __name__ == "__main__":
    sys.exit(main())