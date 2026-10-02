import cv2
import numpy as np
import pytest

from vision.engine import _estimate_camera_transform, _gray_for_motion


def test_background_motion_estimator_recovers_a_known_image_translation():
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    random = np.random.default_rng(127)
    for _ in range(180):
        x = int(random.integers(20, 620))
        y = int(random.integers(20, 460))
        radius = int(random.integers(3, 14))
        color = tuple(int(value) for value in random.integers(90, 255, size=3))
        cv2.circle(image, (x, y), radius, color, -1)
    matrix = np.asarray([[1, 0, 13], [0, 1, -7]], dtype=np.float32)
    moved = cv2.warpAffine(image, matrix, (640, 480))
    previous_gray, scale = _gray_for_motion(image, cv2)
    current_gray, current_scale = _gray_for_motion(moved, cv2)

    estimated = _estimate_camera_transform(previous_gray, current_gray, cv2, current_scale)

    assert scale == current_scale == 1.0
    assert estimated is not None
    assert estimated[0][2] == pytest.approx(13, abs=2)
    assert estimated[1][2] == pytest.approx(-7, abs=2)
