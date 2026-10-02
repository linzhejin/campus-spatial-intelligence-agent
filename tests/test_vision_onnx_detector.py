from types import SimpleNamespace

import numpy as np
import pytest

from vision.engine import OnnxDetector, VisionConfigurationError


class FakeSession:
    def __init__(self, outputs=None, input_shape=(1, 3, 960, 960)):
        self.outputs = outputs or [
            np.asarray([[3, 5]]),
            np.asarray([[[240, 120, 720, 600], [-10, 200, 40, 300]]], dtype=np.float32),
            np.asarray([[0.9, 0.1]], dtype=np.float32),
        ]
        self.feed = None
        self.input_shape = input_shape

    def get_inputs(self):
        return [
            SimpleNamespace(name="images", shape=self.input_shape),
            SimpleNamespace(name="orig_target_sizes", shape=(1, 2)),
        ]

    def get_outputs(self):
        return [
            SimpleNamespace(name="labels"),
            SimpleNamespace(name="boxes"),
            SimpleNamespace(name="scores"),
        ]

    def run(self, output_names, input_feed):
        self.feed = input_feed
        return self.outputs


def test_onnx_detector_preprocesses_rgb_letterbox_and_restores_boxes():
    session = FakeSession()
    detector = OnnxDetector("model.onnx", session=session, confidence_threshold=0.25)
    # OpenCV camera frames are BGR. The blue source pixel moves to RGB channel 2.
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[0, 0] = [255, 0, 0]

    detections = detector.detect(frame)

    assert len(detections) == 1
    assert detections[0]["label"] == "car"
    assert detections[0]["confidence"] == pytest.approx(0.9)
    assert detections[0]["box"] == pytest.approx([160, 0, 480, 320])
    assert session.feed["images"].shape == (1, 3, 960, 960)
    assert session.feed["images"][0, 0, 120, 0] == pytest.approx(0.0)
    assert session.feed["images"][0, 2, 120, 0] == pytest.approx(1.0)
    assert session.feed["orig_target_sizes"].tolist() == [[960, 960]]


def test_onnx_detector_returns_empty_for_empty_valid_outputs():
    session = FakeSession(outputs=[
        np.empty((1, 0), dtype=np.int64),
        np.empty((1, 0, 4), dtype=np.float32),
        np.empty((1, 0), dtype=np.float32),
    ])
    assert OnnxDetector("model.onnx", session=session).detect(np.zeros((100, 100, 3), dtype=np.uint8)) == []


def test_onnx_detector_rejects_model_with_unexpected_signature():
    session = FakeSession(input_shape=(1, 3, "height", "width"))
    with pytest.raises(VisionConfigurationError, match="静态方形输入"):
        OnnxDetector("model.onnx", session=session)


def test_onnx_detector_rejects_inconsistent_model_output_shapes():
    session = FakeSession(outputs=[
        np.asarray([[3, 5]]),
        np.asarray([[[240, 120, 720, 600]]], dtype=np.float32),
        np.asarray([[0.9, 0.1]], dtype=np.float32),
    ])
    detector = OnnxDetector("model.onnx", session=session)
    with pytest.raises(VisionConfigurationError, match="输出维度"):
        detector.detect(np.zeros((480, 640, 3), dtype=np.uint8))
