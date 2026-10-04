import cv2
import numpy as np

from vision.engine import analyze_media


class FakeDetector:
    model_id = "test-detector"
    model_version = "fixture-v1"
    confidence_threshold = 0.4

    def detect(self, _frame, *, tracking=False):
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
