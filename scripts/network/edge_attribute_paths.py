"""Canonical paths for the edge-attribute data pipeline."""

from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


ROOT = project_root()
DATA_DIR = ROOT / "data"
GRAPH_PATH = DATA_DIR / "whu_road_network.graphml"
LEGACY_ANNOTATIONS_PATH = DATA_DIR / "road_annotations.json"
SOURCES_PATH = DATA_DIR / "edge_attribute_sources.json"
MASTER_PATH = DATA_DIR / "edge_attribute_master.json"
REVIEWS_PATH = DATA_DIR / "edge_attribute_review_decisions.json"
QUALITY_REPORT_PATH = DATA_DIR / "edge_attribute_quality_report.json"
