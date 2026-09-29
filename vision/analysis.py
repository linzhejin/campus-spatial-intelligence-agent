"""Conservative evidence summarization for aerial traffic detections.

Outputs are review candidates, never road closures or routing updates.
"""
from __future__ import annotations

from collections import defaultdict
from math import hypot
from statistics import mean, median
from typing import Any


VEHICLE_LABELS = {
    "car", "bus", "truck", "motorcycle", "motorbike", "motor", "van",
    "vehicle", "tricycle", "awning-tricycle",
}
INCIDENT_LABELS = {"accident", "crash", "collision", "vehicle_accident", "overturned_vehicle"}


def _valid_detection(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get("label"), str)
        and isinstance(item.get("box"), (tuple, list))
        and len(item["box"]) == 4
        and all(isinstance(value, (int, float)) for value in item["box"])
        and isinstance(item.get("confidence"), (int, float))
    )


def _center(box):
    return ((float(box[0]) + float(box[2])) / 2, (float(box[1]) + float(box[3])) / 2)


def _candidate(kind: str, confidence: float, reason: str, evidence: dict) -> dict:
    return {
        "kind": kind,
        "confidence": round(max(0.0, min(1.0, confidence)), 3),
        "reason": reason,
        "evidence": evidence,
        "review_required": True,
        "auto_publish": False,
        "status": "pending_review",
    }


def analyze_observations(
    frames: list[list[dict]], *, frame_width: int, frame_height: int,
    sample_interval_s: float = 1.0,
    camera_stabilized: bool = False,
    min_vehicle_count: int = 6,
    stationary_ratio_threshold: float = 0.5,
) -> dict:
    """Summarize frame detections into human-review evidence.

    Stationary-vehicle inference is only meaningful for a fixed or stabilized
    camera. Camera motion cannot be corrected without pose/georeference data, so
    callers must carry this limitation into the review UI.
    """
    if frame_width <= 0 or frame_height <= 0 or sample_interval_s <= 0:
        raise ValueError("valid frame dimensions and sample interval are required")
    valid_frames = [[item for item in frame if _valid_detection(item)] for frame in frames]
    vehicle_frames: list[list[dict]] = []
    track_positions: dict[str, list[tuple[float, float, int]]] = defaultdict(list)
    accident_detections: list[tuple[int, float, str]] = []
    coverage_values: list[float] = []

    for frame_index, detections in enumerate(valid_frames):
        vehicles = [item for item in detections if item["label"].strip().lower() in VEHICLE_LABELS]
        vehicle_frames.append(vehicles)
        covered = 0.0
        for item in vehicles:
            x1, y1, x2, y2 = map(float, item["box"])
            covered += max(0.0, x2 - x1) * max(0.0, y2 - y1)
            track_id = item.get("track_id")
            if track_id is not None:
                cx, cy = _center(item["box"])
                track_positions[str(track_id)].append((cx, cy, frame_index))
        coverage_values.append(min(1.0, covered / (frame_width * frame_height)))
        for item in detections:
            label = item["label"].strip().lower().replace(" ", "_")
            confidence = float(item["confidence"])
            if label in INCIDENT_LABELS:
                accident_detections.append((frame_index, confidence, label))

    vehicle_counts = [len(items) for items in vehicle_frames]
    diagonal = hypot(frame_width, frame_height)
    track_speeds = []
    for positions in track_positions.values():
        if len(positions) < 3:
            continue
        positions.sort(key=lambda value: value[2])
        first_index, last_index = positions[0][2], positions[-1][2]
        elapsed = max(sample_interval_s, (last_index - first_index) * sample_interval_s)
        distance = sum(
            hypot(right[0] - left[0], right[1] - left[1])
            for left, right in zip(positions, positions[1:])
        )
        track_speeds.append(distance / diagonal / elapsed)

    stationary_count = sum(speed <= 0.0025 for speed in track_speeds)
    stationary_ratio = stationary_count / len(track_speeds) if track_speeds else 0.0
    avg_vehicle_count = mean(vehicle_counts) if vehicle_counts else 0.0
    candidates = []

    # A single frame shows vehicle presence/density only; it cannot establish
    # speed, delay, or a traffic queue.
    if len(valid_frames) == 1 and vehicle_counts and vehicle_counts[0] >= min_vehicle_count:
        candidates.append(_candidate(
            "vehicle_cluster_review", min(0.9, vehicle_counts[0] / (min_vehicle_count * 2)),
            "单帧检测到多辆车辆；静态影像不能判断速度或拥堵，需视频或现场复核。",
            {"vehicle_count": vehicle_counts[0], "occupied_area_ratio": round(coverage_values[0], 4)},
        ))

    if (
        len(valid_frames) >= 5
        and camera_stabilized
        and avg_vehicle_count >= min_vehicle_count
        and len(track_speeds) >= 3
        and stationary_ratio >= stationary_ratio_threshold
    ):
        candidates.append(_candidate(
            "possible_congestion", min(0.95, 0.5 + stationary_ratio / 2),
            "稳定视角视频中检测到多辆持续低位移车辆；需核验镜头稳定性、道路位置和现场通行情况。",
            {
                "mean_vehicle_count": round(avg_vehicle_count, 2),
                "stationary_track_count": stationary_count,
                "tracked_vehicle_count": len(track_speeds),
                "stationary_track_ratio": round(stationary_ratio, 3),
                "camera_motion_compensated": False,
            },
        ))

    if accident_detections:
        by_label: dict[str, list[tuple[int, float, str]]] = defaultdict(list)
        for detection in accident_detections:
            by_label[detection[2]].append(detection)
        strongest = max(
            (max(items, key=lambda value: value[1]) for items in by_label.values()),
            key=lambda value: value[1],
        )
        repeated = len({item[0] for item in by_label[strongest[2]]}) >= 2
        threshold = 0.6 if len(valid_frames) > 1 and repeated else 0.85
        if strongest[1] >= threshold:
            candidates.append(_candidate(
                "possible_accident", strongest[1],
                "检测模型给出事故类别候选；需管理员查看原始影像并核实位置后确认。",
                {"detector_label": strongest[2], "frames_with_detection": len({item[0] for item in by_label[strongest[2]]})},
            ))

    return {
        "metrics": {
            "frames_analyzed": len(valid_frames),
            "mean_vehicle_count": round(avg_vehicle_count, 2),
            "peak_vehicle_count": max(vehicle_counts, default=0),
            "mean_vehicle_occupied_area_ratio": round(mean(coverage_values), 4) if coverage_values else 0.0,
            "tracked_vehicle_count": len(track_speeds),
            "stationary_track_count": stationary_count,
            "stationary_track_ratio": round(stationary_ratio, 3),
            "motion_assessment": (
                "insufficient_single_frame" if len(valid_frames) < 2
                else "operator_declared_stabilized" if camera_stabilized
                else "camera_motion_uncompensated"
            ),
        },
        "candidates": candidates,
        "safety": {
            "requires_human_review": True,
            "camera_motion_compensated": False,
            "camera_stabilized_assumed": bool(camera_stabilized),
            "automatically_changes_routing": False,
        },
    }
