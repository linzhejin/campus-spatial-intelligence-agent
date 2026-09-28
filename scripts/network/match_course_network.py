"""Compare every normalized coursework road with the current OSM graph."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

import networkx as nx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from spatial.course_matching import match_course_features  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def match_files(course_path: Path, graph_path: Path, output_dir: Path) -> dict:
    course_path = course_path.resolve()
    graph_path = graph_path.resolve()
    output_dir = output_dir.resolve()
    course = json.loads(course_path.read_text(encoding="utf-8"))
    graph = nx.read_graphml(graph_path, node_type=int)
    result = match_course_features(course.get("features", []), graph)
    counts = Counter(row["action"] for row in result["matches"])
    report = {
        "schema_version": result["schema_version"],
        "source_crs": result["source_crs"],
        "comparison_crs": result["comparison_crs"],
        "corridor_m": result["corridor_m"],
        "graph": {
            "path_name": graph_path.name,
            "sha256": _sha256(graph_path),
            "node_count": result["graph_node_count"],
            "edge_count": result["graph_edge_count"],
        },
        "course": {
            "path_name": course_path.name,
            "feature_count": result["course_feature_count"],
            "counts_by_action": dict(sorted(counts.items())),
        },
        "matches": result["matches"],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "course_osm_matches.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "match_review.geojson").write_text(
        json.dumps(result["review_geojson"], ensure_ascii=False, indent=2,
                   allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return {"course_features": result["course_feature_count"],
            "counts_by_action": dict(sorted(counts.items())),
            "output_dir": str(output_dir)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course", required=True, type=Path,
                        help="Normalized course_roads_normalized.geojson")
    parser.add_argument("--graph", required=True, type=Path,
                        help="Existing OSM GraphML road network")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(match_files(args.course, args.graph, args.output_dir),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
