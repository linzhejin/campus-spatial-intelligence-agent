#!/usr/bin/env python3
"""Evaluate reviewed accident-event candidates on disjoint held-out UAV videos.

The evaluator calls the same video-analysis pipeline as the visual worker.
Ground truth labels each incident interval and road ROI; split groups must be
independent flights/scenes/incidents rather than frames or adjacent clips.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from vision.event_evaluation import (  # noqa: E402
    extract_accident_event_candidates,
    summarize_event_detection,
    validate_event_manifest,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _dataset_path(root: Path, value: str) -> Path:
    normalized = str(value).replace("\\", "/")
    if (not normalized or PureWindowsPath(normalized).is_absolute()
            or Path(normalized).is_absolute()
            or any(part in {"", ".", ".."} for part in normalized.split("/"))):
        raise ValueError("video paths must be safe relative paths")
    base = root.resolve()
    path = (base / normalized).resolve()
    try:
        path.relative_to(base)
    except ValueError as error:
        raise ValueError("video path resolves outside dataset root") from error
    if not path.is_file():
        raise ValueError(f"video does not exist: {normalized}")
    return path


def run_event_evaluation(manifest: dict, dataset_root: Path, detector_path: Path,
                         accident_model_path: Path, *, threshold: float,
                         min_temporal_iou: float = 0.1,
                         bootstrap_repetitions: int = 1000,
                         seed: int = 20261010, analyzer=None,
                         detector_factory=None, classifier_factory=None) -> dict:
    """Run the production analyzer over test videos and summarize event matches."""
    manifest = validate_event_manifest(manifest)
    dataset_root = Path(dataset_root).resolve()
    detector_path = Path(detector_path).resolve()
    accident_model_path = Path(accident_model_path).resolve()
    if not dataset_root.is_dir() or not detector_path.is_file() or not accident_model_path.is_file():
        raise ValueError("dataset root, detector, and accident model must exist")
    if (isinstance(threshold, bool) or not isinstance(threshold, (int, float))
            or not math.isfinite(float(threshold)) or not 0 < threshold <= 1):
        raise ValueError("threshold must be greater than zero and at most one")
    if analyzer is None:
        from vision.engine import analyze_media
        analyzer = analyze_media
    if detector_factory is None:
        from vision.engine import OnnxDetector
        detector_factory = OnnxDetector
    if classifier_factory is None:
        from vision.specialized import OnnxAiderClassifier
        classifier_factory = OnnxAiderClassifier

    detector = detector_factory(str(detector_path))
    classifier = classifier_factory(str(accident_model_path), threshold=float(threshold))
    predictions = {}
    duration_by_sample = {}
    analysis_seconds = {}
    for sample in manifest["samples"]:
        video_path = _dataset_path(dataset_root, sample["video"])
        started = time.monotonic()
        analysis = analyzer(
            video_path, "video", None,
            camera_stabilized=sample["camera_stabilized"],
            detector=detector,
            accident_model=classifier,
            regions=sample["observation_regions"],
        )
        elapsed = time.monotonic() - started
        if not isinstance(analysis, dict):
            raise ValueError(f"video analyzer returned no result for {sample['sample_id']}")
        safety = analysis.get("safety")
        if not isinstance(safety, dict) or safety.get("accident_recognition_supported") is not True:
            raise RuntimeError(f"accident model was not reliable for video {sample['sample_id']}")
        coverage = analysis.get("video_coverage")
        if not isinstance(coverage, dict) or coverage.get("complete") is not True:
            raise RuntimeError(f"video analysis was incomplete for {sample['sample_id']}")
        duration = coverage.get("duration_seconds")
        if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not math.isfinite(float(duration)) or float(duration) <= 0):
            raise ValueError(f"video duration is unavailable for {sample['sample_id']}")
        duration = float(duration)
        sample_interval = coverage.get("sample_interval_seconds", 1.0)
        if (isinstance(sample_interval, bool) or not isinstance(sample_interval, (int, float))
                or not math.isfinite(float(sample_interval)) or float(sample_interval) <= 0):
            sample_interval = 1.0
        label_tolerance = max(0.5, float(sample_interval))
        if any(event["end_seconds"] > duration + label_tolerance
               for event in sample["events"]):
            raise ValueError(f"event label extends beyond decoded video duration: {sample['sample_id']}")

        region_ids = {region["id"] for region in sample["observation_regions"]}
        candidates = [candidate for candidate in extract_accident_event_candidates(analysis)
                      if candidate["region_id"] in region_ids
                      and candidate["start_seconds"] < duration]
        for candidate in candidates:
            candidate["end_seconds"] = min(candidate["end_seconds"], duration)
        predictions[sample["sample_id"]] = candidates
        duration_by_sample[sample["sample_id"]] = round(duration, 3)
        analysis_seconds[sample["sample_id"]] = round(elapsed, 3)

    event_metrics = summarize_event_detection(
        manifest, predictions, min_temporal_iou=min_temporal_iou,
        bootstrap_repetitions=bootstrap_repetitions, seed=seed,
    )
    return {
        "task": manifest["task"],
        "dataset": manifest["dataset"],
        "split": "test",
        "split_unit": manifest["split_unit"],
        "test_group_count": len(manifest["split_groups"]["test"]),
        "sample_count": len(manifest["samples"]),
        "event_metrics": event_metrics,
        "video_duration_seconds": duration_by_sample,
        "analysis_seconds_per_video": analysis_seconds,
        "threshold": float(threshold),
        "model_provenance": {
            "detector_file": detector_path.name,
            "detector_sha256": _sha256(detector_path),
            "accident_model_file": accident_model_path.name,
            "accident_model_sha256": _sha256(accident_model_path),
        },
        "limitations": [
            "事件匹配只使用人工标注的路段区域和时间区间；不评估事故成因、严重程度或地理坐标定位。",
            "按清单声明的 flight/scene/incident 分组计算置信区间；分组不独立时区间会过于乐观。",
            "此评测使用当前视频分析生产流程；公开数据结果不能替代武汉大学航拍验证。",
            "通过视频事件指标不等于允许自动封路；路线影响仍须管理员逐条确认。",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True,
                        help="UTF-8 JSON event manifest with flight/scene-disjoint train/validation/test groups")
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--detector", type=Path, required=True, help="local VisDrone ONNX detector")
    parser.add_argument("--accident-model", type=Path, required=True, help="local AIDER classifier ONNX")
    parser.add_argument("--threshold", type=float, required=True,
                        help="freeze a threshold selected on validation videos before test evaluation")
    parser.add_argument("--min-temporal-iou", type=float, default=0.1)
    parser.add_argument("--bootstrap-repetitions", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20261010)
    parser.add_argument("--output", type=Path, help="optional local JSON report path")
    args = parser.parse_args()
    if not args.manifest.is_file():
        parser.error("manifest must exist")
    if (not math.isfinite(args.min_temporal_iou) or not 0 <= args.min_temporal_iou <= 1
            or args.bootstrap_repetitions < 100):
        parser.error("min-temporal-iou must be in [0, 1] and bootstrap repetitions at least 100")
    try:
        manifest_bytes = args.manifest.read_bytes()
        manifest = validate_event_manifest(json.loads(manifest_bytes.decode("utf-8-sig")))
        started = time.monotonic()
        report = run_event_evaluation(
            manifest, args.dataset_root, args.detector, args.accident_model,
            threshold=args.threshold, min_temporal_iou=args.min_temporal_iou,
            bootstrap_repetitions=args.bootstrap_repetitions, seed=args.seed,
        )
        report["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RuntimeError) as error:
        parser.error(str(error))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
