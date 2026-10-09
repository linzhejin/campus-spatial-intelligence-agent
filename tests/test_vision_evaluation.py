import pytest

from scripts.vision.evaluate_visdrone import _load_model_manifest, _sha256
from vision.evaluation import evaluate_visdrone_frame


def test_visdrone_evaluator_reports_pedestrian_and_vehicle_errors_separately():
    annotations = "\n".join([
        "0,0,10,10,1,1,0,0",       # pedestrian
        "20,20,10,10,1,4,0,0",    # car
        "40,40,12,12,1,11,0,0",   # ignored/other region
    ])
    predictions = [
        {"label": "pedestrian", "confidence": 0.9, "box": [0, 0, 10, 10]},
        {"label": "car", "confidence": 0.8, "box": [20, 20, 30, 30]},
        {"label": "bus", "confidence": 0.7, "box": [40, 40, 52, 52]},
        {"label": "car", "confidence": 0.6, "box": [70, 70, 80, 80]},
    ]

    result = evaluate_visdrone_frame(predictions, annotations)

    assert result["pedestrian"] == {"tp": 1, "fp": 0, "fn": 0, "ignored": 0}
    assert result["vehicle"] == {"tp": 1, "fp": 1, "fn": 0, "ignored": 1}


def test_visdrone_evaluator_counts_unmatched_objects_as_false_negatives():
    result = evaluate_visdrone_frame([], "2,2,8,8,1,4,0,0")

    assert result["vehicle"] == {"tp": 0, "fp": 0, "fn": 1, "ignored": 0}


def test_visdrone_evaluator_rejects_malformed_annotation_rows():
    with pytest.raises(ValueError, match="annotation"):
        evaluate_visdrone_frame([], "1,2,3")


def test_evaluation_manifest_records_model_revision_and_verifies_onnx_hash(tmp_path):
    import json

    model = tmp_path / "model.onnx"
    model.write_bytes(b"test model bytes")
    (tmp_path / "manifest.json").write_text(json.dumps({
        "model_id": "test-detector",
        "model_revision": "abc123",
        "onnx_sha256": _sha256(model),
    }), encoding="utf-8")

    manifest, manifest_sha = _load_model_manifest(model)

    assert manifest["model_revision"] == "abc123"
    assert manifest_sha == _sha256(tmp_path / "manifest.json")


def test_evaluation_manifest_rejects_model_hash_mismatch(tmp_path):
    import json

    model = tmp_path / "model.onnx"
    model.write_bytes(b"test model bytes")
    (tmp_path / "manifest.json").write_text(json.dumps({
        "onnx_sha256": "0" * 64,
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="SHA256"):
        _load_model_manifest(model)
