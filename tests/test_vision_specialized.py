from types import SimpleNamespace

import numpy as np
import pytest

from vision.specialized import OnnxAiderClassifier, OnnxFloodSegmenter
from vision.analysis import build_specialized_candidates


class FakeSession:
    def __init__(self, output, input_shape=(1, 3, 224, 224)):
        self.output = output
        self.input_shape = input_shape
        self.feed = None
        self.calls = 0

    def get_inputs(self):
        return [SimpleNamespace(name="images", shape=self.input_shape)]

    def get_outputs(self):
        return [SimpleNamespace(name="output", shape=list(self.output.shape))]

    def run(self, _names, feed):
        self.feed = feed
        self.calls += 1
        return [self.output]


def test_aider_classifier_maps_traffic_accident_score_without_claiming_location():
    session = FakeSession(np.asarray([[0.0, 0.0, 0.0, 0.0, 4.0]], dtype=np.float32))
    classifier = OnnxAiderClassifier("model.onnx", session=session)

    prediction = classifier.predict(np.zeros((80, 120, 3), dtype=np.uint8))

    assert prediction["class"] == "traffic_incident"
    assert prediction["traffic_accident_probability"] > 0.9
    assert "location" not in prediction
    assert session.feed["images"].shape == (1, 3, 224, 224)


def test_aider_classifier_rejects_unexpected_output_class_count():
    with pytest.raises(ValueError, match="为 5"):
        OnnxAiderClassifier(
            "model.onnx", session=FakeSession(np.asarray([[1.0, 2.0, 3.0]], dtype=np.float32)),
        )


def test_aider_classifier_accepts_dynamic_batch_and_preserves_probability_output():
    scores = np.asarray([[0.01, 0.02, 0.03, 0.04, 0.90]], dtype=np.float32)
    session = FakeSession(scores, input_shape=("batch", 3, 224, 224))
    classifier = OnnxAiderClassifier("model.onnx", session=session)

    prediction = classifier.predict(np.zeros((80, 120, 3), dtype=np.uint8))

    assert prediction["traffic_accident_probability"] == pytest.approx(0.9, abs=1e-5)


def test_flood_segmenter_measures_flooded_road_only_inside_marked_roi():
    labels = np.full((1, 10, 8, 8), -4.0, dtype=np.float32)
    labels[:, 3, :, :] = 4.0  # Flooded road class.
    session = FakeSession(labels, input_shape=(1, 3, 8, 8))
    segmenter = OnnxFloodSegmenter("flood.onnx", session=session)
    frame = np.zeros((80, 80, 3), dtype=np.uint8)
    region = {"id": "east-road", "kind": "road_surface", "polygon": [
        [0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75],
    ]}

    frame[:20, :] = 0
    frame[20:60, 20:60] = 255
    prediction = segmenter.predict(frame, region, include_mask=True)

    assert prediction["region_id"] == "east-road"
    assert prediction["flooded_road_area_ratio"] == pytest.approx(1.0)
    assert prediction["outline_polygons"]
    assert prediction["flooded_road_mask"].shape == (80, 80)
    assert prediction["flooded_road_mask"][:20].sum() == 0
    assert prediction["flooded_road_mask"].sum() == 1600
    assert session.calls > 1  # The 40x40 road region is analyzed as overlapping 8x8 tiles.
    assert session.feed["images"].mean() > 1.0  # The model receives marked road tiles, not the whole scene.


def test_flood_segmenter_calibrated_probability_threshold_returns_score_map():
    logits = np.full((1, 10, 8, 8), -8.0, dtype=np.float32)
    logits[:, 0, :, :] = 0.0
    logits[0, 3, 1, 1] = 3.0
    logits[0, 3, 2, 2] = 1.0
    session = FakeSession(logits, input_shape=(1, 3, 8, 8))
    segmenter = OnnxFloodSegmenter(
        "flood.onnx", session=session, flood_probability_threshold=0.9,
    )
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    region = {"kind": "road_surface", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]]}

    prediction = segmenter.predict(frame, region, include_mask=True, include_probabilities=True)

    assert prediction["flooded_road_mask"].sum() == 1
    assert prediction["flooded_road_mask"][1, 1] == 1
    assert prediction["flooded_road_mask"][2, 2] == 0
    assert prediction["flooded_road_probability"].shape == (8, 8)
    assert prediction["flooded_road_probability"][1, 1] > 0.9
    assert prediction["flooded_road_probability"][2, 2] < 0.9


def test_flood_segmenter_rejects_invalid_probability_threshold():
    with pytest.raises(ValueError):
        OnnxFloodSegmenter("flood.onnx", session=FakeSession(
            np.zeros((1, 10, 8, 8), dtype=np.float32), input_shape=(1, 3, 8, 8),
        ), flood_probability_threshold=1.1)


def test_specialized_candidates_require_persistent_evidence_and_never_auto_publish():
    sparse = [
        {"time_seconds": 1.0, "traffic_accident_probability": 0.91,
         "flooded_road_area_ratio": 0.3, "region_id": "road"},
    ]
    assert build_specialized_candidates(sparse, sample_interval_s=1.0) == []

    persistent = [
        {"time_seconds": 1.0, "traffic_accident_probability": 0.91,
         "flooded_road_area_ratio": 0.3, "region_id": "road",
         "outline_polygons": [[[0.2, 0.3], [0.4, 0.3], [0.4, 0.5]]]},
        {"time_seconds": 2.0, "traffic_accident_probability": 0.88,
         "flooded_road_area_ratio": 0.26, "region_id": "road",
         "outline_polygons": [[[0.2, 0.3], [0.4, 0.3], [0.4, 0.5]]]},
    ]
    candidates = build_specialized_candidates(persistent, sample_interval_s=1.0)

    by_kind = {item["kind"]: item for item in candidates}
    assert {"possible_accident", "possible_flooding"} <= set(by_kind)
    assert all(item["review_required"] and not item["auto_publish"] for item in candidates)
    assert by_kind["possible_flooding"]["evidence"]["summary"]["region_id"] == "road"
    assert by_kind["possible_flooding"]["evidence"]["summary"]["outline_polygons"]


def test_single_frame_flood_reflection_does_not_create_a_video_candidate():
    candidate = [{"time_seconds": 0.0, "flooded_road_area_ratio": 0.7,
                  "region_id": "road", "outline_polygons": []}]
    assert build_specialized_candidates(candidate, sample_interval_s=1.0) == []


def test_persistence_cannot_be_fabricated_by_switching_to_a_different_road_region():
    observations = [
        {"time_seconds": 1.0, "traffic_accident_probability": 0.95,
         "flooded_road_area_ratio": 0.3, "region_id": "north-road"},
        {"time_seconds": 2.0, "traffic_accident_probability": 0.96,
         "flooded_road_area_ratio": 0.31, "region_id": "south-road"},
    ]

    assert build_specialized_candidates(observations, sample_interval_s=1.0) == []
