"""Leakage-aware evaluation helpers for the optional aerial classifiers."""
from __future__ import annotations

import math

from vision.specialized import AIDER_CLASSES, FLOODNET_CLASSES


def _prf(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 6), "recall": round(recall, 6), "f1": round(f1, 6)}


def summarize_classification(actual, predicted, *, classes=AIDER_CLASSES,
                             accident_class="traffic_incident") -> dict:
    actual, predicted = list(actual), list(predicted)
    classes = tuple(classes)
    if not classes or len(actual) != len(predicted):
        raise ValueError("classification labels must have equal non-empty lengths")
    if not actual:
        raise ValueError("classification evaluation requires at least one sample")
    if accident_class not in classes:
        raise ValueError("accident_class must be present in classes")
    if any(item not in classes for item in actual + predicted):
        raise ValueError("classification label is outside the declared class order")
    per_class = {}
    confusion = {expected: {received: 0 for received in classes} for expected in classes}
    for expected, received in zip(actual, predicted):
        confusion[expected][received] += 1
    for label in classes:
        tp = confusion[label][label]
        fp = sum(confusion[other][label] for other in classes if other != label)
        fn = sum(confusion[label][other] for other in classes if other != label)
        per_class[label] = {"support": actual.count(label), **_prf(tp, fp, fn)}
    actual_positive = [item == accident_class for item in actual]
    predicted_positive = [item == accident_class for item in predicted]
    tp = sum(left and right for left, right in zip(actual_positive, predicted_positive))
    fp = sum(not left and right for left, right in zip(actual_positive, predicted_positive))
    fn = sum(left and not right for left, right in zip(actual_positive, predicted_positive))
    tn = len(actual) - tp - fp - fn
    binary = {"tp": tp, "fp": fp, "fn": fn, "tn": tn, **_prf(tp, fp, fn)}
    return {
        "sample_count": len(actual),
        "accuracy": round(sum(left == right for left, right in zip(actual, predicted)) / len(actual), 6),
        "macro_f1": round(sum(item["f1"] for item in per_class.values()) / len(per_class), 6),
        "class_order": list(classes),
        "confusion_matrix": confusion,
        "per_class": per_class,
        "traffic_incident_binary": binary,
    }


def summarize_binary_counts(*, tp: int, fp: int, fn: int) -> dict:
    values = (tp, fp, fn)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
        raise ValueError("binary counts must be non-negative integers")
    union = tp + fp + fn
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "iou": round(tp / union, 6) if union else None,
        **_prf(tp, fp, fn),
    }


def summarize_binary_classification(actual_positive, predicted_positive) -> dict:
    actual_positive, predicted_positive = list(actual_positive), list(predicted_positive)
    if not actual_positive or len(actual_positive) != len(predicted_positive):
        raise ValueError("binary classification labels must have equal non-empty lengths")
    if any(not isinstance(value, bool) for value in actual_positive + predicted_positive):
        raise ValueError("binary classification labels must be boolean")
    tp = sum(actual and predicted for actual, predicted in zip(actual_positive, predicted_positive))
    fp = sum(not actual and predicted for actual, predicted in zip(actual_positive, predicted_positive))
    fn = sum(actual and not predicted for actual, predicted in zip(actual_positive, predicted_positive))
    tn = len(actual_positive) - tp - fp - fn
    specificity = tn / (tn + fp) if tn + fp else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "specificity": round(specificity, 6), **_prf(tp, fp, fn)}


def validate_split_manifest(manifest, *, task: str) -> dict:
    """Validate a held-out manifest with explicit disjoint flight/scene groups."""
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("manifest schema_version must be 1")
    if task not in {"accident_classification", "flooded_road_segmentation"}:
        raise ValueError("unsupported evaluation task")
    if manifest.get("task") != task:
        raise ValueError("manifest task does not match the requested evaluation task")
    if not str(manifest.get("dataset") or "").strip():
        raise ValueError("manifest dataset name is required")
    if not str(manifest.get("split_unit") or "").strip():
        raise ValueError("manifest split_unit must name the independent flight, scene, or incident")
    groups = manifest.get("split_groups")
    if not isinstance(groups, dict):
        raise ValueError("manifest split_groups is required")
    normalized_groups = {}
    for split in ("train", "validation", "test"):
        values = groups.get(split)
        if not isinstance(values, list):
            raise ValueError(f"split_groups.{split} must be a list")
        normalized = [str(value).strip() for value in values]
        if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError(f"split_groups.{split} contains an empty or duplicate group")
        normalized_groups[split] = normalized
    if not normalized_groups["test"]:
        raise ValueError("test split must contain at least one independent group")
    all_groups = [group for values in normalized_groups.values() for group in values]
    if len(all_groups) != len(set(all_groups)):
        raise ValueError("train, validation, and test groups must not overlap")
    samples = manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("manifest samples must be a non-empty list")
    seen_images = set()
    allowed_classes = set(AIDER_CLASSES) if task == "accident_classification" else None
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            raise ValueError(f"sample {index} must be an object")
        image = str(sample.get("image") or "").strip()
        group_id = str(sample.get("group_id") or "").strip()
        if not image or not group_id:
            raise ValueError(f"sample {index} requires image and group_id")
        if image in seen_images:
            raise ValueError("manifest contains a duplicate image path")
        seen_images.add(image)
        if group_id not in normalized_groups["test"]:
            raise ValueError(f"sample {index} does not belong to the declared test split")
        if task == "accident_classification":
            if sample.get("label") not in allowed_classes:
                raise ValueError(f"sample {index} has an unknown AIDER class")
            if manifest.get("class_order") != list(AIDER_CLASSES):
                raise ValueError("accident manifest class_order must match the AIDER model output order")
        else:
            mask = str(sample.get("mask") or "").strip()
            polygon = sample.get("road_surface_polygon")
            if not mask or not isinstance(polygon, list) or len(polygon) < 3:
                raise ValueError(f"sample {index} requires a mask and road_surface_polygon")
            class_id = sample.get("flooded_road_class_id", 3)
            if isinstance(class_id, bool) or class_id != FLOODNET_CLASSES.index("flooded_road"):
                raise ValueError(f"sample {index} must use FloodNet flooded_road class id 3")
            for point in polygon:
                if (not isinstance(point, (list, tuple)) or len(point) != 2
                        or any(isinstance(value, bool) or not isinstance(value, (int, float))
                               or not math.isfinite(float(value)) or not 0 <= value <= 1
                               for value in point)):
                    raise ValueError(f"sample {index} has an invalid normalized road polygon")
    result = dict(manifest)
    result["split_groups"] = normalized_groups
    return result


def cluster_bootstrap_interval(group_values: dict[str, object], statistic, *,
                               repetitions: int = 1000, seed: int = 20261010):
    """Return percentile intervals by resampling independent scenes, not frames."""
    import random

    if len(group_values) < 2:
        return None
    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions < 100:
        raise ValueError("bootstrap repetitions must be at least 100")
    groups = list(group_values)
    rng = random.Random(seed)
    samples = []
    for _ in range(repetitions):
        chosen = [rng.choice(groups) for _ in groups]
        value = statistic([group_values[group] for group in chosen])
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            samples.append(float(value))
    if not samples:
        return None
    samples.sort()
    return {
        "lower_95": round(samples[int(0.025 * (len(samples) - 1))], 6),
        "upper_95": round(samples[int(0.975 * (len(samples) - 1))], 6),
        "independent_groups": len(groups),
        "bootstrap_repetitions": repetitions,
        "seed": seed,
    }
