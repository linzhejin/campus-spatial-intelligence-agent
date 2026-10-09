import sys

import cv2
import numpy as np
import pytest

from vision.engine import analyze_media


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


def test_single_image_pipeline_returns_vehicle_boxes_without_claiming_accident_or_geolocation(tmp_path):
    source = tmp_path / "image.png"
    assert cv2.imwrite(str(source), np.zeros((80, 100, 3), dtype=np.uint8))

    result = analyze_media(
        source, "image", {"lng": 114.363, "lat": 30.5365}, detector=FakeDetector(),
    )

    assert result["model"] == {
        "id": "test-detector", "version": "fixture-v1", "confidence_threshold": 0.4,
        "classes": result["model"]["classes"],
        "dataset_source": "VisDrone2019-DET (AISKYEYE team, Tianjin University)",
        "dataset_license": "CC BY-NC-SA 3.0; non-commercial academic research use",
        "weights_distributed_by_application": False,
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
