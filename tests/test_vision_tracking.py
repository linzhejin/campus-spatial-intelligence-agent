from vision.tracking import TRACKER_IMPLEMENTATION, assign_track_ids
from vision.analysis import analyze_observations


def detection(confidence=0.9, label="car", box=(10, 10, 60, 40)):
    return {"label": label, "confidence": confidence, "box": list(box), "track_id": None}


IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def test_tracker_reports_the_pinned_upstream_bytetrack_port():
    assert TRACKER_IMPLEMENTATION == "FoundationVision/ByteTrack@d1bf019"


def test_second_stage_low_confidence_detection_keeps_an_active_track():
    frames = [[detection(0.9)], [detection(0.2)], [detection(0.8)]]

    tracked = assign_track_ids(
        frames,
        frame_transforms=[None, IDENTITY, IDENTITY],
        camera_stabilized=True,
        high_threshold=0.5,
        low_threshold=0.1,
    )

    ids = [frame[0]["track_id"] for frame in tracked]
    assert ids[0] is not None
    assert ids == [ids[0], ids[0], ids[0]]
    assert frames[0][0]["track_id"] is None


def test_low_confidence_detection_without_an_existing_track_does_not_start_one():
    tracked = assign_track_ids(
        [[detection(0.2)]], frame_transforms=[None], camera_stabilized=True,
        high_threshold=0.5, low_threshold=0.1,
    )

    assert tracked[0][0]["track_id"] is None


def test_tracker_does_not_match_detections_across_classes():
    tracked = assign_track_ids(
        [[detection(0.9, "car")], [detection(0.9, "bus")]],
        frame_transforms=[None, IDENTITY], camera_stabilized=True,
        high_threshold=0.5, low_threshold=0.1,
    )

    assert tracked[0][0]["track_id"] != tracked[1][0]["track_id"]


def test_tracker_does_not_reuse_tracks_after_the_configured_gap():
    tracked = assign_track_ids(
        [[detection()], [], [], [detection()]],
        frame_transforms=[None, IDENTITY, IDENTITY, IDENTITY],
        camera_stabilized=True,
        high_threshold=0.5,
        low_threshold=0.1,
        max_lost=1,
    )

    assert tracked[0][0]["track_id"] != tracked[3][0]["track_id"]


def test_tracker_compensates_camera_translation_before_associating_objects():
    shifted_camera = [[1.0, 0.0, 80.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    tracked = assign_track_ids(
        [[detection(box=(10, 10, 60, 40))], [detection(box=(90, 10, 140, 40))]],
        frame_transforms=[None, shifted_camera], camera_stabilized=False,
        high_threshold=0.5, low_threshold=0.1,
    )

    assert tracked[0][0]["track_id"] == tracked[1][0]["track_id"]


def test_unmatched_low_confidence_detections_do_not_inflate_counts_but_matched_ones_do():
    region = {"id": "lane", "kind": "vehicle_lane", "polygon": [
        [0, 0], [1, 0], [1, 1], [0, 1],
    ]}
    unmatched = analyze_observations(
        [[detection(0.2)]], frame_width=100, frame_height=100,
        camera_stabilized=True, regions=[region], tracking_high_threshold=0.5,
        tracking_low_threshold=0.1,
    )
    matched = analyze_observations(
        [[detection(0.9)], [detection(0.2)]], frame_width=100, frame_height=100,
        camera_stabilized=True, regions=[region], tracking_high_threshold=0.5,
        tracking_low_threshold=0.1,
    )

    assert unmatched["metrics"]["peak_vehicle_count"] == 0
    assert matched["metrics"]["peak_vehicle_count"] == 1
    assert matched["metrics"]["mean_vehicle_count"] == 1
