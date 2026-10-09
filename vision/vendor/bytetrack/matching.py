"""Association helpers adapted from FoundationVision/ByteTrack (MIT)."""
import numpy as np
from scipy.optimize import linear_sum_assignment


def _iou(left, right):
    x1 = np.maximum(left[:, None, 0], right[None, :, 0])
    y1 = np.maximum(left[:, None, 1], right[None, :, 1])
    x2 = np.minimum(left[:, None, 2], right[None, :, 2])
    y2 = np.minimum(left[:, None, 3], right[None, :, 3])
    intersection = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    left_area = np.maximum(0.0, left[:, 2] - left[:, 0]) * np.maximum(0.0, left[:, 3] - left[:, 1])
    right_area = np.maximum(0.0, right[:, 2] - right[:, 0]) * np.maximum(0.0, right[:, 3] - right[:, 1])
    union = left_area[:, None] + right_area[None, :] - intersection
    return np.divide(intersection, union, out=np.zeros_like(intersection), where=union > 0)


def iou_distance(tracks, detections):
    if not tracks or not detections:
        return np.zeros((len(tracks), len(detections)), dtype=np.float64)
    track_boxes = np.asarray([track.tlbr for track in tracks], dtype=np.float64)
    detection_boxes = np.asarray([track.tlbr for track in detections], dtype=np.float64)
    return 1.0 - _iou(track_boxes, detection_boxes)


def linear_assignment(cost_matrix, thresh):
    """Minimum-cost assignment with upstream ByteTrack's maximum valid-match rule."""
    cost_matrix = np.asarray(cost_matrix, dtype=np.float64)
    rows, columns = cost_matrix.shape
    if not rows or not columns:
        return np.empty((0, 2), dtype=int), tuple(range(rows)), tuple(range(columns))
    unmatched_cost = float(thresh) + 1.0
    invalid_cost = unmatched_cost * 1000.0
    # Dummy rows/columns encode unmatched tracks/detections. The augmented
    # problem first maximizes the number of assignments under `thresh`, then
    # minimizes their costs, matching lapjv(cost_limit=...) semantics.
    augmented = np.full((rows + columns, columns + rows), invalid_cost, dtype=np.float64)
    valid = np.isfinite(cost_matrix) & (cost_matrix <= float(thresh))
    augmented[:rows, :columns] = np.where(valid, cost_matrix, invalid_cost)
    augmented[np.arange(rows), columns + np.arange(rows)] = unmatched_cost
    augmented[rows + np.arange(columns), np.arange(columns)] = unmatched_cost
    augmented[rows:, columns:] = 0.0
    assigned_rows, assigned_columns = linear_sum_assignment(augmented)
    pairs = [(row, column) for row, column in zip(assigned_rows, assigned_columns)
             if row < rows and column < columns and valid[row, column]]
    matches = np.asarray(pairs, dtype=int).reshape((-1, 2))
    used_rows = set(matches[:, 0].tolist()) if len(matches) else set()
    used_columns = set(matches[:, 1].tolist()) if len(matches) else set()
    unmatched_rows = tuple(index for index in range(rows) if index not in used_rows)
    unmatched_columns = tuple(index for index in range(columns) if index not in used_columns)
    return matches, unmatched_rows, unmatched_columns


def fuse_score(cost_matrix, detections):
    if np.asarray(cost_matrix).size == 0:
        return cost_matrix
    iou_similarity = 1.0 - cost_matrix
    scores = np.asarray([detection.score for detection in detections], dtype=np.float64)[None, :]
    return 1.0 - iou_similarity * scores
