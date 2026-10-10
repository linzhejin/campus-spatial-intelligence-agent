import pytest

try:
    import torch
except Exception as error:  # pragma: no cover - depends on the developer's isolated training runtime
    pytest.skip(
        f"FloodNet model tests require the isolated PyTorch environment: {type(error).__name__}",
        allow_module_level=True,
    )

from vision.floodnet_training import build_floodnet_model, export_floodnet_onnx


def test_floodnet_mobilenet_unet_returns_ten_class_full_resolution_logits():
    model = build_floodnet_model(pretrained=False)
    model.eval()

    with torch.inference_mode():
        logits = model(torch.zeros((1, 3, 64, 64), dtype=torch.float32))

    assert tuple(logits.shape) == (1, 10, 64, 64)
    assert torch.isfinite(logits).all()


def test_visible_water_unet_returns_two_class_full_resolution_logits():
    model = build_floodnet_model(num_classes=2, pretrained=False)
    model.eval()

    with torch.inference_mode():
        logits = model(torch.zeros((1, 3, 64, 64), dtype=torch.float32))

    assert tuple(logits.shape) == (1, 2, 64, 64)
    assert torch.isfinite(logits).all()


def test_exported_floodnet_onnx_has_fixed_contract_and_cpu_parity(tmp_path):
    pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    model = torch.nn.Conv2d(3, 10, kernel_size=1)
    report = export_floodnet_onnx(model, tmp_path / "model.onnx", input_size=32)

    assert report["input_shape"] == [1, 3, 32, 32]
    assert report["output_shape"] == [1, 10, 32, 32]
    assert report["max_absolute_error"] < 1e-4


def test_exported_binary_water_onnx_has_two_class_contract(tmp_path):
    pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    model = torch.nn.Conv2d(3, 2, kernel_size=1)
    report = export_floodnet_onnx(model, tmp_path / "binary-water.onnx", input_size=32)

    assert report["output_shape"] == [1, 2, 32, 32]
    assert report["max_absolute_error"] < 1e-4
