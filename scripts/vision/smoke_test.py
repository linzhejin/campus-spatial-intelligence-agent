#!/usr/bin/env python3
"""Run a reproducible CPU smoke test on a directory of public aerial images."""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import cv2

from vision.engine import OnnxDetector, analyze_media


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=ROOT / "models/vision/visdrone-rtdetrv4-s.onnx")
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--render-dir", type=Path)
    parser.add_argument("--confidence", type=float, default=0.369)
    parser.add_argument("--video", type=Path,
                        help="optional public video sample for end-to-end sequence and motion-compensation smoke")
    args = parser.parse_args()
    images = sorted(
        path for path in args.images.iterdir()
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    ) if args.images.is_dir() else [args.images]
    if not images:
        parser.error("没有找到可读取的 JPG、PNG 或 WebP 样本")
    if args.render_dir:
        args.render_dir.mkdir(parents=True, exist_ok=True)

    detector = OnnxDetector(str(args.model), confidence_threshold=args.confidence)
    rows = []
    total_started = time.monotonic()
    for path in images:
        frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if frame is None:
            rows.append({"image": path.name, "error": "image_decode_failed"})
            continue
        started = time.monotonic()
        detections = detector.detect(frame)
        elapsed = time.monotonic() - started
        counts = Counter(item["label"] for item in detections)
        rows.append({
            "image": path.name,
            "width": int(frame.shape[1]),
            "height": int(frame.shape[0]),
            "detections": len(detections),
            "class_counts": dict(sorted(counts.items())),
            "mean_confidence": round(
                sum(item["confidence"] for item in detections) / len(detections), 4
            ) if detections else 0.0,
            "inference_seconds": round(elapsed, 4),
        })
        if args.render_dir:
            for item in detections:
                x1, y1, x2, y2 = map(int, item["box"])
                cv2.rectangle(frame, (x1, y1), (x2, y2), (50, 190, 240), 2)
                caption = f"{item['label']} {item['confidence']:.2f}"
                cv2.putText(frame, caption, (x1, max(14, y1 - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (20, 35, 30), 3, cv2.LINE_AA)
                cv2.putText(frame, caption, (x1, max(14, y1 - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (250, 245, 230), 1, cv2.LINE_AA)
            cv2.imwrite(str(args.render_dir / path.name), frame)

    successful = [row for row in rows if "error" not in row]
    video_report = None
    if args.video:
        video_result = analyze_media(args.video, "video", None, detector=detector)
        video_report = {
            "video": args.video.name,
            "metrics": video_result["metrics"],
            "candidate_kinds": [item["kind"] for item in video_result["candidates"]],
            "safety": video_result["safety"],
        }
    report = {
        "model": detector.model_id,
        "model_revision": detector.model_version,
        "confidence_threshold": detector.confidence_threshold,
        "execution_provider": "CPUExecutionProvider",
        "sample_count": len(images),
        "decoded_count": len(successful),
        "elapsed_seconds": round(time.monotonic() - total_started, 4),
        "images": rows,
        "video_sequence_smoke": video_report,
        "evaluation_note": "功能与推理冒烟检查；样本目录若无标注，不计算精确率、召回率或 mAP。",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if len(successful) == len(images) else 1


if __name__ == "__main__":
    raise SystemExit(main())
