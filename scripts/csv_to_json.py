"""
csv_to_json.py — 将标注好的 CSV 转换为 JSON 格式 (TDD §11 Step 4)

用法:
  python scripts/csv_to_json.py data/edges_to_annotate.csv
  python scripts/csv_to_json.py data/edges_to_annotate.csv -o data/road_annotations.json

输入 CSV 列: edge_id, u, v, name, length_m, slope_level, scenery_level, note
输出 JSON 格式匹配 TDD §4.2 路网标注数据结构
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def parse_edge_id(raw):
    if isinstance(raw, list):
        return [int(x) for x in raw]
    if isinstance(raw, str):
        s = raw.strip()
        s = s.strip("[]")
        parts = [p.strip() for p in s.split(",") if p.strip()]
        return [int(p) for p in parts]
    return []


def validate_level(value, field_name, row_idx):
    if value is None or value == "":
        return None
    try:
        v = int(value)
    except (ValueError, TypeError):
        return None, f"第 {row_idx} 行: {field_name} 值 '{value}' 不是整数"
    if v < 1 or v > 5:
        return None, f"第 {row_idx} 行: {field_name} 值 {v} 超出范围 [1, 5]"
    return v, None


def read_csv(filepath):
    if not os.path.exists(filepath):
        print(f"❌ 文件不存在: {filepath}")
        sys.exit(1)

    delimiter = ","
    sample = None
    with open(filepath, "r", encoding="utf-8-sig") as f:
        sample = f.read(2000)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample)
            delimiter = dialect.delimiter
        except csv.Error:
            pass

    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        rows = list(reader)

    if not rows:
        print("❌ CSV 文件为空")
        sys.exit(1)

    print(f"  读取 {len(rows)} 行数据")
    return rows


def convert(rows):
    edges = []
    errors = []
    skipped = 0
    slope_empty = 0
    scenery_empty = 0

    for idx, row in enumerate(rows, start=1):
        try:
            edge_id_raw = row.get("edge_id", "").strip()
            if not edge_id_raw:
                errors.append(f"第 {idx} 行: edge_id 为空")
                continue

            edge_id = parse_edge_id(edge_id_raw)
            if len(edge_id) < 2:
                errors.append(f"第 {idx} 行: edge_id 解析失败: {edge_id_raw}")
                continue

            u = int(row.get("u", edge_id[0]))
            v = int(row.get("v", edge_id[1]))
            name = row.get("name", "").strip()
            length_str = row.get("length_m", "0").strip()
            try:
                length_m = float(length_str) if length_str else 0.0
            except ValueError:
                length_m = 0.0

            slope_raw = row.get("slope_level", "").strip()
            scenery_raw = row.get("scenery_level", "").strip()
            note = row.get("note", "").strip()

            slope_level, err = validate_level(slope_raw, "slope_level", idx)
            if err:
                errors.append(err)
                skipped += 1
                continue

            scenery_level, err = validate_level(scenery_raw, "scenery_level", idx)
            if err:
                errors.append(err)
                skipped += 1
                continue

            if slope_level is None and scenery_level is None:
                slope_empty += 1
                scenery_empty += 1
            elif slope_level is None:
                slope_empty += 1
            elif scenery_level is None:
                scenery_empty += 1

            edges.append({
                "edge_id": edge_id,
                "u": u,
                "v": v,
                "name": name,
                "length_m": round(length_m, 1),
                "slope_level": slope_level,
                "scenery_level": scenery_level,
                "note": note,
            })

        except Exception as e:
            errors.append(f"第 {idx} 行: 解析异常: {e}")
            skipped += 1

    return edges, errors, skipped, slope_empty, scenery_empty


def compute_coverage(edges, reference_edges_path=None):
    annotated = sum(1 for e in edges
                    if e.get("slope_level") is not None
                    and e.get("scenery_level") is not None)
    total = len(edges)

    rate = annotated / total if total > 0 else 0.0

    if reference_edges_path and os.path.exists(reference_edges_path):
        try:
            with open(reference_edges_path, "r", encoding="utf-8") as f:
                ref_data = json.load(f)
            ref_edges = ref_data.get("edges", [])
            ref_count = len(ref_edges)
            ref_annotated = 0
            if ref_edges:
                ref_edge_ids = set()
                for re in ref_edges:
                    eid = re.get("edge_id", [])
                    if eid:
                        ref_edge_ids.add(tuple(eid))
                for e in edges:
                    if tuple(e.get("edge_id", [])) in ref_edge_ids:
                        if e.get("slope_level") is not None and e.get("scenery_level") is not None:
                            ref_annotated += 1
                if ref_count > 0:
                    rate = ref_annotated / ref_count
        except Exception:
            pass

    return rate, annotated, total


def build_output(edges, coverage_rate):
    return {
        "version": "1.0",
        "annotator": "武大学生开发者",
        "annotated_at": datetime.now().strftime("%Y-%m-%d"),
        "coverage": "武大核心区 OSM 路网 highway=footway/path",
        "coverage_rate": round(coverage_rate, 4),
        "edges": edges,
    }


def main():
    parser = argparse.ArgumentParser(
        description="将标注好的 CSV 文件转换为 WHU-Walker JSON 标注格式"
    )
    parser.add_argument("input", help="输入 CSV 文件路径")
    parser.add_argument("-o", "--output", help="输出 JSON 文件路径",
                        default=os.path.join(PROJECT_ROOT, "data", "road_annotations.json"))
    parser.add_argument("-r", "--reference", help="参考 edges JSON 文件（用于覆盖率计算）",
                        default=None)
    args = parser.parse_args()

    input_path = args.input
    output_path = args.output
    reference_path = args.reference

    print("=" * 50)
    print("  CSV → JSON 转换工具 — 路段标注数据")
    print("=" * 50)

    print(f"\n📖 读取 CSV: {input_path}")
    rows = read_csv(input_path)

    print("\n🔄 转换数据...")
    edges, errors, skipped, slope_empty, scenery_empty = convert(rows)

    coverage_rate, annotated, total = compute_coverage(edges, reference_path)

    print("\n📊 转换结果:")
    print(f"  总行数:        {len(rows)}")
    print(f"  成功转换:      {len(edges)}")
    print(f"  跳过/错误:     {skipped}")
    print(f"  坡度空值:      {slope_empty}")
    print(f"  景观空值:      {scenery_empty}")
    print(f"  覆盖率:        {coverage_rate:.1%} ({annotated}/{total})")

    if errors:
        print("\n⚠ 校验错误:")
        for err in errors[:20]:
            print(f"  - {err}")
        if len(errors) > 20:
            print(f"  ... 还有 {len(errors) - 20} 条错误")

    print(f"\n💾 写入 JSON: {output_path}")
    output_data = build_output(edges, coverage_rate)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"  ✓ 文件已保存")

    if coverage_rate < 0.8:
        print(f"\n⚠ 覆盖率 {coverage_rate:.1%} 低于 80% 阈值 (TDD §11.3)")
        print("  建议补充标注后再重新运行此脚本")

    print("\n✅ 转换完成!")
    return 0


if __name__ == "__main__":
    sys.exit(main())