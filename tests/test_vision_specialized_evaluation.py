import pytest
import numpy as np

from scripts.vision.evaluate_specialized import _align_rgb_to_mask_grid

from vision.specialized_evaluation import (
    summarize_binary_classification,
    summarize_binary_counts,
    summarize_classification,
    validate_split_manifest,
)


def test_floodnet_evaluation_resizes_rgb_to_mask_grid_and_keeps_label_ids():
    frame = np.zeros((4, 8, 3), dtype=np.uint8)
    labels = np.full((6, 8), 3, dtype=np.uint8)
    original_labels = labels.copy()

    aligned, alignment = _align_rgb_to_mask_grid(frame, labels)

    assert aligned.shape == (6, 8, 3)
    assert alignment == "image_resized_to_mask_grid"
    assert np.array_equal(labels, original_labels)


def test_split_manifest_requires_scene_disjoint_groups_and_test_samples():
    manifest = {
        "schema_version": 1,
        "task": "accident_classification",
        "dataset": "AIDER local copy",
        "class_order": ["collapsed_building", "fire", "flooded_areas", "normal", "traffic_incident"],
        "split_unit": "incident_scene",
        "split_groups": {
            "train": ["incident-1"],
            "validation": ["incident-2"],
            "test": ["incident-3"],
        },
        "samples": [{"image": "test/image.jpg", "group_id": "incident-3",
                     "label": "traffic_incident"}],
    }

    assert validate_split_manifest(manifest, task="accident_classification")["split_groups"] == manifest["split_groups"]

    manifest["split_groups"]["train"].append("incident-3")
    with pytest.raises(ValueError, match="groups must not overlap"):
        validate_split_manifest(manifest, task="accident_classification")


def test_test_samples_cannot_belong_to_training_or_validation_group():
    manifest = {
        "schema_version": 1,
        "task": "accident_classification",
        "dataset": "AIDER",
        "class_order": ["collapsed_building", "fire", "flooded_areas", "normal", "traffic_incident"],
        "split_unit": "incident_scene",
        "split_groups": {"train": ["scene-a"], "validation": [], "test": ["scene-b"]},
        "samples": [{"image": "bad.jpg", "group_id": "scene-a", "label": "normal"}],
    }

    with pytest.raises(ValueError, match="test split"):
        validate_split_manifest(manifest, task="accident_classification")


def test_classification_report_contains_per_class_and_accident_binary_metrics():
    report = summarize_classification(
        ["normal", "traffic_incident", "traffic_incident", "fire"],
        ["normal", "traffic_incident", "normal", "fire"],
        classes=("fire", "normal", "traffic_incident"),
        accident_class="traffic_incident",
    )

    assert report["accuracy"] == pytest.approx(0.75)
    assert report["per_class"]["traffic_incident"]["recall"] == pytest.approx(0.5)
    assert report["traffic_incident_binary"] == {
        "tp": 1, "fp": 0, "fn": 1, "tn": 2,
        "precision": 1.0, "recall": 0.5, "f1": pytest.approx(2 / 3),
    }


def test_binary_segmentation_summary_reports_iou_precision_recall_and_empty_union():
    assert summarize_binary_counts(tp=3, fp=1, fn=2) == {
        "tp": 3, "fp": 1, "fn": 2,
        "iou": pytest.approx(0.5), "precision": pytest.approx(0.75),
        "recall": pytest.approx(0.6), "f1": pytest.approx(2 / 3),
    }
    assert summarize_binary_counts(tp=0, fp=0, fn=0)["iou"] is None


def test_incident_operating_point_uses_explicit_probability_threshold():
    report = summarize_binary_classification(
        [True, True, False, False], [True, False, True, False],
    )
    assert report == {
        "tp": 1, "fp": 1, "fn": 1, "tn": 1,
        "specificity": 0.5, "precision": 0.5, "recall": 0.5, "f1": 0.5,
    }
