"""Conservative evidence summarization for aerial traffic detections.

Outputs are review candidates, never road closures or routing updates.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from math import hypot
from statistics import mean
from typing import Any


VEHICLE_LABELS = {
    "car", "bus", "truck", "motorcycle", "motorbike", "motor", "van",
    "vehicle", "tricycle", "awning-tricycle",
}
INCIDENT_LABELS = {"accident", "crash", "collision", "vehicle_accident", "overturned_vehicle"}
PEDESTRIAN_LABELS = {"pedestrian", "person", "people"}
ROI_KINDS = {"vehicle_lane", "pedestrian", "parking", "exclude"}


def _validated_regions(regions, frame_width: int, frame_height: int) -> list[dict]:
    if regions is None:
        return []
    if not isinstance(regions, list) or len(regions) > 24:
        raise ValueError("观察区域必须是最多 24 个多边形组成的列表")
    normalized = []
    for region in regions:
        if not isinstance(region, dict) or region.get("kind") not in ROI_KINDS:
            raise ValueError("观察区域类型无效")
        polygon = region.get("polygon")
        if not isinstance(polygon, list) or not 3 <= len(polygon) <= 128:
            raise ValueError("观察区域至少需要 3 个、最多 128 个顶点")
        points = []
        for point in polygon:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError("观察区域坐标无效")
            x, y = point
            if (isinstance(x, bool) or isinstance(y, bool)
                    or not isinstance(x, (int, float)) or not isinstance(y, (int, float))
                    or not 0 <= x <= 1 or not 0 <= y <= 1):
                raise ValueError("观察区域坐标必须是 0 到 1 之间的画面比例")
            points.append([float(x) * frame_width, float(y) * frame_height])
        normalized.append({
            "id": str(region.get("id") or f"region-{len(normalized) + 1}")[:64],
            "kind": region["kind"],
            "polygon": points,
        })
    return normalized


def _inside_polygon(x: float, y: float, polygon: list[list[float]]) -> bool:
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y):
            crossing_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing_x:
                inside = not inside
        previous = current
    return inside


def _in_regions(item: dict, regions: list[dict]) -> bool:
    x, y = _center(item["box"])
    return any(_inside_polygon(x, y, region["polygon"]) for region in regions)


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


def _transform_point(matrix, point):
    if matrix is None:
        return None
    x, y = point
    try:
        denominator = matrix[2][0] * x + matrix[2][1] * y + matrix[2][2]
        if abs(float(denominator)) < 1e-9:
            return None
        return (
            (matrix[0][0] * x + matrix[0][1] * y + matrix[0][2]) / denominator,
            (matrix[1][0] * x + matrix[1][1] * y + matrix[1][2]) / denominator,
        )
    except (IndexError, TypeError, ValueError, ZeroDivisionError):
        return None


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


def _assign_track_ids(frames, transforms, *, camera_stabilized: bool, max_distance: float):
    """Link adjacent-frame detections using the estimated background transform."""
    copied = [[dict(item) for item in frame] for frame in frames]
    existing = [
        int(item["track_id"])
        for frame in copied for item in frame
        if isinstance(item.get("track_id"), (int, float))
    ]
    next_id = max(existing, default=-1) + 1
    if not copied:
        return copied
    for item in copied[0]:
        if item.get("track_id") is None:
            item["track_id"] = next_id
            next_id += 1

    for frame_index in range(1, len(copied)):
        matrix = None
        if camera_stabilized:
            matrix = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        elif transforms and frame_index < len(transforms):
            matrix = transforms[frame_index]
        if matrix is None:
            for item in copied[frame_index]:
                if item.get("track_id") is None:
                    item["track_id"] = next_id
                    next_id += 1
            continue

        previous = [
            item for item in copied[frame_index - 1]
            if item.get("track_id") is not None
            and item.get("label", "").strip().lower() in VEHICLE_LABELS
        ]
        current = [
            (index, item) for index, item in enumerate(copied[frame_index])
            if item.get("label", "").strip().lower() in VEHICLE_LABELS
        ]
        possible = []
        for before in previous:
            predicted = _transform_point(matrix, _center(before["box"]))
            if predicted is None:
                continue
            for index, after in current:
                if after.get("track_id") is not None:
                    continue
                if before["label"].strip().lower() != after["label"].strip().lower():
                    continue
                observed = _center(after["box"])
                distance = hypot(predicted[0] - observed[0], predicted[1] - observed[1])
                if distance <= max_distance:
                    possible.append((distance, int(before["track_id"]), index, after))
        used_tracks, used_detections = set(), set()
        for _distance, track_id, index, item in sorted(possible, key=lambda candidate: candidate[0]):
            if track_id in used_tracks or index in used_detections:
                continue
            item["track_id"] = track_id
            used_tracks.add(track_id)
            used_detections.add(index)
        for _index, item in current:
            if item.get("track_id") is None:
                item["track_id"] = next_id
                next_id += 1
        for item in copied[frame_index]:
            if item.get("track_id") is None:
                item["track_id"] = next_id
                next_id += 1
    return copied


def analyze_observations(
    frames: list[list[dict]], *, frame_width: int, frame_height: int,
    sample_interval_s: float = 1.0,
    camera_stabilized: bool = False,
    frame_transforms: list[list[list[float]] | None] | None = None,
    accident_model_enabled: bool = False,
    regions: list[dict] | None = None,
    min_vehicle_count: int = 6,
    stationary_ratio_threshold: float = 0.5,
) -> dict:
    """Summarize detections and compensate vehicle motion for verified camera motion.

    ``frame_transforms[i]`` maps coordinates in frame ``i-1`` to frame ``i``.
    A missing transform is treated as failed camera-motion estimation. A video
    congestion candidate requires at least 80% valid frame-to-frame transforms,
    unless the manager explicitly declares a fixed or already stabilized camera.
    """
    if frame_width <= 0 or frame_height <= 0 or sample_interval_s <= 0:
        raise ValueError("valid frame dimensions and sample interval are required")
    regions = _validated_regions(regions, frame_width, frame_height)
    vehicle_regions = [region for region in regions if region["kind"] == "vehicle_lane"]
    pedestrian_regions = [region for region in regions if region["kind"] == "pedestrian"]
    parking_regions = [region for region in regions if region["kind"] == "parking"]

    def outside_exclusions(item):
        return not any(_in_regions(item, [region]) for region in regions if region["kind"] == "exclude")

    valid_frames = [
        [item for item in frame if _valid_detection(item) and outside_exclusions(item)]
        for frame in frames
    ]
    transition_count = max(0, len(valid_frames) - 1)
    valid_transition_count = 0 if camera_stabilized else sum(
        1 for index in range(1, len(valid_frames))
        if frame_transforms and index < len(frame_transforms) and frame_transforms[index] is not None
    )
    valid_transition_ratio = (
        1.0 if camera_stabilized and transition_count
        else valid_transition_count / transition_count if transition_count else 0.0
    )
    motion_compensation_ready = (
        camera_stabilized
        or (transition_count > 0 and valid_transition_ratio >= 0.8)
    )
    track_frames = _assign_track_ids(
        valid_frames, frame_transforms, camera_stabilized=camera_stabilized,
        max_distance=hypot(frame_width, frame_height) * 0.08,
    )
    vehicle_frames: list[list[dict]] = []
    pedestrian_counts: list[int] = []
    track_positions: dict[str, list[tuple[float, float, int]]] = defaultdict(list)
    accident_detections: list[tuple[int, float, str]] = []
    coverage_values: list[float] = []
    class_counts: list[Counter] = []

    for frame_index, detections in enumerate(track_frames):
        all_vehicles = [item for item in detections
                        if item["label"].strip().lower() in VEHICLE_LABELS]
        vehicles = (
            [item for item in all_vehicles if _in_regions(item, vehicle_regions)]
            if vehicle_regions else all_vehicles
        )
        if parking_regions:
            vehicles = [item for item in vehicles if not _in_regions(item, parking_regions)]
        vehicle_frames.append(vehicles)
        class_counts.append(Counter(item["label"].strip().lower() for item in detections))
        people = [item for item in detections if item["label"].strip().lower() in PEDESTRIAN_LABELS]
        if pedestrian_regions:
            people = [item for item in people if _in_regions(item, pedestrian_regions)]
        pedestrian_counts.append(len(people))
        covered = 0.0
        for item in vehicles:
            x1, y1, x2, y2 = map(float, item["box"])
            covered += max(0.0, x2 - x1) * max(0.0, y2 - y1)
            track_id = item.get("track_id")
            if track_id is not None:
                cx, cy = _center(item["box"])
                track_positions[str(track_id)].append((cx, cy, frame_index))
        coverage_values.append(min(1.0, covered / (frame_width * frame_height)))
        if accident_model_enabled:
            for item in detections:
                label = item["label"].strip().lower().replace(" ", "_")
                if label in INCIDENT_LABELS:
                    accident_detections.append((frame_index, float(item["confidence"]), label))

    vehicle_counts = [len(items) for items in vehicle_frames]
    diagonal = hypot(frame_width, frame_height)
    track_speeds = []
    for positions in track_positions.values():
        positions.sort(key=lambda value: value[2])
        residuals = []
        for left, right in zip(positions, positions[1:]):
            if right[2] != left[2] + 1:
                continue
            matrix = (
                [[1, 0, 0], [0, 1, 0], [0, 0, 1]] if camera_stabilized
                else frame_transforms[right[2]] if frame_transforms and right[2] < len(frame_transforms)
                else None
            )
            predicted = _transform_point(matrix, (left[0], left[1]))
            if predicted is not None:
                residuals.append(hypot(right[0] - predicted[0], right[1] - predicted[1]))
        if len(residuals) >= 2:
            track_speeds.append(mean(residuals) / diagonal / sample_interval_s)

    stationary_count = sum(speed <= 0.0025 for speed in track_speeds)
    stationary_ratio = stationary_count / len(track_speeds) if track_speeds else 0.0
    avg_vehicle_count = mean(vehicle_counts) if vehicle_counts else 0.0
    candidates = []

    # A single frame shows vehicle presence/density only, not speed or congestion.
    if (vehicle_regions and len(valid_frames) == 1 and vehicle_counts
            and vehicle_counts[0] >= min_vehicle_count):
        candidates.append(_candidate(
            "vehicle_cluster_review", min(0.9, vehicle_counts[0] / (min_vehicle_count * 2)),
            "单帧检测到多辆车辆；静态影像不能判断速度或拥堵，需视频或现场复核。",
            {"vehicle_count": vehicle_counts[0], "occupied_area_ratio": round(coverage_values[0], 4)},
        ))

    if (
        vehicle_regions
        and camera_stabilized
        and
        len(valid_frames) >= 5
        and motion_compensation_ready
        and avg_vehicle_count >= min_vehicle_count
        and len(track_speeds) >= 3
        and stationary_ratio >= stationary_ratio_threshold
    ):
        candidates.append(_candidate(
            "possible_congestion", min(0.95, 0.5 + stationary_ratio / 2),
            "多帧车辆在相机运动校正后仍呈低位移；请核实观察区域、道路位置和现场通行情况。",
            {
                "mean_vehicle_count": round(avg_vehicle_count, 2),
                "stationary_track_count": stationary_count,
                "tracked_vehicle_count": len(track_speeds),
                "stationary_track_ratio": round(stationary_ratio, 3),
                "camera_motion_compensated": not camera_stabilized,
                "valid_motion_transition_ratio": round(valid_transition_ratio, 3),
            },
        ))

    if (
        pedestrian_regions and camera_stabilized and len(pedestrian_counts) >= 3
        and max(pedestrian_counts, default=0) >= 15
        and sum(count > 0 for count in pedestrian_counts) >= 3
    ):
        candidates.append(_candidate(
            "possible_crowding",
            min(0.9, 0.55 + max(pedestrian_counts) / 100),
            "行人观察区域内连续检测到较多人群；未标定有效面积，不能换算为每平方米人数，请结合原片和现场核实。",
            {
                "region_id": pedestrian_regions[0]["id"],
                "peak_person_count": max(pedestrian_counts),
                "mean_person_count": round(mean(pedestrian_counts), 2),
                "frames_with_people": sum(count > 0 for count in pedestrian_counts),
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
                "专用事故模型给出事故类别候选；需管理员查看原始影像并核实位置后确认。",
                {"detector_label": strongest[2], "frames_with_detection": len({item[0] for item in by_label[strongest[2]]})},
            ))

    all_classes = set().union(*(counts.keys() for counts in class_counts)) if class_counts else set()
    mean_class_counts = {
        label: round(mean(counts.get(label, 0) for counts in class_counts), 2)
        for label in sorted(all_classes)
    }
    peak_class_counts = {
        label: max((counts.get(label, 0) for counts in class_counts), default=0)
        for label in sorted(all_classes)
    }
    return {
        "metrics": {
            "frames_analyzed": len(valid_frames),
            "mean_vehicle_count": round(avg_vehicle_count, 2),
            "peak_vehicle_count": max(vehicle_counts, default=0),
            "mean_pedestrian_count": round(mean(pedestrian_counts), 2) if pedestrian_counts else 0.0,
            "peak_pedestrian_count": max(pedestrian_counts, default=0),
            "mean_vehicle_occupied_area_ratio": round(mean(coverage_values), 4) if coverage_values else 0.0,
            "mean_class_counts": mean_class_counts,
            "peak_class_counts": peak_class_counts,
            "tracked_vehicle_count": len(track_speeds),
            "stationary_track_count": stationary_count,
            "stationary_track_ratio": round(stationary_ratio, 3),
            "motion_compensation_valid_transitions": valid_transition_count,
            "motion_compensation_transition_count": transition_count,
            "motion_compensation_valid_ratio": round(valid_transition_ratio, 3),
            "motion_assessment": (
                "insufficient_single_frame" if len(valid_frames) < 2
                else "operator_declared_stabilized" if camera_stabilized
                else "camera_motion_compensated" if motion_compensation_ready
                else "camera_motion_uncompensated"
            ),
        },
        "candidates": candidates,
        "safety": {
            "requires_human_review": True,
            "camera_motion_compensated": bool(motion_compensation_ready and not camera_stabilized),
            "camera_stabilized_assumed": bool(camera_stabilized),
            "accident_recognition_supported": False,
            "pedestrian_region_required_for_crowding": True,
            "automatically_changes_routing": False,
        },
    }
