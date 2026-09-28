"""Normalize the user-supplied WHU coursework layers into an audit bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from spatial.course_data import import_course_data  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path,
                        help="Directory containing whu_road.shp and whu_spot.shp")
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="Destination for normalized GeoJSON and the source manifest")
    args = parser.parse_args(argv)
    result = import_course_data(args.source_dir, args.output_dir)
    print(json.dumps({key: value for key, value in result.items() if key != "manifest"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
