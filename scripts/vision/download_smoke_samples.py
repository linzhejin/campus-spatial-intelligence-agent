#!/usr/bin/env python3
"""Fetch a small, pinned set of public VisDrone images for UI/runtime smoke checks."""
from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path


DATASET = "Voxel51/VisDrone2019-DET"
REVISION = "3c3b9e9bd44c91121c1fc19fce428f0e6b71bba2"
MODEL_REPOSITORY = "dronefreak/visdrone-rtdetrv4-s"
MODEL_REVISION = "820fca3d962243f7d611dd35cf2a64d267635499"
DEMO_VIDEO = "assets/demo_banner.mp4"
DEMO_VIDEO_SHA256 = "aa3b5e47718601d48f954ad07b16f9f0a43f544a9da8fa14ca97570b1236fce3"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/vision/visdrone-smoke"))
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--with-video", action="store_true",
                        help="also download the model card's public VisDrone video example")
    args = parser.parse_args()
    if not 1 <= args.count <= 32:
        parser.error("--count must be between 1 and 32")
    api = f"https://huggingface.co/api/datasets/{DATASET}/tree/{REVISION}?recursive=true&expand=true"
    with urllib.request.urlopen(api, timeout=30) as response:
        entries = json.load(response)
    images = [
        entry for entry in entries
        if entry.get("path", "").lower().endswith((".jpg", ".jpeg", ".png"))
        and entry.get("lfs", {}).get("oid")
    ][:args.count]
    if len(images) < args.count:
        parser.error("the pinned dataset snapshot does not contain enough LFS images")
    args.output.mkdir(parents=True, exist_ok=True)
    samples = []
    for entry in images:
        filename = Path(entry["path"]).name
        target = args.output / filename
        expected_hash = entry["lfs"]["oid"]
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected_hash:
            url = f"https://huggingface.co/datasets/{DATASET}/resolve/{REVISION}/{entry['path']}?download=true"
            request = urllib.request.Request(url, headers={"User-Agent": "WHU-Walker-Academic-Prototype/1.0"})
            partial = target.with_suffix(target.suffix + ".part")
            digest = hashlib.sha256()
            try:
                with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as out:
                    while block := response.read(1024 * 1024):
                        digest.update(block)
                        out.write(block)
                if digest.hexdigest() != expected_hash:
                    raise RuntimeError(f"sample hash mismatch: {filename}")
                partial.replace(target)
            finally:
                partial.unlink(missing_ok=True)
        samples.append({"path": entry["path"], "sha256": expected_hash, "size": entry["size"]})
    manifest = {
        "dataset": DATASET,
        "revision": REVISION,
        "source": "Hugging Face image sample; no labels in this repository snapshot",
        "purpose": "runtime and visualization smoke only; do not compute accuracy metrics",
        "license_note": "Follow the original VisDrone non-commercial research terms and provide attribution.",
        "samples": samples,
    }
    if args.with_video:
        target = args.output / "visdrone-model-demo.mp4"
        url = f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/{MODEL_REVISION}/{DEMO_VIDEO}?download=true"
        request = urllib.request.Request(url, headers={"User-Agent": "WHU-Walker-Academic-Prototype/1.0"})
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != DEMO_VIDEO_SHA256:
            partial = target.with_suffix(target.suffix + ".part")
            digest = hashlib.sha256()
            try:
                with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as out:
                    while block := response.read(1024 * 1024):
                        digest.update(block)
                        out.write(block)
                if digest.hexdigest() != DEMO_VIDEO_SHA256:
                    raise RuntimeError("pinned model demo video hash mismatch")
                partial.replace(target)
            finally:
                partial.unlink(missing_ok=True)
        manifest["video_demo"] = {
            "model_repository": MODEL_REPOSITORY,
            "model_revision": MODEL_REVISION,
            "path": DEMO_VIDEO,
            "local_path": target.name,
            "sha256": DEMO_VIDEO_SHA256,
            "purpose": "video pipeline smoke only; no labels or accident ground truth",
        }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
