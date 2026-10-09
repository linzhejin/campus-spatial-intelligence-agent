"""ByteTrack-style two-stage association for review-only aerial counts.

This small, dependency-free implementation follows ByteTrack's key association
idea: match high-confidence detections first, then use low-confidence detections
to recover unmatched active tracks. It is not a vendored copy of the upstream
ByteTrack tracker and does not claim its benchmark performance.
"""
from __future__ import annotations

from math import hypot


TRACKABLE_LABELS = {
    "car", "bus", "truck", "motorcycle", "motorbike", "motor", "van",
    "vehicle", "tricycle", "awning-tricycle", "bicycle",
    "pedestrian", "person", "people",
}


def _center(box):
    return ((float(box[0]) + float(box[2])) / 2, (float(box[1]) + float(box[3])) / 2)


def _multiply(left, right):
    return [
        [sum(left[row][index] * right[index][column] for index in range(3))
         for column in range(3)]
        for row in range(3)
    ]


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


def _project_box(box, matrix):
    if matrix is None:
        return None
    corners = [
        _transform_point(matrix, (box[0], box[1])),
        _transform_point(matrix, (box[2], box[1])),
        _transform_point(matrix, (box[2], box[3])),
        _transform_point(matrix, (box[0], box[3])),
    ]
    if any(point is None for point in corners):
        return None
    return [
        min(point[0] for point in corners), min(point[1] for point in corners),
        max(point[0] for point in corners), max(point[1] for point in corners),
    ]


def _iou(left, right):
    x1, y1 = max(left[0], right[0]), max(left[1], right[1])
    x2, y2 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def _transform_between(first_frame, last_frame, frame_transforms):
    if last_frame <= first_frame:
        return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    matrix = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    for frame_index in range(first_frame + 1, last_frame + 1):
        transform = (frame_transforms[frame_index]
                     if frame_transforms and frame_index < len(frame_transforms) else None)
        if transform is None:
            return None
        matrix = _multiply(transform, matrix)
    return matrix


def assign_track_ids(
    frames,
    *,
    frame_transforms=None,
    camera_stabilized=False,
    high_threshold=0.369,
    low_threshold=0.1,
    high_iou_threshold=0.1,
    low_iou_threshold=0.05,
    max_lost=1,
):
    """Associate frame detections in high-then-low confidence passes.

    Unmatched low-confidence detections do not create new tracks. A camera
    transform is required between observations unless the video is declared
    stabilized, so camera motion cannot silently become object motion.
    """
    if not 0 <= low_threshold <= high_threshold <= 1:
        raise ValueError("tracking confidence thresholds must satisfy 0 <= low <= high <= 1")
    if max_lost < 0:
        raise ValueError("max_lost must be non-negative")

    copied = [[dict(item) for item in frame] for frame in frames]
    existing_ids = [
        int(item["track_id"])
        for frame in copied for item in frame
        if isinstance(item.get("track_id"), (int, float)) and not isinstance(item.get("track_id"), bool)
    ]
    next_id = max(existing_ids, default=-1) + 1
    active = {}

    for frame_index, detections in enumerate(copied):
        for track_id in list(active):
            if frame_index - active[track_id]["frame_index"] > max_lost + 1:
                del active[track_id]

        for item in detections:
            if item.get("track_id") is not None:
                try:
                    track_id = int(item["track_id"])
                except (TypeError, ValueError, OverflowError):
                    item["track_id"] = None
                    continue
                active[track_id] = {
                    "label": str(item.get("label", "")).strip().lower(),
                    "box": list(item["box"]), "frame_index": frame_index,
                }

        high_indices = [
            index for index, item in enumerate(detections)
            if item.get("track_id") is None and float(item.get("confidence", 0)) >= high_threshold
        ]
        low_indices = [
            index for index, item in enumerate(detections)
            if item.get("track_id") is None
            and low_threshold <= float(item.get("confidence", 0)) < high_threshold
        ]
        available_tracks = {
            track_id: track for track_id, track in active.items()
            if track["label"] in TRACKABLE_LABELS
            and 0 < frame_index - track["frame_index"] <= max_lost + 1
        }

        can_associate = camera_stabilized or frame_index == 0 or bool(frame_transforms)

        def match(indices, tracks, minimum_iou):
            if not can_associate or not indices or not tracks:
                return set(), set()
            possible = []
            for track_id, track in tracks.items():
                matrix = (
                    [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
                    if camera_stabilized else
                    _transform_between(track["frame_index"], frame_index, frame_transforms)
                )
                predicted = _project_box(track["box"], matrix)
                if predicted is None:
                    continue
                for detection_index in indices:
                    item = detections[detection_index]
                    label = str(item.get("label", "")).strip().lower()
                    if label != track["label"]:
                        continue
                    overlap = _iou(predicted, item["box"])
                    if overlap >= minimum_iou:
                        distance = hypot(
                            _center(predicted)[0] - _center(item["box"])[0],
                            _center(predicted)[1] - _center(item["box"])[1],
                        )
                        possible.append((-overlap, distance, track_id, detection_index))
            used_tracks, used_detections = set(), set()
            for _overlap, _distance, track_id, detection_index in sorted(possible):
                if track_id in used_tracks or detection_index in used_detections:
                    continue
                item = detections[detection_index]
                item["track_id"] = track_id
                active[track_id] = {
                    "label": str(item.get("label", "")).strip().lower(),
                    "box": list(item["box"]), "frame_index": frame_index,
                }
                used_tracks.add(track_id)
                used_detections.add(detection_index)
            return used_tracks, used_detections

        matched_tracks, matched_high = match(high_indices, available_tracks, high_iou_threshold)
        remaining_tracks = {
            track_id: track for track_id, track in available_tracks.items()
            if track_id not in matched_tracks
        }
        _, matched_low = match(low_indices, remaining_tracks, low_iou_threshold)

        for detection_index in high_indices:
            if detection_index in matched_high:
                continue
            item = detections[detection_index]
            item["track_id"] = next_id
            active[next_id] = {
                "label": str(item.get("label", "")).strip().lower(),
                "box": list(item["box"]), "frame_index": frame_index,
            }
            next_id += 1

        # Keep legacy IDs for non-trackable high-confidence categories without
        # letting them match traffic tracks in later frames.
        for detection_index in high_indices:
            item = detections[detection_index]
            if item.get("track_id") is None:
                item["track_id"] = next_id
                next_id += 1
        del matched_low

    return copied
