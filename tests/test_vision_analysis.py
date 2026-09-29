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


def test_accident_candidate_requires_explicit_detector_class_and_human_review():
    frame = [{"label": "accident", "confidence": 0.91, "track_id": None, "box": [10, 10, 80, 80]}]
    result = analyze_observations([frame], frame_width=640, frame_height=480)
    candidate = next(item for item in result["candidates"] if item["kind"] == "possible_accident")
    assert candidate["review_required"] is True
    assert candidate["auto_publish"] is False


def test_vehicle_cluster_is_a_review_observation_not_a_road_condition():
    result = analyze_observations([frame_of_stopped_vehicles()], frame_width=640, frame_height=480)
    assert any(item["kind"] == "vehicle_cluster_review" for item in result["candidates"])
    assert all(item["auto_publish"] is False for item in result["candidates"])
