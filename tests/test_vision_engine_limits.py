import pytest

from vision.engine import _validate_frame_dimensions


def test_frame_dimensions_reject_invalid_or_excessive_pixel_counts(monkeypatch):
    import config

    monkeypatch.setattr(config, "VISION_MAX_FRAME_PIXELS", 1_000_000)
    with pytest.raises(ValueError, match="分辨率无效"):
        _validate_frame_dimensions(0, 400)
    with pytest.raises(ValueError, match="像素上限"):
        _validate_frame_dimensions(1200, 1000)
    _validate_frame_dimensions(1000, 1000)
