"""Camera-motion-aware adapter for the upstream ByteTrack association flow."""
from __future__ import annotations

from math import isfinite

import numpy as np

from vision.vendor.bytetrack import BYTETracker


TRACKER_IMPLEMENTATION = "FoundationVision/ByteTrack@d1bf019"
TRACKABLE_LABELS = {
    "car", "bus", "truck", "motorcycle", "motorbike", "motor", "van",
    "vehicle", "tricycle", "awning-tricycle", "bicycle",
    "pedestrian", "person", "people",
}


def _multiply(left, right):
    return [
        [sum(left[row][index] * right[index][column] for index in range(3))
         for column in range(3)]
        for row in range(3)
    ]


def _transform_point(matrix, point):
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
    corners = [
        _transform_point(matrix, (box[0], box[1])),
        _transform_point(matrix, (box[2], box[1])),
        _transform_point(matrix, (box[2], box[3])),
        _transform_point(matrix, (box[0], box[3])),
    ]
    if any(point is None for point in corners):
        return None
    projected = [
        min(point[0] for point in corners), min(point[1] for point in corners),
        max(point[0] for point in corners), max(point[1] for point in corners),
    ]
    if not all(isfinite(float(value)) for value in projected):
        return None
    return projected


def _transform_between(first_frame, last_frame, frame_transforms):
    if last_frame <= first_frame:
        return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    matrix = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    for frame_index in range(first_frame + 1, last_frame + 1):
        transform = (
            frame_transforms[frame_index]
            if frame_transforms and frame_index < len(frame_transforms) else None
        )
        if transform is None:
            return None
        matrix = _multiply(transform, matrix)
    return matrix


def _camera_stabilized_box(box, *, frame_index, anchor_frame, frame_transforms, camera_stabilized):
    if camera_stabilized or frame_index == anchor_frame:
        return [float(value) for value in box]
    forward = _transform_between(anchor_frame, frame_index, frame_transforms)
    if forward is None:
        return None
    try:
        inverse = np.linalg.inv(np.asarray(forward, dtype=np.float64))
    except (np.linalg.LinAlgError, TypeError, ValueError):
        return None
    return _project_box(box, inverse.tolist())


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
    """Run ByteTrack on per-class detections in a camera-stabilized frame.

    The ROI/video engine supplies transforms mapping frame ``i-1`` to frame
    ``i``. Boxes are projected back into the current stable-camera anchor before
    Kalman prediction and IoU association. A failed transform resets the local
    tracker so a camera jump cannot silently link unrelated detections.
    ``high_iou_threshold`` and ``low_iou_threshold`` are retained for caller
    compatibility; official ByteTrack uses cost gates of 0.8 and 0.5.
    """
    if not 0 <= low_threshold <= high_threshold <= 1:
        raise ValueError("tracking confidence thresholds must satisfy 0 <= low <= high <= 1")
    if max_lost < 0:
        raise ValueError("max_lost must be non-negative")
    if high_iou_threshold < 0 or low_iou_threshold < 0:
        raise ValueError("tracking IoU thresholds must be non-negative")

    copied = [[dict(item) for item in frame] for frame in frames]
    existing_ids = [
        int(item["track_id"])
        for frame in copied for item in frame
        if isinstance(item.get("track_id"), (int, float))
        and not isinstance(item.get("track_id"), bool)
    ]
    next_id = max(existing_ids, default=-1) + 1
    id_map = {}
    trackers = {}
    anchor_frame = 0

    for frame_index, detections in enumerate(copied):
        if not camera_stabilized and frame_index > anchor_frame:
            forward = _transform_between(anchor_frame, frame_index, frame_transforms)
            try:
                invertible = forward is not None and abs(float(np.linalg.det(forward))) > 1e-12
            except (np.linalg.LinAlgError, TypeError, ValueError):
                invertible = False
            if not invertible:
                trackers = {}
                anchor_frame = frame_index

        by_label = {}
        for detection_index, item in enumerate(detections):
            label = str(item.get("label", "")).strip().lower()
            try:
                confidence = float(item.get("confidence", 0.0))
            except (TypeError, ValueError, OverflowError):
                continue
            if not isfinite(confidence) or not 0 <= confidence <= 1:
                continue
            if item.get("track_id") is not None or label not in TRACKABLE_LABELS:
                if (item.get("track_id") is None and confidence >= high_threshold
                        and label not in TRACKABLE_LABELS):
                    item["track_id"] = next_id
                    next_id += 1
                continue
            stable_box = _camera_stabilized_box(
                item["box"], frame_index=frame_index, anchor_frame=anchor_frame,
                frame_transforms=frame_transforms, camera_stabilized=camera_stabilized,
            )
            if stable_box is None or stable_box[2] <= stable_box[0] or stable_box[3] <= stable_box[1]:
                continue
            by_label.setdefault(label, []).append((detection_index, stable_box, confidence))

        labels = set(trackers) | set(by_label)
        for label in labels:
            tracker = trackers.get(label)
            if tracker is None:
                tracker = BYTETracker(
                    track_threshold=high_threshold,
                    low_threshold=low_threshold,
                    match_threshold=0.8,
                    max_lost=max_lost,
                )
                trackers[label] = tracker
            selected = by_label.get(label, [])
            payload = [
                [*box, confidence, detection_index]
                for detection_index, box, confidence in selected
            ]
            active_tracks = tracker.update(payload)
            for track in active_tracks:
                detection_index = track.detection_index
                if detection_index is None or not 0 <= detection_index < len(detections):
                    continue
                stable_id = id_map.get((label, int(track.track_id)))
                if stable_id is None:
                    stable_id = next_id
                    next_id += 1
                    id_map[(label, int(track.track_id))] = stable_id
                detections[detection_index]["track_id"] = stable_id

    return copied
