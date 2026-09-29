"""Optional detector adapter and bounded image/video analysis worker."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import config
from vision.analysis import analyze_observations


class VisionConfigurationError(RuntimeError):
    pass


def _validate_frame_dimensions(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise ValueError("影像分辨率无效")
    if width * height > config.VISION_MAX_FRAME_PIXELS:
        raise ValueError(
            f"影像分辨率超过 {config.VISION_MAX_FRAME_PIXELS:,} 像素上限，请先裁剪或降低分辨率。"
        )


class UltralyticsDetector:
    """Loads an explicitly configured local checkpoint; never downloads weights."""

    def __init__(self, model_path: str):
        if not model_path or not Path(model_path).is_file():
            raise VisionConfigurationError("VISION_MODEL_PATH 未配置或权重文件不存在")
        try:
            from ultralytics import YOLO
        except ImportError as error:
            raise VisionConfigurationError(
                "视觉依赖未安装，请在独立视觉工作进程中安装 requirements-vision.txt"
            ) from error
        self.model = YOLO(model_path)

    def detect(self, frame, *, tracking: bool = False) -> list[dict[str, Any]]:
        options = {"source": frame, "verbose": False, "conf": 0.25}
        if tracking:
            results = self.model.track(
                frame, persist=True, tracker=os.getenv("VISION_TRACKER", "botsort.yaml"),
                verbose=False, conf=0.25,
            )
        else:
            results = self.model.predict(**options)
        if not results:
            return []
        result = results[0]
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return []
        xyxy = boxes.xyxy.cpu().tolist()
        classes = boxes.cls.cpu().tolist()
        confidences = boxes.conf.cpu().tolist()
        raw_ids = getattr(boxes, "id", None)
        track_ids = raw_ids.cpu().tolist() if raw_ids is not None else [None] * len(xyxy)
        names = getattr(result, "names", getattr(self.model, "names", {}))
        detections = []
        for box, class_id, confidence, track_id in zip(xyxy, classes, confidences, track_ids):
            label = names.get(int(class_id), str(int(class_id))) if isinstance(names, dict) else names[int(class_id)]
            detections.append({
                "label": str(label).strip().lower(),
                "box": [float(value) for value in box],
                "confidence": float(confidence),
                "track_id": int(track_id) if track_id is not None else None,
            })
        return detections


def analyze_media(media_path: str | Path, media_kind: str, anchor_gcj: dict,
                  camera_stabilized: bool = False,
                  *, detector=None) -> dict:
    """Analyze a manager-uploaded item and return review-only spatial evidence."""
    path = Path(media_path)
    if not path.is_file():
        raise FileNotFoundError("巡检影像文件已丢失")
    detector = detector or UltralyticsDetector(config.VISION_MODEL_PATH)
    started = time.monotonic()
    try:
        import cv2
    except ImportError as error:
        raise VisionConfigurationError("视觉依赖未安装，请安装 requirements-vision.txt") from error

    frames: list[list[dict]] = []
    width = height = 0
    sample_interval = 1.0
    if media_kind == "image":
        try:
            from PIL import Image
        except ImportError as error:
            raise VisionConfigurationError("缺少图片尺寸校验依赖，请安装 requirements-vision.txt") from error
        try:
            with Image.open(path) as image:
                _validate_frame_dimensions(*image.size)
        except OSError as error:
            raise ValueError("图片无法读取或分辨率无效") from error
        frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("图片无法解码，请上传有效的 JPG、PNG 或 WebP 文件")
        height, width = frame.shape[:2]
        _validate_frame_dimensions(width, height)
        frames.append(detector.detect(frame, tracking=False))
    elif media_kind == "video":
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            raise ValueError("视频无法解码，请上传有效的视频文件")
        try:
            _validate_frame_dimensions(
                int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            )
        except Exception:
            capture.release()
            raise
        fps = capture.get(cv2.CAP_PROP_FPS)
        fps = fps if fps and fps > 0 else 1.0
        stride = max(1, round(fps * sample_interval))
        frame_index = 0
        sampled = 0
        try:
            while sampled < config.VISION_MAX_VIDEO_FRAMES:
                ok, frame = capture.read()
                if not ok:
                    break
                if frame_index % stride == 0:
                    height, width = frame.shape[:2]
                    _validate_frame_dimensions(width, height)
                    frames.append(detector.detect(frame, tracking=True))
                    sampled += 1
                    if time.monotonic() - started > config.VISION_MAX_ANALYSIS_SECONDS:
                        raise TimeoutError("影像分析超过配置时限，请缩短视频后重试")
                frame_index += 1
        finally:
            capture.release()
        if not frames:
            raise ValueError("视频中没有可读取的画面")
    else:
        raise ValueError("不支持的影像类型")

    if time.monotonic() - started > config.VISION_MAX_ANALYSIS_SECONDS:
        raise TimeoutError("影像分析超过配置时限，请缩短视频后重试")
    analysis = analyze_observations(
        frames, frame_width=width, frame_height=height,
        sample_interval_s=sample_interval,
        camera_stabilized=camera_stabilized,
    )
    return {
        **analysis,
        "media": {"kind": media_kind, "width": width, "height": height},
        "anchor_gcj": anchor_gcj,
        "location_precision": "operator_selected_area_only",
        "review_guidance": "图像位置仅代表管理员标注的观察区域，不是检测框的地面坐标；确认管制前须人工选取具体道路段。",
    }
