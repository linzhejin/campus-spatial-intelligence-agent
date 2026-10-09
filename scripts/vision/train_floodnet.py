#!/usr/bin/env python3
"""Train and export a compact FloodNet ten-class road-water segmenter.

Run this with an isolated PyTorch training environment. It does not install ML
packages into the Flask/production environment and never puts dataset files or
weights in the repository.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from vision.floodnet_training import (
    FloodNetSegmentationDataset,
    build_floodnet_model,
    evaluate_floodnet,
    export_floodnet_onnx,
    floodnet_class_weights,
    floodnet_focus_sample_weights,
    set_training_seed,
    train_floodnet,
    validate_floodnet_manifest,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    import torch
    from torch.utils.data import DataLoader, WeightedRandomSampler

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="outside the source repo; contains local checkpoints and ONNX export")
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--tile-size", type=int, default=512)
    parser.add_argument("--overlap", type=int, default=128)
    parser.add_argument("--eval-overlap", type=int, default=64,
                        help="held-out tiling overlap; full raster remains covered")
    parser.add_argument("--positive-sample-weight", type=float, default=8.0,
                        help="training-image sampling weight when the flooded-road class is present")
    parser.add_argument("--focus-probability", type=float, default=0.9,
                        help="probability to center a training crop on flooded-road pixels in positive images")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20261010)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--pretrained", action="store_true",
                        help="initialize the MobileNetV3-small encoder from torchvision weights")
    args = parser.parse_args()
    if not args.dataset_root.is_dir() or not args.manifest.is_file():
        parser.error("dataset root and manifest must exist")
    if args.epochs < 1 or args.batch_size < 1 or args.threads < 1:
        parser.error("epochs, batch-size, and threads must be positive")
    if (args.tile_size < 32 or not 0 <= args.overlap < args.tile_size
            or not 0 <= args.eval_overlap < args.tile_size):
        parser.error("tile size or train/evaluation overlap is invalid")
    if args.positive_sample_weight < 1 or not 0 <= args.focus_probability <= 1:
        parser.error("positive sample weight must be at least 1 and focus probability between 0 and 1")
    try:
        args.output_dir.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        parser.error("output-dir must be outside the source repository so weights stay out of Git")
    try:
        manifest_bytes = args.manifest.read_bytes()
        manifest = validate_floodnet_manifest(json.loads(manifest_bytes.decode("utf-8-sig")))
        for sample in manifest["samples"]:
            for field in ("image", "mask"):
                path = (args.dataset_root / sample[field]).resolve()
                path.relative_to(args.dataset_root.resolve())
                if not path.is_file():
                    raise ValueError(f"missing dataset file: {sample[field]}")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        parser.error(str(error))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    set_training_seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    train_samples = [sample for sample in manifest["samples"] if sample["split"] == "train"]
    validation_samples = [sample for sample in manifest["samples"] if sample["split"] == "validation"]
    test_samples = [sample for sample in manifest["samples"] if sample["split"] == "test"]
    train_data = FloodNetSegmentationDataset(
        args.dataset_root, train_samples, tile_size=args.tile_size, overlap=args.overlap,
        augment=True, one_tile_per_sample=True, random_crop_per_sample=True,
        focus_class_id=3, focus_probability=args.focus_probability,
    )
    validation_data = FloodNetSegmentationDataset(
        args.dataset_root, validation_samples, tile_size=args.tile_size, overlap=args.overlap,
        one_tile_per_sample=True, random_crop_per_sample=False,
        focus_class_id=3, focus_class_center=True,
    )
    test_data = FloodNetSegmentationDataset(
        args.dataset_root, test_samples, tile_size=args.tile_size, overlap=args.eval_overlap,
    )
    generator = torch.Generator().manual_seed(args.seed)
    sample_weights, sample_weight_report = floodnet_focus_sample_weights(
        args.dataset_root, train_samples, positive_class_id=3,
        positive_weight=args.positive_sample_weight,
    )
    train_sampler = WeightedRandomSampler(
        sample_weights, num_samples=len(train_data), replacement=True, generator=generator,
    )
    train_loader = DataLoader(
        train_data, batch_size=args.batch_size, sampler=train_sampler,
        num_workers=0, pin_memory=False, drop_last=False,
    )
    validation_loader = DataLoader(
        validation_data, batch_size=1, shuffle=False, num_workers=0, pin_memory=False,
    )
    test_loader = DataLoader(
        test_data, batch_size=1, shuffle=False, num_workers=0, pin_memory=False,
    )
    weights = floodnet_class_weights(args.dataset_root, train_samples)
    model = build_floodnet_model(pretrained=args.pretrained)
    checkpoint_path = args.output_dir / "floodnet-best-state.pt"
    training_started = time.monotonic()
    training_report = train_floodnet(
        model, train_loader, validation_loader, device=device,
        checkpoint_path=checkpoint_path, epochs=args.epochs,
        learning_rate=args.learning_rate, class_weights=weights,
        progress_callback=lambda row: print(
            json.dumps({"epoch_validation": row}, ensure_ascii=False), flush=True,
        ),
    )
    model.load_state_dict(torch.load(checkpoint_path, map_location="cpu", weights_only=True))
    model.to(device)
    test_metrics = evaluate_floodnet(model, test_loader, device=device)
    onnx_path = args.output_dir / "floodnet-mobilenetv3-unet.onnx"
    export = export_floodnet_onnx(model, onnx_path, input_size=args.tile_size)
    report = {
        "task": manifest["task"],
        "dataset": manifest["dataset"],
        "dataset_license": manifest["license"],
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "split_unit": manifest["split_unit"],
        "split_sample_counts": {"train": len(train_samples),
                                 "validation": len(validation_samples),
                                 "test": len(test_samples)},
        "pixel_alignment_by_split": {
            "train": train_data.alignment_report,
            "validation": validation_data.alignment_report,
            "test": test_data.alignment_report,
        },
        "training_config": {
            "seed": args.seed, "epochs": args.epochs, "batch_size": args.batch_size,
            "tile_size": args.tile_size, "overlap": args.overlap,
            "evaluation_overlap": args.eval_overlap,
            "threads": args.threads, "device": str(device),
            "pretrained_encoder": args.pretrained,
            "learning_rate": args.learning_rate,
            "class_weights": [round(float(value), 6) for value in weights.tolist()],
            "positive_sample_sampling": sample_weight_report,
            "focus_class_id": 3,
            "focus_probability": args.focus_probability,
            "num_workers": 0, "pin_memory": False,
            "validation_sampling": "one deterministic tile per image; center on the flooded-road class centroid when present; checkpoint selection only",
        },
        "training": training_report,
        "held_out_test_metrics": test_metrics,
        "onnx_export": export,
        "checkpoint_sha256": _sha256(checkpoint_path),
        "onnx_sha256": _sha256(onnx_path),
        "elapsed_seconds": round(time.monotonic() - training_started, 2),
        "limitations": manifest["limitations"] + [
            "Held-out metrics are image-ID splits; no flight/scene IDs are available for independence claims.",
            "Public FloodNet post-disaster imagery does not establish campus shallow-water performance.",
            "The test metrics are pixel segmentation scores, not road-closure decision accuracy.",
        ],
    }
    report_path = args.output_dir / "floodnet-training-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    test_manifest = dict(manifest)
    test_manifest["samples"] = test_samples
    test_manifest_path = args.output_dir / "floodnet-held-out-test.json"
    test_manifest_path.write_text(
        json.dumps(test_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps({
        "report": str(report_path),
        "model": str(onnx_path),
        "device": str(device),
        "test_flooded_road": test_metrics["flooded_road"],
        "test_mean_iou": test_metrics["mean_iou"],
        "elapsed_seconds": report["elapsed_seconds"],
        "limitations": report["limitations"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
