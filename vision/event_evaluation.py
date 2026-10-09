"""Leakage-aware event-level evaluation for aerial-video accident candidates."""
from __future__ import annotations

import math

from vision.specialized_evaluation import cluster_bootstrap_interval, summarize_binary_counts


def _safe_time(value, field: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value))):
        raise ValueError(f"{field} must be a finite number")
    return float(value)


def _validate_polygon(polygon, *, sample_id: str, region_id: str) -> None:
    if not isinstance(polygon, list) or len(polygon) < 3:
        raise ValueError(f"sample {sample_id} region {region_id} requires a polygon with at least three points")
    points = []
    for point in polygon:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError(f"sample {sample_id} region {region_id} has an invalid polygon point")
        x, y = (_safe_time(value, "polygon coordinate") for value in point)
        if not 0 <= x <= 1 or not 0 <= y <= 1:
            raise ValueError(f"sample {sample_id} region {region_id} polygon coordinates must be between zero and one")
        points.append((x, y))
    area = abs(sum(
        x1 * y2 - x2 * y1
        for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1])
    )) / 2
    if area <= 1e-9:
        raise ValueError(f"sample {sample_id} region {region_id} polygon must have non-zero area")


def validate_event_manifest(manifest: dict) -> dict:
    """Validate one held-out video per event window and flight/scene-disjoint groups."""
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("event manifest schema_version must be 1")
    if manifest.get("task") != "accident_event_detection":
        raise ValueError("event manifest task must be accident_event_detection")
    if not str(manifest.get("dataset") or "").strip():
        raise ValueError("event manifest dataset name is required")
    split_unit = str(manifest.get("split_unit") or "").strip()
    split_unit_key = split_unit.lower().replace("-", "_")
    if not split_unit or any(token in split_unit_key for token in
                             ("frame", "image", "pixel", "sample", "clip", "video")):
        raise ValueError("split_unit must identify an independent flight, scene, or incident")

    groups = manifest.get("split_groups")
    if not isinstance(groups, dict):
        raise ValueError("event manifest split_groups is required")
    normalized_groups = {}
    for split in ("train", "validation", "test"):
        values = groups.get(split)
        if not isinstance(values, list) or not values:
            raise ValueError(f"split_groups.{split} must be a non-empty list")
        normalized = [str(value).strip() for value in values]
        if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError(f"split_groups.{split} contains empty or duplicate IDs")
        normalized_groups[split] = normalized
    all_groups = [group for values in normalized_groups.values() for group in values]
    if len(all_groups) != len(set(all_groups)):
        raise ValueError("train, validation, and test groups must not overlap")

    samples = manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("event manifest samples must be a non-empty list")
    sample_ids, video_paths, observed_test_groups = set(), set(), set()
    normalized_samples = []
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            raise ValueError(f"sample {index} must be an object")
        sample_id = str(sample.get("sample_id") or "").strip()
        group_id = str(sample.get("group_id") or "").strip()
        video = str(sample.get("video") or "").strip().replace("\\", "/")
        if not sample_id or sample_id in sample_ids:
            raise ValueError(f"sample {index} requires a unique sample_id")
        if group_id not in normalized_groups["test"]:
            raise ValueError(f"sample {sample_id} must belong to a declared test group")
        if (not video or video.startswith("/") or ":" in video.split("/")[0]
                or any(part in {"", ".", ".."} for part in video.split("/"))):
            raise ValueError(f"sample {sample_id} video must use a safe relative path")
        if video in video_paths:
            raise ValueError("event manifest contains a duplicate video path")
        sample_ids.add(sample_id)
        video_paths.add(video)
        observed_test_groups.add(group_id)
        if not isinstance(sample.get("camera_stabilized"), bool):
            raise ValueError(f"sample {sample_id} camera_stabilized must be boolean")

        regions = sample.get("observation_regions")
        if not isinstance(regions, list) or not regions:
            raise ValueError(f"sample {sample_id} requires a marked vehicle_lane observation region")
        region_ids = set()
        for region in regions:
            if not isinstance(region, dict):
                raise ValueError(f"sample {sample_id} observation regions must be objects")
            region_id = str(region.get("id") or "").strip()
            if (not region_id or region_id in region_ids
                    or region.get("kind") != "vehicle_lane"):
                raise ValueError(f"sample {sample_id} regions must have unique IDs and kind vehicle_lane")
            _validate_polygon(region.get("polygon"), sample_id=sample_id, region_id=region_id)
            region_ids.add(region_id)

        events = sample.get("events")
        if not isinstance(events, list):
            raise ValueError(f"sample {sample_id} events must be a list; use an empty list for a negative clip")
        event_ids = set()
        normalized_events = []
        for event_index, event in enumerate(events):
            if not isinstance(event, dict):
                raise ValueError(f"sample {sample_id} event {event_index} must be an object")
            event_id = str(event.get("event_id") or "").strip()
            region_id = str(event.get("region_id") or "").strip()
            start = _safe_time(event.get("start_seconds"), "start_seconds")
            end = _safe_time(event.get("end_seconds"), "end_seconds")
            if not event_id or event_id in event_ids:
                raise ValueError(f"sample {sample_id} requires unique event_id values")
            if region_id not in region_ids:
                raise ValueError(f"sample {sample_id} event {event_id} must reference a marked vehicle_lane region")
            if start < 0 or end <= start:
                raise ValueError("end_seconds must be after start_seconds and start_seconds cannot be negative")
            event_ids.add(event_id)
            normalized_events.append({**event, "event_id": event_id, "region_id": region_id,
                                      "start_seconds": start, "end_seconds": end})
        normalized_samples.append({**sample, "sample_id": sample_id, "group_id": group_id,
                                   "video": video, "events": normalized_events})

    if observed_test_groups != set(normalized_groups["test"]):
        raise ValueError("test groups must all have at least one video")

    result = dict(manifest)
    result["split_groups"] = normalized_groups
    result["samples"] = normalized_samples
    return result


def extract_accident_event_candidates(analysis: dict) -> list[dict]:
    """Convert production pipeline candidates to temporal event intervals."""
    candidates = []
    rows = analysis.get("candidates", []) if isinstance(analysis, dict) else []
    if not isinstance(rows, list):
        return candidates
    for index, candidate in enumerate(rows):
        if not isinstance(candidate, dict) or candidate.get("kind") != "possible_accident":
            continue
        evidence = candidate.get("evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        summary = evidence.get("summary")
        summary = summary if isinstance(summary, dict) else {}
        region_id = str(summary.get("region_id") or "").strip()
        segments = evidence.get("segments")
        if not region_id or not isinstance(segments, list):
            continue
        intervals = []
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            try:
                start = _safe_time(segment.get("start_seconds"), "candidate start_seconds")
                end = _safe_time(segment.get("end_seconds"), "candidate end_seconds")
            except ValueError:
                continue
            if start >= 0 and end > start:
                intervals.append((start, end))
        if not intervals:
            continue
        try:
            confidence = _safe_time(candidate.get("confidence"), "candidate confidence")
        except ValueError:
            confidence = 0.0
        candidates.append({
            "candidate_id": str(candidate.get("candidate_id") or f"candidate-{index + 1}"),
            "region_id": region_id,
            "start_seconds": min(item[0] for item in intervals),
            "end_seconds": max(item[1] for item in intervals),
            "confidence": confidence,
        })
    return candidates


def _temporal_iou(left: dict, right: dict) -> float:
    start = max(float(left["start_seconds"]), float(right["start_seconds"]))
    end = min(float(left["end_seconds"]), float(right["end_seconds"]))
    overlap = max(0.0, end - start)
    union_start = min(float(left["start_seconds"]), float(right["start_seconds"]))
    union_end = max(float(left["end_seconds"]), float(right["end_seconds"]))
    union = union_end - union_start
    return overlap / union if union > 0 else 0.0


def match_event_candidates(actual_events: list[dict], predicted_events: list[dict], *,
                           min_temporal_iou: float = 0.1) -> dict:
    """One-to-one matching by region and maximum temporal intersection-over-union."""
    if (isinstance(min_temporal_iou, bool) or not isinstance(min_temporal_iou, (int, float))
            or not math.isfinite(float(min_temporal_iou)) or not 0 <= min_temporal_iou <= 1):
        raise ValueError("min_temporal_iou must be between zero and one")
    actual = list(actual_events)
    predicted = list(predicted_events)
    if not actual or not predicted:
        return {"matches": [],
                "unmatched_event_ids": [str(item.get("event_id") or "") for item in actual],
                "unmatched_candidate_ids": [str(item.get("candidate_id") or "") for item in predicted]}

    import numpy as np
    from scipy.optimize import linear_sum_assignment

    scores = np.zeros((len(actual), len(predicted)), dtype=np.float64)
    for row, event in enumerate(actual):
        for col, candidate in enumerate(predicted):
            if str(event.get("region_id")) != str(candidate.get("region_id")):
                continue
            iou = _temporal_iou(event, candidate)
            if iou >= float(min_temporal_iou) and iou > 0:
                scores[row, col] = iou
    rows, columns = linear_sum_assignment(-scores)
    matches = []
    matched_actual, matched_predicted = set(), set()
    for row, column in zip(rows, columns):
        score = float(scores[row, column])
        if score <= 0:
            continue
        event, candidate = actual[row], predicted[column]
        matches.append({"event_id": str(event.get("event_id") or ""),
                        "candidate_id": str(candidate.get("candidate_id") or ""),
                        "region_id": str(event.get("region_id") or ""),
                        "temporal_iou": round(score, 6)})
        matched_actual.add(row)
        matched_predicted.add(column)
    matches.sort(key=lambda item: (item["event_id"], item["candidate_id"]))
    return {
        "matches": matches,
        "unmatched_event_ids": [str(item.get("event_id") or "") for index, item in enumerate(actual)
                                if index not in matched_actual],
        "unmatched_candidate_ids": [str(item.get("candidate_id") or "")
                                    for index, item in enumerate(predicted)
                                    if index not in matched_predicted],
    }


def summarize_event_detection(manifest: dict, predictions_by_sample: dict[str, list[dict]], *,
                              min_temporal_iou: float = 0.1,
                              bootstrap_repetitions: int = 1000,
                              seed: int = 20261010) -> dict:
    """Report event precision/recall with confidence intervals clustered by flight."""
    if not isinstance(predictions_by_sample, dict):
        raise ValueError("predictions_by_sample must be an object keyed by sample_id")
    known_ids = {sample["sample_id"] for sample in manifest["samples"]}
    unknown_ids = set(predictions_by_sample) - known_ids
    if unknown_ids:
        raise ValueError(f"unknown sample_id: {sorted(unknown_ids)[0]}")
    group_counts = {}
    per_sample = {}
    total = {"tp": 0, "fp": 0, "fn": 0}
    for sample in manifest["samples"]:
        candidates = predictions_by_sample.get(sample["sample_id"], [])
        if not isinstance(candidates, list):
            raise ValueError(f"predictions for {sample['sample_id']} must be a list")
        result = match_event_candidates(sample["events"], candidates,
                                        min_temporal_iou=min_temporal_iou)
        counts = {"tp": len(result["matches"]), "fp": len(result["unmatched_candidate_ids"]),
                  "fn": len(result["unmatched_event_ids"])}
        for key, value in counts.items():
            total[key] += value
        group = group_counts.setdefault(sample["group_id"], {"tp": 0, "fp": 0, "fn": 0})
        for key, value in counts.items():
            group[key] += value
        per_sample[sample["sample_id"]] = {
            "group_id": sample["group_id"], "counts": counts,
            "ground_truth_event_count": len(sample["events"]),
            "candidate_count": len(candidates), "matches": result["matches"],
            "unmatched_event_ids": result["unmatched_event_ids"],
            "unmatched_candidate_ids": result["unmatched_candidate_ids"],
        }

    aggregate = summarize_binary_counts(**total)
    metrics = {key: aggregate[key] for key in ("precision", "recall", "f1")}

    def bootstrap_metric(batches, metric):
        counts = {key: sum(batch[key] for batch in batches) for key in ("tp", "fp", "fn")}
        return summarize_binary_counts(**counts)[metric]

    intervals = {
        metric: cluster_bootstrap_interval(
            group_counts, lambda batches, metric=metric: bootstrap_metric(batches, metric),
            repetitions=bootstrap_repetitions, seed=seed,
        )
        for metric in ("precision", "recall", "f1")
    }
    return {
        "counts": total, "metrics": metrics,
        "flight_cluster_bootstrap_95_ci": intervals,
        "flight_group_count": len(group_counts),
        "test_sample_count": len(per_sample),
        "ground_truth_event_count": sum(item["ground_truth_event_count"] for item in per_sample.values()),
        "candidate_count": sum(item["candidate_count"] for item in per_sample.values()),
        "min_temporal_iou": float(min_temporal_iou), "samples": per_sample,
    }
