"""CPU-only aerial image/video inference and bounded, review-only analysis."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import config
from vision.analysis import VEHICLE_LABELS, analyze_observations


VISDRONE_CLASSES = (
    "pedestrian", "people", "bicycle", "car", "van", "truck",
    "tricycle", "awning-tricycle", "bus", "motor", "others",
)
MAX_PREVIEW_DETECTIONS = 250


class VisionConfigurationError(RuntimeError):
    """The optional vision runtime or its model is not usable."""


def _validate_frame_dimensions(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise ValueError("影像分辨率无效")
    if width * height > config.VISION_MAX_FRAME_PIXELS:
        raise ValueError(
            f"影像分辨率超过 {config.VISION_MAX_FRAME_PIXELS:,} 像素上限，请先裁剪或降低分辨率。"
        )


class OnnxDetector:
    """Loads a local, fixed-shape RT-DETRv4 ONNX model using CPU execution only."""

    def __init__(self, model_path: str, *, session=None,
                 confidence_threshold: float | None = None,
                 model_id: str | None = None, model_version: str | None = None):
        if session is None and (not model_path or not Path(model_path).is_file()):
            raise VisionConfigurationError("VISION_MODEL_PATH 未配置或 ONNX 权重文件不存在")
        if session is None:
            try:
                import onnxruntime as ort
            except ImportError as error:
                raise VisionConfigurationError(
                    "缺少 onnxruntime，请在独立视觉工作进程中安装 requirements-vision.txt"
                ) from error
            options = ort.SessionOptions()
            options.intra_op_num_threads = max(1, int(os.getenv("VISION_CPU_THREADS", "1")))
            options.inter_op_num_threads = 1
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            try:
                session = ort.InferenceSession(
                    str(model_path), sess_options=options, providers=["CPUExecutionProvider"],
                )
            except Exception as error:
                raise VisionConfigurationError(f"ONNX 模型加载失败：{error}") from error

        self.session = session
        self.model_path = str(model_path or "injected-session")
        self.model_id = model_id or os.getenv("VISION_MODEL_ID", "visdrone-rtdetrv4-s")
        self.model_version = model_version or os.getenv("VISION_MODEL_REVISION", "unspecified")
        self.confidence_threshold = (
            float(confidence_threshold) if confidence_threshold is not None
            else float(os.getenv("VISION_CONFIDENCE_THRESHOLD", "0.369"))
        )
        if not 0.0 <= self.confidence_threshold <= 1.0:
            raise VisionConfigurationError("VISION_CONFIDENCE_THRESHOLD 必须介于 0 和 1 之间")

        try:
            inputs = {item.name: item for item in session.get_inputs()}
            outputs = {item.name for item in session.get_outputs()}
            image_shape = inputs["images"].shape
            inputs["orig_target_sizes"]
        except (AttributeError, KeyError, TypeError) as error:
            raise VisionConfigurationError("ONNX 模型必须提供 images 与 orig_target_sizes 输入") from error
        if (
            len(image_shape) != 4 or image_shape[1] != 3
            or not isinstance(image_shape[2], int) or not isinstance(image_shape[3], int)
            or image_shape[2] <= 0 or image_shape[2] != image_shape[3]
        ):
            raise VisionConfigurationError("ONNX 模型必须使用静态方形输入，例如 [N, 3, 960, 960]")
        if not {"labels", "boxes", "scores"}.issubset(outputs):
            raise VisionConfigurationError("ONNX 模型输出必须包含 labels、boxes 和 scores")
        self.input_size = image_shape[2]

    @staticmethod
    def _squeeze_batch(value, expected_rank: int):
        import numpy as np

        array = np.asarray(value)
        if array.ndim == expected_rank + 1 and array.shape[0] == 1:
            array = array[0]
        if array.ndim != expected_rank:
            raise VisionConfigurationError("ONNX 模型输出维度无效")
        return array

    def detect(self, frame, *, tracking: bool = False) -> list[dict[str, Any]]:
        """Return original-frame pixel boxes and normalized VisDrone labels."""
        del tracking  # Track association is performed after optional camera-motion compensation.
        import cv2
        import numpy as np

        if frame is None or not hasattr(frame, "shape") or len(frame.shape) != 3 or frame.shape[2] != 3:
            raise ValueError("待识别画面必须是三通道彩色图像")
        height, width = frame.shape[:2]
        _validate_frame_dimensions(width, height)
        ratio = min(self.input_size / width, self.input_size / height)
        resized_width = max(1, round(width * ratio))
        resized_height = max(1, round(height * ratio))
        resized = cv2.resize(frame, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        pad_x = (self.input_size - resized_width) // 2
        pad_y = (self.input_size - resized_height) // 2
        padded = np.zeros((self.input_size, self.input_size, 3), dtype=np.uint8)
        padded[pad_y:pad_y + resized_height, pad_x:pad_x + resized_width] = rgb
        tensor = np.transpose(padded.astype(np.float32) / 255.0, (2, 0, 1))[None, ...]
        target_size = np.asarray([[self.input_size, self.input_size]], dtype=np.int64)
        try:
            raw = self.session.run(
                ["labels", "boxes", "scores"],
                {"images": tensor, "orig_target_sizes": target_size},
            )
        except Exception as error:
            raise VisionConfigurationError(f"ONNX 推理失败：{error}") from error
        if not isinstance(raw, (tuple, list)) or len(raw) != 3:
            raise VisionConfigurationError("ONNX 模型返回了不支持的结果")
        labels = self._squeeze_batch(raw[0], 1)
        boxes = self._squeeze_batch(raw[1], 2)
        scores = self._squeeze_batch(raw[2], 1)
        if boxes.shape[-1] != 4 or len(labels) != len(boxes) or len(scores) != len(boxes):
            raise VisionConfigurationError("ONNX 模型输出维度不一致")

        detections = []
        for class_id, box, confidence in zip(labels, boxes, scores):
            if not np.isfinite(class_id) or not np.isfinite(confidence):
                continue
            class_index = int(class_id)
            confidence = float(confidence)
            if class_index < 0 or class_index >= len(VISDRONE_CLASSES) or confidence < self.confidence_threshold:
                continue
            if not np.isfinite(box).all():
                continue
            x1, y1, x2, y2 = map(float, box)
            x1 = min(float(width), max(0.0, (x1 - pad_x) / ratio))
            y1 = min(float(height), max(0.0, (y1 - pad_y) / ratio))
            x2 = min(float(width), max(0.0, (x2 - pad_x) / ratio))
            y2 = min(float(height), max(0.0, (y2 - pad_y) / ratio))
            if x2 <= x1 or y2 <= y1:
                continue
            detections.append({
                "label": VISDRONE_CLASSES[class_index],
                "box": [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)],
                "confidence": round(confidence, 4),
                "track_id": None,
            })
        detections.sort(key=lambda item: item["confidence"], reverse=True)
        return detections[:MAX_PREVIEW_DETECTIONS]


def _gray_for_motion(frame, cv2):
    height, width = frame.shape[:2]
    scale = min(640.0 / max(width, height), 1.0)
    if scale < 1.0:
        frame = cv2.resize(frame, (max(1, round(width * scale)), max(1, round(height * scale))))
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), scale


def _estimate_camera_transform(previous_gray, current_gray, cv2, scale: float):
    """Estimate a conservative previous-frame -> current-frame background homography."""
    import numpy as np

    orb = cv2.ORB_create(nfeatures=1200, scaleFactor=1.2, nlevels=6)
    previous_keypoints, previous_descriptors = orb.detectAndCompute(previous_gray, None)
    current_keypoints, current_descriptors = orb.detectAndCompute(current_gray, None)
    if previous_descriptors is None or current_descriptors is None:
        return None
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    pairs = matcher.knnMatch(previous_descriptors, current_descriptors, k=2)
    good = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance]
    if len(good) < 20:
        return None
    source = np.float32([previous_keypoints[item.queryIdx].pt for item in good]).reshape(-1, 1, 2)
    target = np.float32([current_keypoints[item.trainIdx].pt for item in good]).reshape(-1, 1, 2)
    matrix, inlier_mask = cv2.findHomography(source, target, cv2.RANSAC, 3.0)
    if matrix is None or inlier_mask is None:
        return None
    inliers = int(inlier_mask.sum())
    if inliers < 12 or inliers / len(good) < 0.35:
        return None
    # Convert the transform from downscaled coordinates back to original pixels.
    scale_matrix = np.diag([scale, scale, 1.0])
    inverse_scale = np.diag([1.0 / scale, 1.0 / scale, 1.0])
    full_resolution_matrix = inverse_scale @ matrix @ scale_matrix
    if not np.isfinite(full_resolution_matrix).all() or abs(np.linalg.det(full_resolution_matrix)) < 1e-8:
        return None
    return full_resolution_matrix.tolist()


def analyze_media(media_path: str | Path, media_kind: str, anchor_gcj: dict | None,
                  camera_stabilized: bool = False,
                  *, detector=None) -> dict:
    """Analyze a manager-uploaded item and return bounded, review-only evidence."""
    path = Path(media_path)
    if not path.is_file():
        raise FileNotFoundError("巡检影像文件已丢失")
    detector = detector or OnnxDetector(
        config.VISION_MODEL_PATH,
        model_id=getattr(config, "VISION_MODEL_ID", "visdrone-rtdetrv4-s"),
        model_version=getattr(config, "VISION_MODEL_REVISION", "unspecified"),
    )
    started = time.monotonic()
    try:
        import cv2
    except ImportError as error:
        raise VisionConfigurationError("视觉依赖未安装，请安装 requirements-vision.txt") from error

    frames: list[list[dict]] = []
    transforms: list[list[list[float]] | None] = [None]
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
        previous_gray = None
        previous_scale = 1.0
        try:
            while sampled < config.VISION_MAX_VIDEO_FRAMES:
                ok, frame = capture.read()
                if not ok:
                    break
                if frame_index % stride == 0:
                    height, width = frame.shape[:2]
                    _validate_frame_dimensions(width, height)
                    frames.append(detector.detect(frame, tracking=False))
                    if camera_stabilized:
                        transforms.append([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
                    else:
                        gray, scale = _gray_for_motion(frame, cv2)
                        transform = (
                            _estimate_camera_transform(previous_gray, gray, cv2, scale)
                            if previous_gray is not None and abs(scale - previous_scale) < 1e-6
                            else None
                        )
                        transforms.append(transform)
                        previous_gray, previous_scale = gray, scale
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
        frame_transforms=transforms if media_kind == "video" else None,
    )
    model_info = {
        "id": getattr(detector, "model_id", "injected-detector"),
        "version": getattr(detector, "model_version", "unspecified"),
        "confidence_threshold": getattr(detector, "confidence_threshold", None),
        "classes": list(VISDRONE_CLASSES),
        "dataset_source": "VisDrone2019-DET (AISKYEYE team, Tianjin University)",
        "dataset_license": "CC BY-NC-SA 3.0; non-commercial academic research use",
        "weights_distributed_by_application": False,
    }
    preview_detections = []
    if media_kind == "image":
        preview_detections = [
            item for item in frames[0]
            if item.get("label", "").strip().lower() in VEHICLE_LABELS | {"bicycle"}
        ][:MAX_PREVIEW_DETECTIONS]
    has_anchor = anchor_gcj is not None
    return {
        **analysis,
        "media": {"kind": media_kind, "width": width, "height": height},
        "model": model_info,
        "preview_detections": preview_detections,
        "anchor_gcj": anchor_gcj,
        "location_precision": "operator_selected_area_only" if has_anchor else "not_provided",
        "review_guidance": (
            "图像位置仅代表管理员标注的观察区域，不是检测框的地面坐标；确认管制前须人工选取具体道路段。"
            if has_anchor else
            "影像未提供地面坐标；确认候选并转入路况流程后，须由管理员人工选取具体道路段。"
        ),
    }
