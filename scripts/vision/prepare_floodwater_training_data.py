"""Extract one frame per second for a source-video-safe water experiment.

All generated images and manifests stay beside the downloaded research corpus,
outside the application repository. The official pseudo-labeled test videos
are intentionally not extracted or used for model selection.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vision.floodwater_training import build_floodwater_manifest  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, required=True,
                        help="extracted Floodwater Dataset v1.0.0 root")
    parser.add_argument("--sample-period-frames", type=int, default=25,
                        help="select one labeled frame every N source frames (default: 25)")
    parser.add_argument("--jpeg-quality", type=int, default=95)
    parser.add_argument("--dry-run", action="store_true", help="write no files")
    args = parser.parse_args(argv)
    dataset_root = args.dataset_root.resolve()
    if args.sample_period_frames < 1 or not 50 <= args.jpeg_quality <= 100:
        parser.error("sample period must be positive and JPEG quality must be 50..100")
    try:
        manifest = build_floodwater_manifest(
            dataset_root / "metadata" / "chunks.csv",
            dataset_root / "metadata" / "samples.csv",
            sample_period_frames=args.sample_period_frames,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))

    summary = {
        split: sum(sample["split"] == split for sample in manifest["samples"])
        for split in ("train", "validation", "test")
    }
    if args.dry_run:
        print(json.dumps({
            "samples_by_split": summary,
            "source_videos_by_split": {
                split: len(manifest["split_groups"][split])
                for split in ("train", "validation", "test")
            },
            "sample_period_frames": args.sample_period_frames,
            "writes_files": False,
        }, ensure_ascii=False, indent=2))
        return 0

    target = dataset_root / "derived"
    staging = dataset_root / ".derived.partial"
    if target.exists() or staging.exists():
        parser.error("derived/ or .derived.partial already exists; refusing to overwrite")

    try:
        import cv2
        import numpy as np
        from PIL import Image

        selected_by_chunk = defaultdict(list)
        for sample in manifest["samples"]:
            selected_by_chunk[sample["chunk_id"]].append(sample)
        chunks = {
            sample["chunk_id"]: sample["video"]
            for sample in manifest["samples"]
        }
        staging.mkdir()
        extracted_counts = Counter()
        for chunk_id, relative_video in sorted(chunks.items()):
            video_path = (dataset_root / relative_video).resolve()
            video_path.relative_to(dataset_root)
            if not video_path.is_file():
                raise ValueError(f"missing video: {relative_video}")
            selected = {sample["chunk_frame_index"]: sample for sample in selected_by_chunk[chunk_id]}
            capture = cv2.VideoCapture(str(video_path))
            if not capture.isOpened():
                raise ValueError(f"cannot open video: {relative_video}")
            fps = float(capture.get(cv2.CAP_PROP_FPS))
            if fps > 0 and abs(fps - manifest["sampling"]["source_fps"]) > 0.5:
                capture.release()
                raise ValueError(f"unexpected source FPS {fps} in {relative_video}")
            max_frame = max(selected)
            frame_index = 0
            while frame_index <= max_frame:
                ok, frame = capture.read()
                if not ok:
                    break
                sample = selected.get(frame_index)
                if sample is not None:
                    mask_path = (dataset_root / sample["mask"]).resolve()
                    mask_path.relative_to(dataset_root)
                    with Image.open(mask_path) as source:
                        mask = np.asarray(source.convert("L"))
                    if mask.shape != frame.shape[:2]:
                        raise ValueError(f"frame/mask dimension mismatch for {sample['sample_id']}")
                    if not np.isin(np.unique(mask), (0, 255)).all():
                        raise ValueError(f"non-binary water mask for {sample['sample_id']}")
                    image_path = staging / "frames" / chunk_id / f"{frame_index:05d}.jpg"
                    image_path.parent.mkdir(parents=True, exist_ok=True)
                    if not cv2.imwrite(str(image_path), frame,
                                       [cv2.IMWRITE_JPEG_QUALITY, args.jpeg_quality]):
                        raise OSError(f"could not write frame {sample['sample_id']}")
                    extracted_counts[sample["split"]] += 1
                frame_index += 1
            capture.release()
            missing = sorted(set(selected) - {
                int(path.stem) for path in (staging / "frames" / chunk_id).glob("*.jpg")
            })
            if missing:
                raise ValueError(f"video ended before selected frames in {chunk_id}: {missing[:3]}")
            print(f"extracted {chunk_id}: {len(selected)} frames", flush=True)

        if any(extracted_counts[split] != summary[split] for split in ("train", "validation")):
            raise ValueError("extracted frame counts do not match the manifest")
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
        staging.replace(target)
    except Exception as error:
        print(f"preparation failed; inspect the preserved staging directory {staging}: {error}",
              file=sys.stderr)
        return 2

    print(json.dumps({
        "output": str(target),
        "manifest": str(target / "manifest.json"),
        "extracted_samples_by_split": dict(extracted_counts),
        "license": manifest["license"],
        "training_scope": "train + validation only; official pseudo-labeled test split excluded",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
