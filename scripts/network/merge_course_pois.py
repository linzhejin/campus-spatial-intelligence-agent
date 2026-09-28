#!/usr/bin/env python3
"""Merge the reviewed WHU coursework spot crosswalk into a POI JSON file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spatial.course_poi_fusion import fuse_course_pois, write_json_atomic


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--course",
        type=Path,
        default=ROOT / "output/course_fusion/source/course_pois_normalized.geojson",
        help="normalized WGS-84 course spot GeoJSON",
    )
    parser.add_argument(
        "--crosswalk",
        type=Path,
        default=ROOT / "data/course_poi_crosswalk.json",
        help="reviewed source-row to formal-POI correspondence table",
    )
    parser.add_argument(
        "--pois",
        type=Path,
        default=ROOT / "data/pois.json",
        help="input POI library",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "output/course_fusion/pois/pois.merged.json",
        help="destination; defaults to a review artifact, not the live POI file",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "output/course_fusion/pois/course_poi_migration.json",
        help="migration audit report destination",
    )
    args = parser.parse_args(argv)

    course = json.loads(args.course.read_text(encoding="utf-8"))
    crosswalk = json.loads(args.crosswalk.read_text(encoding="utf-8"))
    pois = json.loads(args.pois.read_text(encoding="utf-8"))
    merged, report = fuse_course_pois(pois, course.get("features", []), crosswalk)

    write_json_atomic(args.output, merged)
    write_json_atomic(args.report, report)
    print(json.dumps({
        "course_points": report["input_count"],
        "counts": report["counts"],
        "poi_count": merged["count"],
        "output": str(args.output.resolve()),
        "report": str(args.report.resolve()),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
