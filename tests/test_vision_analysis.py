from vision.analysis import analyze_observations


def frame_of_stopped_vehicles(count=8, offset=0):
    return [
        {"label": "car", "confidence": 0.92, "track_id": i,
         "box": [100 + i * 30 + offset, 120, 120 + i * 30 + offset, 140]}
        for i in range(count)
    ]


def test_stabilized_video_with_many_stopped_tracked_vehicles_proposes_congestion():
    frames = [frame_of_stopped_vehicles(offset=0) for _ in range(6)]
    result = analyze_observations(frames, frame_width=640, frame_height=480, camera_stabilized=True)
    assert result["metrics"]["frames_analyzed"] == 6
    assert any(item["kind"] == "possible_congestion" and item["review_required"] for item in result["candidates"])


def test_single_image_vehicle_count_never_claims_traffic_congestion():
    result = analyze_observations([frame_of_stopped_vehicles()], frame_width=640, frame_height=480)
    assert all(item["kind"] != "possible_congestion" for item in result["candidates"])
    assert result["metrics"]["motion_assessment"] == "insufficient_single_frame"


def test_video_without_stabilized_camera_never_claims_congestion_from_pixel_motion():
    frames = [frame_of_stopped_vehicles(offset=0) for _ in range(6)]
    result = analyze_observations(frames, frame_width=640, frame_height=480)
    assert result["metrics"]["motion_assessment"] == "camera_motion_uncompensated"
    assert all(item["kind"] != "possible_congestion" for item in result["candidates"])


def test_moving_camera_video_can_report_congestion_only_after_motion_compensation():
    frames = [frame_of_stopped_vehicles(offset=index * 20) for index in range(6)]
    translate_right = [[1, 0, 20], [0, 1, 0], [0, 0, 1]]

    result = analyze_observations(
        frames, frame_width=640, frame_height=480,
        frame_transforms=[None] + [translate_right] * 5,
    )

    assert result["metrics"]["motion_assessment"] == "camera_motion_compensated"
    candidate = next(item for item in result["candidates"] if item["kind"] == "possible_congestion")
    assert candidate["review_required"] is True
    assert candidate["auto_publish"] is False
    assert result["safety"]["automatically_changes_routing"] is False
    assert result["safety"]["accident_recognition_supported"] is False


def test_video_with_unreliable_camera_motion_compensation_only_reports_vehicle_counts():
    frames = [frame_of_stopped_vehicles(offset=index * 20) for index in range(6)]
    result = analyze_observations(
        frames, frame_width=640, frame_height=480,
        frame_transforms=[None, None, None, None, None, None],
    )

    assert result["metrics"]["motion_assessment"] == "camera_motion_uncompensated"
    assert all(item["kind"] != "possible_congestion" for item in result["candidates"])


def test_vehicle_detector_classes_never_become_accident_candidates():
    frame = frame_of_stopped_vehicles()
    result = analyze_observations([frame], frame_width=640, frame_height=480)
    assert all(item["kind"] != "possible_accident" for item in result["candidates"])


def test_accident_candidate_requires_explicit_detector_class_and_human_review():
    frame = [{"label": "accident", "confidence": 0.91, "track_id": None, "box": [10, 10, 80, 80]}]
    unsupported = analyze_observations([frame], frame_width=640, frame_height=480)
    assert all(item["kind"] != "possible_accident" for item in unsupported["candidates"])
    result = analyze_observations(
        [frame], frame_width=640, frame_height=480, accident_model_enabled=True,
    )
    candidate = next(item for item in result["candidates"] if item["kind"] == "possible_accident")
    assert candidate["review_required"] is True
    assert candidate["auto_publish"] is False


def test_vehicle_cluster_is_a_review_observation_not_a_road_condition():
    result = analyze_observations([frame_of_stopped_vehicles()], frame_width=640, frame_height=480)
    assert any(item["kind"] == "vehicle_cluster_review" for item in result["candidates"])
    assert all(item["auto_publish"] is False for item in result["candidates"])
