#!/usr/bin/env python3
"""Evaluate local AIDER/FloodNet-compatible ONNX weights on a held-out manifest.

The evaluator never creates a random image-level split. The manifest must list
disjoint independent flight/scene groups and only test-split samples.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import cv2
import numpy as np

from vision.specialized import AIDER_CLASSES, OnnxAiderClassifier, OnnxFloodSegmenter
from vision.specialized_evaluation import (
    cluster_bootstrap_interval,
    summarize_binary_classification,
    summarize_binary_counts,
    summarize_classification,
    validate_split_manifest,
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
        raise ValueError("dataset files must use safe relative paths")
    base = root.resolve()
    path = (base / normalized).resolve()
    try:
        path.relative_to(base)
    except ValueError as error:
        raise ValueError("dataset file resolves outside dataset root") from error
    if not path.is_file():
        raise ValueError(f"dataset file does not exist: {normalized}")
    return path


def _classification_metric(records, classes):
    return summarize_classification(
        [record["actual"] for record in records],
        [record["predicted"] for record in records],
        classes=classes,
    )


def _evaluate_classification(manifest, root: Path, model_path: Path, threshold: float):
    classifier = OnnxAiderClassifier(str(model_path), threshold=threshold)
    records = []
    elapsed = []
    for sample in manifest["samples"]:
        image_path = _dataset_path(root, sample["image"])
        frame = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError(f"cannot decode test image: {sample['image']}")
        started = time.monotonic()
        prediction = classifier.predict(frame)
        elapsed.append(time.monotonic() - started)
        records.append({
            "group_id": sample["group_id"],
            "actual": sample["label"],
            "predicted": prediction["class"],
            "traffic_incident_probability": prediction["traffic_accident_probability"],
        })
    summary = _classification_metric(records, AIDER_CLASSES)
    summary["traffic_incident_binary"] = summarize_binary_classification(
        [record["actual"] == "traffic_incident" for record in records],
        [record["traffic_incident_probability"] >= threshold for record in records],
    )
    summary["traffic_incident_binary"]["threshold"] = threshold
    group_records = {}
    for record in records:
        group_records.setdefault(record["group_id"], []).append(record)
    summary["group_bootstrap_95_ci"] = {
        "traffic_incident_recall": cluster_bootstrap_interval(
            group_records,
            lambda batches: summarize_binary_classification(
                [record["actual"] == "traffic_incident"
                 for batch in batches for record in batch],
                [record["traffic_incident_probability"] >= threshold
                 for batch in batches for record in batch],
            )["recall"],
        ),
        "accuracy": cluster_bootstrap_interval(
            group_records,
            lambda batches: _classification_metric(
                [record for batch in batches for record in batch], AIDER_CLASSES,
            )["accuracy"],
        ),
    }
    summary["mean_inference_seconds_per_image"] = round(sum(elapsed) / len(elapsed), 4)
    return summary


def _polygon_mask(frame_shape, polygon):
    height, width = frame_shape[:2]
    points = np.asarray([
        [round(float(point[0]) * (width - 1)), round(float(point[1]) * (height - 1))]
        for point in polygon
    ], dtype=np.int32)
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(mask, [points], 1)
    return mask.astype(bool)


def _evaluate_flood(manifest, root: Path, model_path: Path, min_area_ratio: float):
    segmenter = OnnxFloodSegmenter(str(model_path), min_area_ratio=min_area_ratio)
    tp = fp = fn = 0
    group_counts = {}
    sample_iou = []
    for sample in manifest["samples"]:
        image_path = _dataset_path(root, sample["image"])
        mask_path = _dataset_path(root, sample["mask"])
        frame = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        truth_labels = cv2.imread(str(mask_path), cv2.IMREAD_UNCHANGED)
        if frame is None:
            raise ValueError(f"cannot decode test image: {sample['image']}")
        if truth_labels is None or truth_labels.ndim != 2:
            raise ValueError(f"ground truth must be a grayscale class-index mask: {sample['mask']}")
        if truth_labels.shape != frame.shape[:2]:
            raise ValueError(f"image and mask dimensions differ: {sample['image']}")
        region = {
            "id": sample["group_id"], "kind": "road_surface",
            "polygon": sample["road_surface_polygon"],
        }
        prediction = segmenter.predict(frame, region, include_mask=True)
        if prediction is None:
            raise ValueError(f"road ROI is empty: {sample['image']}")
        roi = _polygon_mask(frame.shape, sample["road_surface_polygon"])
        predicted = prediction["flooded_road_mask"].astype(bool) & roi
        actual = (truth_labels == int(sample.get("flooded_road_class_id", 3))) & roi
        sample_tp = int(np.logical_and(predicted, actual).sum())
        sample_fp = int(np.logical_and(predicted, ~actual & roi).sum())
        sample_fn = int(np.logical_and(~predicted, actual).sum())
        tp += sample_tp
        fp += sample_fp
        fn += sample_fn
        group = group_counts.setdefault(sample["group_id"], [0, 0, 0])
        group[0] += sample_tp
        group[1] += sample_fp
        group[2] += sample_fn
        per_sample = summarize_binary_counts(tp=sample_tp, fp=sample_fp, fn=sample_fn)
        sample_iou.append(per_sample["iou"])
    overall = summarize_binary_counts(tp=tp, fp=fp, fn=fn)

    def bootstrap_iou(batches):
        counts = [sum(batch[index] for batch in batches) for index in range(3)]
        return summarize_binary_counts(tp=counts[0], fp=counts[1], fn=counts[2])["iou"]

    return {
        **overall,
        "sample_count": len(manifest["samples"]),
        "mean_per_image_iou": round(
            sum(value for value in sample_iou if value is not None)
            / max(1, sum(value is not None for value in sample_iou)), 6,
        ) if any(value is not None for value in sample_iou) else None,
        "group_bootstrap_95_ci_iou": cluster_bootstrap_interval(group_counts, bootstrap_iou),
        "interpretation": "像素指标仅在标注的路面观察区域计算；不代表水深，也不等同于道路封闭准确率。",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=("accident_classification", "flooded_road_segmentation"), required=True)
    parser.add_argument("--manifest", type=Path, required=True,
                        help="UTF-8 JSON held-out manifest with disjoint train/validation/test groups")
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True, help="local ONNX weights; never copied into Git")
    parser.add_argument("--threshold", type=float, default=0.85,
                        help="AIDER traffic_incident probability threshold")
    parser.add_argument("--flood-min-area", type=float, default=0.08,
                        help="Flooded-road minimum image-region fraction")
    parser.add_argument("--output", type=Path, help="optional JSON report path")
    args = parser.parse_args()
    if not args.manifest.is_file() or not args.dataset_root.is_dir() or not args.model.is_file():
        parser.error("manifest, dataset root, and model must exist")
    if not 0 < args.threshold <= 1 or not 0 < args.flood_min_area <= 1:
        parser.error("thresholds must be greater than zero and at most one")
    try:
        manifest_bytes = args.manifest.read_bytes()
        manifest = validate_split_manifest(
            json.loads(manifest_bytes.decode("utf-8-sig")), task=args.task,
        )
        model_path = args.model.resolve()
        started = time.monotonic()
        metrics = (
            _evaluate_classification(manifest, args.dataset_root, model_path, args.threshold)
            if args.task == "accident_classification" else
            _evaluate_flood(manifest, args.dataset_root, model_path, args.flood_min_area)
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RuntimeError) as error:
        parser.error(str(error))
    report = {
        "task": args.task,
        "dataset": manifest["dataset"],
        "split": "test",
        "split_unit": manifest["split_unit"],
        "independent_test_groups": len(manifest["split_groups"]["test"]),
        "sample_count": len(manifest["samples"]),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "model_sha256": _sha256(model_path),
        "model_file": model_path.name,
        "metrics": metrics,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "limitations": [
            "此脚本评测本地提供的权重，不训练模型。",
            "测试集必须按航次、事故或独立场景分组；不得将相邻视频帧随机分入不同集合。",
            "公开数据结果不能替代武汉大学航拍样本的外域验证。",
        ],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
