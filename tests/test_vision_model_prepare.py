from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts.vision.prepare_model import _verify_standalone_onnx


def _model(initializers=()):
    return SimpleNamespace(
        graph=SimpleNamespace(initializer=list(initializers), sparse_initializer=[]),
    )


def test_model_preparer_accepts_and_hashes_self_contained_onnx(tmp_path, monkeypatch):
    model_path = tmp_path / "model.onnx"
    model_path.write_bytes(b"validated-model")
    checker = Mock()
    onnx = SimpleNamespace(
        load=Mock(return_value=_model()),
        TensorProto=SimpleNamespace(EXTERNAL=1),
        checker=SimpleNamespace(check_model=checker),
    )
    monkeypatch.setitem(sys.modules, "onnx", onnx)

    from scripts.vision.prepare_model import _sha256
    assert _verify_standalone_onnx(model_path) == _sha256(model_path)
    onnx.load.assert_called_once_with(str(model_path), load_external_data=False)
    checker.assert_called_once()


def test_model_preparer_rejects_external_onnx_weights(tmp_path, monkeypatch):
    initializer = SimpleNamespace(
        name="external-weight", data_location=1, external_data=[object()],
    )
    onnx = SimpleNamespace(
        load=Mock(return_value=_model([initializer])),
        TensorProto=SimpleNamespace(EXTERNAL=1),
        checker=SimpleNamespace(check_model=Mock()),
    )
    monkeypatch.setitem(sys.modules, "onnx", onnx)

    with pytest.raises(RuntimeError, match="外部权重文件"):
        _verify_standalone_onnx(tmp_path / "model.onnx")
    onnx.checker.check_model.assert_not_called()
