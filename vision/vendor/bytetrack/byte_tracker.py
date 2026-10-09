"""NumPy/SciPy adaptation of FoundationVision ByteTrack (MIT)."""
from types import SimpleNamespace

import numpy as np

from vision.vendor.bytetrack.basetrack import BaseTrack, TrackState
from vision.vendor.bytetrack.kalman_filter import KalmanFilter
from vision.vendor.bytetrack import matching


class STrack(BaseTrack):
    shared_kalman = KalmanFilter()

    def __init__(self, tlwh, score, detection_index=None):
        super().__init__()
        self._tlwh = np.asarray(tlwh, dtype=np.float64)
        self.kalman_filter = None
        self.mean = self.covariance = None
        self.score = float(score)
        self.tracklet_len = 0
        self.detection_index = detection_index

    def activate(self, kalman_filter, frame_id):
        self.kalman_filter = kalman_filter
        self.track_id = self.next_id()
        self.mean, self.covariance = self.kalman_filter.initiate(self.tlwh_to_xyah(self._tlwh))
        self.tracklet_len = 0
        self.state = TrackState.Tracked
        self.is_activated = frame_id == 1
        self.frame_id = self.start_frame = frame_id

    def re_activate(self, new_track, frame_id, new_id=False):
        self.mean, self.covariance = self.kalman_filter.update(
            self.mean, self.covariance, self.tlwh_to_xyah(new_track.tlwh),
        )
        self.tracklet_len = 0
        self.state = TrackState.Tracked
        self.is_activated = True
        self.frame_id = frame_id
        self.detection_index = new_track.detection_index
        if new_id:
            self.track_id = self.next_id()
        self.score = new_track.score

    def update(self, new_track, frame_id):
        self.frame_id = frame_id
        self.tracklet_len += 1
        self.mean, self.covariance = self.kalman_filter.update(
            self.mean, self.covariance, self.tlwh_to_xyah(new_track.tlwh),
        )
        self.state = TrackState.Tracked
        self.is_activated = True
        self.score = new_track.score
        self.detection_index = new_track.detection_index

    @property
    def tlwh(self):
        if self.mean is None:
            return self._tlwh.copy()
        box = self.mean[:4].copy()
        box[2] *= box[3]
        box[:2] -= box[2:] / 2
        return box

    @property
    def tlbr(self):
        box = self.tlwh.copy()
        box[2:] += box[:2]
        return box

    @staticmethod
    def tlwh_to_xyah(tlwh):
        result = np.asarray(tlwh, dtype=np.float64).copy()
        if result[3] <= 0:
            raise ValueError("ByteTrack requires positive-height detection boxes")
        result[:2] += result[2:] / 2
        result[2] /= result[3]
        return result

    @staticmethod
    def tlbr_to_tlwh(tlbr):
        result = np.asarray(tlbr, dtype=np.float64).copy()
        result[2:] -= result[:2]
        return result


def joint_stracks(first, second):
    by_id = {track.track_id: track for track in first}
    return list(by_id.values()) + [track for track in second if track.track_id not in by_id]


def sub_stracks(first, second):
    excluded = {track.track_id for track in second}
    return [track for track in first if track.track_id not in excluded]


def remove_duplicate_stracks(tracked, lost):
    if not tracked or not lost:
        return tracked, lost
    distances = matching.iou_distance(tracked, lost)
    duplicate_pairs = np.where(distances < 0.15)
    drop_tracked, drop_lost = set(), set()
    for tracked_index, lost_index in zip(*duplicate_pairs):
        tracked_age = tracked[tracked_index].frame_id - tracked[tracked_index].start_frame
        lost_age = lost[lost_index].frame_id - lost[lost_index].start_frame
        if tracked_age > lost_age:
            drop_lost.add(lost_index)
        else:
            drop_tracked.add(tracked_index)
    return ([track for index, track in enumerate(tracked) if index not in drop_tracked],
            [track for index, track in enumerate(lost) if index not in drop_lost])


class BYTETracker:
    """ByteTrack high/low-score association using an in-memory detection stream."""

    def __init__(self, *, track_threshold, low_threshold=0.1, match_threshold=0.8, max_lost=2):
        self.tracked_stracks = []
        self.lost_stracks = []
        self.removed_stracks = []
        self.frame_id = 0
        self.args = SimpleNamespace(
            track_thresh=float(track_threshold),
            low_thresh=float(low_threshold),
            match_thresh=float(match_threshold),
            mot20=False,
        )
        self.det_thresh = self.args.track_thresh + 0.1
        self.max_time_lost = int(max_lost)
        self.kalman_filter = KalmanFilter()

    def update(self, detections):
        values = np.asarray(detections, dtype=np.float64)
        if values.size == 0:
            values = np.empty((0, 6), dtype=np.float64)
        if values.ndim != 2 or values.shape[1] != 6:
            raise ValueError("ByteTrack detections must be Nx6 [xyxy, score, index]")
        self.frame_id += 1
        expired = [
            track for track in self.lost_stracks
            if self.frame_id - track.end_frame > self.max_time_lost
        ]
        for track in expired:
            track.mark_removed()
        self.removed_stracks.extend(expired)
        self.lost_stracks = [track for track in self.lost_stracks if track not in expired]
        activated, refound, newly_lost, removed = [], [], [], []
        boxes, scores = values[:, :4], values[:, 4]
        high_indices = np.flatnonzero(scores > self.args.track_thresh)
        second_indices = np.flatnonzero((scores > self.args.low_thresh) & (scores < self.args.track_thresh))
        detections_high = [STrack(STrack.tlbr_to_tlwh(boxes[index]), scores[index], int(values[index, 5]))
                           for index in high_indices]
        unconfirmed, tracked = [], []
        for track in self.tracked_stracks:
            (unconfirmed if not track.is_activated else tracked).append(track)

        pool = joint_stracks(tracked, self.lost_stracks)
        if pool:
            means, covariances = STrack.shared_kalman.multi_predict(
                np.asarray([track.mean.copy() for track in pool]),
                np.asarray([track.covariance for track in pool]),
            )
            for track, mean, covariance in zip(pool, means, covariances):
                track.mean, track.covariance = mean, covariance

        distances = matching.iou_distance(pool, detections_high)
        if not self.args.mot20:
            distances = matching.fuse_score(distances, detections_high)
        matches, unmatched_tracks, unmatched_high = matching.linear_assignment(
            distances, thresh=self.args.match_thresh,
        )
        for track_index, detection_index in matches:
            track, detection = pool[track_index], detections_high[detection_index]
            if track.state == TrackState.Tracked:
                track.update(detection, self.frame_id)
                activated.append(track)
            else:
                track.re_activate(detection, self.frame_id, new_id=False)
                refound.append(track)

        detections_low = [STrack(STrack.tlbr_to_tlwh(boxes[index]), scores[index], int(values[index, 5]))
                          for index in second_indices]
        remaining_tracked = [pool[index] for index in unmatched_tracks
                             if pool[index].state == TrackState.Tracked]
        distances = matching.iou_distance(remaining_tracked, detections_low)
        matches, unmatched_tracks, _ = matching.linear_assignment(distances, thresh=0.5)
        for track_index, detection_index in matches:
            track, detection = remaining_tracked[track_index], detections_low[detection_index]
            if track.state == TrackState.Tracked:
                track.update(detection, self.frame_id)
                activated.append(track)
            else:
                track.re_activate(detection, self.frame_id, new_id=False)
                refound.append(track)
        for track_index in unmatched_tracks:
            track = remaining_tracked[track_index]
            if track.state != TrackState.Lost:
                track.mark_lost()
                newly_lost.append(track)

        remaining_high = [detections_high[index] for index in unmatched_high]
        distances = matching.iou_distance(unconfirmed, remaining_high)
        if not self.args.mot20:
            distances = matching.fuse_score(distances, remaining_high)
        matches, unmatched_unconfirmed, unmatched_high = matching.linear_assignment(distances, thresh=0.7)
        for track_index, detection_index in matches:
            unconfirmed[track_index].update(remaining_high[detection_index], self.frame_id)
            activated.append(unconfirmed[track_index])
        for track_index in unmatched_unconfirmed:
            track = unconfirmed[track_index]
            track.mark_removed()
            removed.append(track)

        for detection_index in unmatched_high:
            track = remaining_high[detection_index]
            if track.score < self.det_thresh:
                continue
            track.activate(self.kalman_filter, self.frame_id)
            activated.append(track)
        for track in self.lost_stracks:
            if self.frame_id - track.end_frame > self.max_time_lost:
                track.mark_removed()
                removed.append(track)

        self.tracked_stracks = [track for track in self.tracked_stracks
                                if track.state == TrackState.Tracked]
        self.tracked_stracks = joint_stracks(self.tracked_stracks, activated)
        self.tracked_stracks = joint_stracks(self.tracked_stracks, refound)
        self.lost_stracks = sub_stracks(self.lost_stracks, self.tracked_stracks)
        self.lost_stracks.extend(newly_lost)
        self.lost_stracks = sub_stracks(self.lost_stracks, self.removed_stracks)
        self.removed_stracks.extend(removed)
        self.tracked_stracks, self.lost_stracks = remove_duplicate_stracks(
            self.tracked_stracks, self.lost_stracks,
        )
        return [track for track in self.tracked_stracks if track.is_activated]
