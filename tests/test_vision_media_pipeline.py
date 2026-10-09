import sys

import cv2
import numpy as np
import pytest

from vision.engine import analyze_media, _compact_specialized_observations
from vision.worker import load_optional_models


class FakeDetector:
    model_id = "test-detector"
    model_version = "fixture-v1"
    confidence_threshold = 0.4

    def __init__(self):
        self.calls = 0

    def detect(self, _frame, *, tracking=False):
        self.calls += 1
        return [
            {"label": "car", "box": [10, 10, 40, 40], "confidence": 0.91, "track_id": None},
            {"label": "pedestrian", "box": [50, 10, 60, 30], "confidence": 0.88, "track_id": None},
        ]


def test_optional_model_failure_does_not_disable_other_visual_capabilities():
    class AccidentLoader:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("bad accident model")

    class FloodLoader:
        def __init__(self, *_args, **_kwargs):
            self.model_id = "flood-fixture"

    accident, flood, capabilities = load_optional_models(
        accident_path="bad-aider.onnx", flood_path="valid-flood.onnx",
        accident_loader=AccidentLoader, flood_loader=FloodLoader,
    )

    assert accident is None
    assert flood.model_id == "flood-fixture"
    assert capabilities == {
        "accident_recognition_supported": False,
        "flood_segmentation_supported": True,
    }


def test_single_image_pipeline_returns_vehicle_boxes_without_claiming_accident_or_geolocation(tmp_path):
    source = tmp_path / "image.png"
    assert cv2.imwrite(str(source), np.zeros((80, 100, 3), dtype=np.uint8))

    result = analyze_media(
        source, "image", {"lng": 114.363, "lat": 30.5365}, detector=FakeDetector(),
    )

    assert result["model"] == {
        "id": "test-detector", "version": "fixture-v1", "confidence_threshold": 0.4,
        "tracking": {
            "algorithm": "bytetrack",
            "implementation": "FoundationVision/ByteTrack@d1bf019",
            "camera_motion_compensation": "homography_projected_to_segment_anchor",
            "low_confidence_tracking": True,
            "high_confidence_threshold": 0.4,
            "low_confidence_threshold": 0.1,
        },
        "classes": result["model"]["classes"],
        "dataset_source": "VisDrone2019-DET (AISKYEYE team, Tianjin University)",
        "dataset_license": "CC BY-NC-SA 3.0; non-commercial academic research use",
        "weights_distributed_by_application": False,
        "accident_model": None,
        "flood_model": None,
    }
    assert [item["label"] for item in result["preview_detections"]] == ["car"]
    assert result["metrics"]["peak_vehicle_count"] == 1
    assert result["metrics"]["motion_assessment"] == "insufficient_single_frame"
    assert result["safety"]["accident_recognition_supported"] is False
    assert result["safety"]["automatically_changes_routing"] is False
    assert result["location_precision"] == "operator_selected_area_only"


def test_single_image_pipeline_explains_when_no_location_was_supplied(tmp_path):
    source = tmp_path / "image.png"
    assert cv2.imwrite(str(source), np.zeros((80, 100, 3), dtype=np.uint8))

    result = analyze_media(source, "image", None, detector=FakeDetector())

    assert result["anchor_gcj"] is None
    assert result["location_precision"] == "not_provided"
    assert "具体道路" in result["review_guidance"]
    assert "管理员标注" not in result["review_guidance"]


def test_optional_accident_inference_failure_does_not_fail_basic_image_analysis(tmp_path):
    class BrokenAccidentModel:
        model_id = "broken-aider"
        model_version = "fixture"

        def predict(self, _frame):
            raise ValueError("bad optional model output")

    source = tmp_path / "image.png"
    assert cv2.imwrite(str(source), np.zeros((80, 100, 3), dtype=np.uint8))
    region = {"id": "lane", "kind": "vehicle_lane", "polygon": [
        [0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9],
    ]}

    result = analyze_media(
        source, "image", None, detector=FakeDetector(), accident_model=BrokenAccidentModel(),
        regions=[region],
    )

    assert result["metrics"]["peak_vehicle_count"] == 1
    assert result["safety"]["accident_recognition_supported"] is False
    assert result["model"]["accident_model"]["runtime_status"] == "inference_failed"
    assert result["optional_model_errors"] == [{"model": "accident", "error_type": "ValueError"}]


def test_video_checkpoint_keeps_only_the_strongest_flood_outline_per_road():
    observations = [
        {"time_seconds": 1.0, "region_id": "road-a", "flooded_road_area_ratio": 0.2,
         "outline_polygons": [[[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]]]},
        {"time_seconds": 2.0, "region_id": "road-a", "flooded_road_area_ratio": 0.5,
         "outline_polygons": [[[0.3, 0.3], [0.4, 0.3], [0.4, 0.4]]]},
        {"time_seconds": 2.0, "region_id": "road-b", "flooded_road_area_ratio": 0.3,
         "outline_polygons": [[[0.5, 0.5], [0.6, 0.5], [0.6, 0.6]]]},
    ]

    compacted = _compact_specialized_observations(observations)

    assert compacted[0]["outline_polygons"] == []
    assert compacted[1]["outline_polygons"] == observations[1]["outline_polygons"]
    assert compacted[2]["outline_polygons"] == observations[2]["outline_polygons"]
    assert len(compacted) == len(observations)

class FakeCapture:
    def __init__(self, total_frames, fps=10):
        self.total_frames = total_frames
        self.fps = fps
        self.index = 0
        self.released = False

    def isOpened(self):
        return True

    def get(self, prop):
        return {
            FakeCV2.CAP_PROP_FRAME_WIDTH: 100,
            FakeCV2.CAP_PROP_FRAME_HEIGHT: 80,
            FakeCV2.CAP_PROP_FPS: self.fps,
            FakeCV2.CAP_PROP_FRAME_COUNT: self.total_frames,
        }.get(prop, 0)

    def set(self, prop, value):
        if prop == FakeCV2.CAP_PROP_POS_FRAMES:
            self.index = int(value)
            return True
        return False

    def read(self):
        if self.index >= self.total_frames:
            return False, None
        self.index += 1
        return True, np.zeros((80, 100, 3), dtype=np.uint8)

    def release(self):
        self.released = True


class FakeCV2:
    CAP_PROP_POS_FRAMES = 1
    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4
    CAP_PROP_FPS = 5
    CAP_PROP_FRAME_COUNT = 7
    IMREAD_COLOR = 1
    _total_frames = 1900

    @classmethod
    def VideoCapture(cls, _path):
        return FakeCapture(cls._total_frames)


def test_video_pipeline_samples_entire_clip_in_segments_not_just_first_180_seconds(tmp_path, monkeypatch):
    import config

    source = tmp_path / "campus.mp4"
    source.write_bytes(b"fake-video")
    FakeCV2._total_frames = 1900  # 190 seconds at 10 fps
    monkeypatch.setitem(sys.modules, "cv2", FakeCV2)
    monkeypatch.setattr(config, "VISION_SAMPLE_FPS", 5)
    monkeypatch.setattr(config, "VISION_SEGMENT_FRAMES", 180)
    detector = FakeDetector()
    progress = []

    result = analyze_media(
        source, "video", {"lng": 114.363, "lat": 30.5365},
        camera_stabilized=True, detector=detector, progress_callback=progress.append,
    )

    assert result["video_coverage"]["complete"] is True
    assert result["video_coverage"]["duration_seconds"] == 190
    assert result["video_coverage"]["analyzed_through_seconds"] >= 189
    assert result["video_coverage"]["sampled_frames"] == 950
    assert result["video_coverage"]["segment_count"] == 6
    assert detector.calls == 950
    assert progress[-1]["checkpoint"]["next_frame_index"] == 1900


def test_specialized_video_evidence_is_merged_across_processing_segments(tmp_path, monkeypatch):
    import config

    class AccidentModel:
        model_id = "fake-aider"
        model_version = "fixture"
        threshold = 0.85

        def predict(self, _frame):
            return {"traffic_accident_probability": 0.96, "model_id": self.model_id}

    source = tmp_path / "short.mp4"
    source.write_bytes(b"fake-video")
    FakeCV2._total_frames = 80
    monkeypatch.setitem(sys.modules, "cv2", FakeCV2)
    monkeypatch.setattr(config, "VISION_SAMPLE_FPS", 5)
    monkeypatch.setattr(config, "VISION_SEGMENT_FRAMES", 5)

    result = analyze_media(
        source, "video", None, camera_stabilized=True, detector=FakeDetector(),
        accident_model=AccidentModel(),
        regions=[{"id": "lane", "kind": "vehicle_lane",
                  "polygon": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]}],
    )

    candidate = next(item for item in result["candidates"] if item["kind"] == "possible_accident")
    assert candidate["evidence"]["summary"]["supporting_frames"] >= 2
    assert result["metrics"]["specialized_frames_analyzed"] >= 2
    assert result["safety"]["accident_recognition_supported"] is True
    assert candidate["review_required"] is True
    assert candidate["auto_publish"] is False


def test_optional_accident_failure_is_isolated_and_stopped_for_rest_of_video(tmp_path, monkeypatch):
    import config

    class BrokenAccidentModel:
        model_id = "broken-aider"
        model_version = "fixture"

        def __init__(self):
            self.calls = 0

        def predict(self, _frame):
            self.calls += 1
            raise ValueError("incompatible runtime output")

    source = tmp_path / "short.mp4"
    source.write_bytes(b"fake-video")
    FakeCV2._total_frames = 80
    monkeypatch.setitem(sys.modules, "cv2", FakeCV2)
    monkeypatch.setattr(config, "VISION_SAMPLE_FPS", 5)
    monkeypatch.setattr(config, "VISION_SEGMENT_FRAMES", 5)
    accident_model = BrokenAccidentModel()
    region = {"id": "lane", "kind": "vehicle_lane", "polygon": [
        [0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9],
    ]}

    result = analyze_media(
        source, "video", None, camera_stabilized=True, detector=FakeDetector(),
        accident_model=accident_model, regions=[region],
    )

    assert result["video_coverage"]["complete"] is True
    assert result["metrics"]["frames_analyzed"] > 0
    assert accident_model.calls == 1
    assert result["safety"]["accident_recognition_supported"] is False
    assert result["optional_model_errors"] == [{"model": "accident", "error_type": "ValueError"}]


def test_interrupted_video_resumes_after_last_completed_segment(tmp_path, monkeypatch):
    import config

    source = tmp_path / "campus.mp4"
    source.write_bytes(b"fake-video")
    FakeCV2._total_frames = 1000  # 100 seconds at 10 fps
    monkeypatch.setitem(sys.modules, "cv2", FakeCV2)
    monkeypatch.setattr(config, "VISION_SAMPLE_FPS", 5)
    monkeypatch.setattr(config, "VISION_SEGMENT_FRAMES", 50)
    checkpoint = {}

    class StopAfterFirstSegment(RuntimeError):
        pass

    def interrupt(payload):
        checkpoint.update(payload["checkpoint"])
        raise StopAfterFirstSegment

    with pytest.raises(StopAfterFirstSegment):
        analyze_media(
            source, "video", {"lng": 114.363, "lat": 30.5365},
            camera_stabilized=True, detector=FakeDetector(), progress_callback=interrupt,
        )
    calls_before_resume = 0
    detector = FakeDetector()
    original_detect = detector.detect

    def count_detect(frame, *, tracking=False):
        nonlocal calls_before_resume
        calls_before_resume += 1
        return original_detect(frame, tracking=tracking)

    detector.detect = count_detect
    result = analyze_media(
        source, "video", {"lng": 114.363, "lat": 30.5365},
        camera_stabilized=True, detector=detector, resume_state=checkpoint,
    )

    assert checkpoint["next_frame_index"] == 99
    assert result["video_coverage"]["sampled_frames"] == 500
    assert calls_before_resume == 450
