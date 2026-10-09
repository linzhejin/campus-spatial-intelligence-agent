#!/usr/bin/env python3
"""Train a compact AIDER scene classifier in an isolated PyTorch environment.

The output remains a local research artifact. AIDER has no flight/incident IDs,
so neither the held-out score nor the calibrated operating point proves
independent-event or campus generalization.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import sys
import time
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from vision.aider_training import choose_accident_threshold, validate_aider_training_manifest
from vision.specialized import AIDER_CLASSES, _image_tensor
from vision.specialized_evaluation import (
    cluster_bootstrap_interval,
    summarize_binary_classification,
    summarize_classification,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _metric_rows(labels, predictions, incident_scores, *, threshold: float) -> dict:
    actual_binary = [label == "traffic_incident" for label in labels]
    predicted_binary = [float(score) >= threshold for score in incident_scores]
    return {
        "scene_classification": summarize_classification(labels, predictions),
        "traffic_incident_operating_point": summarize_binary_classification(
            actual_binary, predicted_binary,
        ),
    }


def main() -> int:
    import numpy as np
    import cv2
    import onnx
    import onnxruntime
    import torch
    import torchvision
    from torch.utils.data import DataLoader, Dataset
    from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="outside the source repo; stores checkpoints, report and ONNX")
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20261010)
    parser.add_argument("--backbone-learning-rate", type=float, default=2e-5)
    parser.add_argument("--head-learning-rate", type=float, default=5e-4)
    parser.add_argument("--min-precision", type=float, default=0.90)
    parser.add_argument("--min-recall", type=float, default=0.80)
    parser.add_argument("--no-pretrained", action="store_true",
                        help="initialize MobileNetV3-small without cached ImageNet weights")
    args = parser.parse_args()
    if (args.epochs < 1 or args.batch_size < 1 or args.threads < 1
            or args.backbone_learning_rate <= 0 or args.head_learning_rate <= 0):
        parser.error("epochs, batch-size, threads and learning rates must be positive")
    if not 0 <= args.min_precision <= 1 or not 0 <= args.min_recall <= 1:
        parser.error("precision and recall targets must be between 0 and 1")
    if not args.dataset_root.is_dir() or not args.manifest.is_file():
        parser.error("dataset-root and manifest must exist")
    try:
        args.output_dir.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        parser.error("output-dir must be outside the source repository")

    try:
        manifest_bytes = args.manifest.read_bytes()
        manifest = validate_aider_training_manifest(json.loads(manifest_bytes.decode("utf-8-sig")))
        dataset_root = args.dataset_root.resolve()
        for index, sample in enumerate(manifest["samples"]):
            path = (dataset_root / Path(*PurePosixPath(sample["image"]).parts)).resolve()
            path.relative_to(dataset_root)
            if not path.is_file():
                raise ValueError(f"sample {index} image does not exist: {sample['image']}")
            if _sha256(path) != sample["sha256"]:
                raise ValueError(f"sample {index} image SHA-256 differs from manifest")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        parser.error(str(error))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    class_to_idx = {label: index for index, label in enumerate(AIDER_CLASSES)}

    class AIDERDataset(Dataset):
        def __init__(self, split: str, *, augment: bool):
            self.rows = [row for row in manifest["samples"] if row["split"] == split]
            self.augment = augment
            if not self.rows:
                raise ValueError(f"AIDER manifest has no {split} samples")

        def __len__(self):
            return len(self.rows)

        def __getitem__(self, index):
            row = self.rows[index]
            path = (dataset_root / Path(*PurePosixPath(row["image"]).parts)).resolve()
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError(f"AIDER image cannot be decoded: {row['image']}")
            if self.augment:
                if random.random() < 0.5:
                    image = cv2.flip(image, 1)
                if random.random() < 0.5:
                    image = cv2.flip(image, 0)
                if random.random() < 0.5:
                    image = cv2.rotate(image, (cv2.ROTATE_90_CLOCKWISE,
                                               cv2.ROTATE_180,
                                               cv2.ROTATE_90_COUNTERCLOCKWISE)[random.randrange(3)])
            tensor = _image_tensor(image, "images", (224, 224))["images"][0]
            return torch.from_numpy(tensor.copy()), class_to_idx[row["label"]]

    train_data = AIDERDataset("train", augment=True)
    validation_data = AIDERDataset("validation", augment=False)
    test_data = AIDERDataset("test", augment=False)
    generator = torch.Generator().manual_seed(args.seed)
    train_loader = DataLoader(train_data, batch_size=args.batch_size, shuffle=True,
                              generator=generator, num_workers=0, pin_memory=False)
    validation_loader = DataLoader(validation_data, batch_size=args.batch_size, shuffle=False,
                                   num_workers=0, pin_memory=False)
    test_loader = DataLoader(test_data, batch_size=args.batch_size, shuffle=False,
                             num_workers=0, pin_memory=False)

    weights = None if args.no_pretrained else MobileNet_V3_Small_Weights.IMAGENET1K_V1
    pretrained_path = (Path(torch.hub.get_dir()) / "checkpoints" / Path(weights.url).name
                       if weights is not None else None)
    pretrained_provenance = ({"weights": str(weights), "url": weights.url,
                              "sha256": _sha256(pretrained_path)}
                             if pretrained_path is not None and pretrained_path.is_file()
                             else ({"weights": str(weights), "url": weights.url,
                                    "sha256": None, "cached_file_found": False}
                                   if weights is not None else None))
    model = mobilenet_v3_small(weights=weights)
    model.classifier[3] = torch.nn.Linear(model.classifier[3].in_features, len(AIDER_CLASSES))
    model.to(device)
    train_counts = {label: sum(row["label"] == label for row in train_data.rows)
                    for label in AIDER_CLASSES}
    class_weights = torch.tensor([
        (len(train_data) / (len(AIDER_CLASSES) * train_counts[label])) ** 0.5
        for label in AIDER_CLASSES
    ], dtype=torch.float32, device=device)
    class_weights /= class_weights.mean()
    criterion = torch.nn.CrossEntropyLoss(weight=class_weights)
    optimizer = torch.optim.AdamW([
        {"params": model.features.parameters(), "lr": args.backbone_learning_rate},
        {"params": model.classifier.parameters(), "lr": args.head_learning_rate},
    ], weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)
    best_checkpoint = args.output_dir / "aider-mobilenetv3-small-best.pt"
    best_validation = (-1.0, -1.0)
    history = []
    started = time.monotonic()

    def predict(loader):
        model.eval()
        actual, predicted, accident_scores = [], [], []
        with torch.inference_mode():
            for images, labels in loader:
                logits = model(images.to(device))
                scores = torch.softmax(logits, dim=1).cpu().numpy()
                for target, probability in zip(labels.tolist(), scores):
                    actual.append(AIDER_CLASSES[target])
                    predicted.append(AIDER_CLASSES[int(probability.argmax())])
                    accident_scores.append(float(probability[class_to_idx["traffic_incident"]]))
        return actual, predicted, accident_scores

    for epoch in range(1, args.epochs + 1):
        model.train()
        loss_sum = 0.0
        seen = 0
        for images, labels in train_loader:
            images = images.to(device, non_blocking=False)
            labels = labels.to(device, non_blocking=False)
            optimizer.zero_grad(set_to_none=True)
            logits = model(images)
            loss = criterion(logits, labels)
            if not torch.isfinite(loss):
                raise RuntimeError(f"non-finite AIDER training loss at epoch {epoch}")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            loss_sum += float(loss.detach().item()) * labels.shape[0]
            seen += labels.shape[0]
        scheduler.step()
        val_actual, val_predicted, val_scores = predict(validation_loader)
        val_metrics = summarize_classification(val_actual, val_predicted)
        score = (val_metrics["macro_f1"], val_metrics["accuracy"])
        epoch_row = {"epoch": epoch, "train_loss": round(loss_sum / max(1, seen), 6),
                     "validation_macro_f1": val_metrics["macro_f1"],
                     "validation_accuracy": val_metrics["accuracy"]}
        history.append(epoch_row)
        print(json.dumps({"epoch_validation": epoch_row}, ensure_ascii=False), flush=True)
        if score > best_validation:
            best_validation = score
            torch.save({"state_dict": model.state_dict(), "epoch": epoch,
                        "class_order": list(AIDER_CLASSES), "seed": args.seed,
                        "training_config": {"backbone": "torchvision_mobilenet_v3_small",
                                            "pretrained_imagenet": weights is not None}}, best_checkpoint)

    checkpoint = torch.load(best_checkpoint, map_location="cpu", weights_only=True)
    if checkpoint["class_order"] != list(AIDER_CLASSES):
        raise ValueError("saved model class order differs from production AIDER contract")
    model.load_state_dict(checkpoint["state_dict"])
    model.to(device)
    val_actual, val_predicted, val_scores = predict(validation_loader)
    threshold = choose_accident_threshold(
        [label == "traffic_incident" for label in val_actual], val_scores,
        min_precision=args.min_precision, min_recall=args.min_recall,
    )
    test_actual, test_predicted, test_scores = predict(test_loader)
    selected_threshold = threshold["threshold"]
    evaluated_threshold = (selected_threshold if selected_threshold is not None else
                           (threshold.get("validation_metrics") or {}).get("threshold"))
    if evaluated_threshold is not None:
        test_metrics = _metric_rows(test_actual, test_predicted, test_scores,
                                    threshold=evaluated_threshold)
        test_decisions = [float(value) >= evaluated_threshold for value in test_scores]
        test_truth = [label == "traffic_incident" for label in test_actual]
        clustered = {}
        for row, actual, predicted in zip(test_data.rows, test_truth, test_decisions):
            key = row["group_id"]
            value = clustered.setdefault(key, [0, 0, 0, 0])
            if actual and predicted: value[0] += 1
            elif not actual and predicted: value[1] += 1
            elif actual and not predicted: value[2] += 1
            else: value[3] += 1
        precision_interval = cluster_bootstrap_interval(
            clustered, lambda groups: sum(group[0] for group in groups)
            / max(1, sum(group[0] + group[1] for group in groups)),
        )
        recall_interval = cluster_bootstrap_interval(
            clustered, lambda groups: sum(group[0] for group in groups)
            / max(1, sum(group[0] + group[2] for group in groups)),
        )
        test_metrics["traffic_incident_operating_point"]["image_hash_cluster_bootstrap_95"] = {
            "precision": precision_interval, "recall": recall_interval,
            "note": "Image-hash-cluster resampling only; not independent incident confidence intervals.",
        }
        test_metrics["traffic_incident_operating_point"]["threshold_from_validation"] = evaluated_threshold
        test_metrics["traffic_incident_operating_point"]["validation_met_targets"] = threshold["meets_targets"]
    else:
        test_metrics = {"scene_classification": summarize_classification(test_actual, test_predicted),
                        "traffic_incident_operating_point": None}

    model.eval()
    dummy = torch.zeros((1, 3, 224, 224), dtype=torch.float32, device=device)
    onnx_path = args.output_dir / "aider-mobilenetv3-small.onnx"
    torch.onnx.export(model, dummy, str(onnx_path), input_names=["images"],
                      output_names=["scores"], opset_version=17,
                      dynamic_axes={"images": {0: "batch"}, "scores": {0: "batch"}},
                      dynamo=False)

    from vision.specialized import OnnxAiderClassifier
    onnx_classifier = OnnxAiderClassifier(str(onnx_path), threshold=(
        evaluated_threshold if evaluated_threshold is not None else 0.85),
                                          model_version="local-aider-baseline")
    parity_row = test_data.rows[0]
    parity_path = (dataset_root / Path(*PurePosixPath(parity_row["image"]).parts)).resolve()
    parity_frame = cv2.imread(str(parity_path), cv2.IMREAD_COLOR)
    if parity_frame is None:
        raise ValueError("could not decode held-out image for production preprocessing parity")
    production_scores = onnx_classifier.predict(parity_frame)["class_probabilities"]
    production_scores = np.asarray([production_scores[label] for label in AIDER_CLASSES], dtype=np.float32)
    with torch.inference_mode():
        production_tensor = _image_tensor(
            parity_frame, onnx_classifier.input_name, onnx_classifier.input_size,
        )[onnx_classifier.input_name]
        reference_scores = torch.softmax(
            model(torch.from_numpy(production_tensor).to(device)), dim=1,
        ).cpu().numpy()[0]
    parity_error = float(np.max(np.abs(reference_scores - production_scores)))
    if parity_error > 1e-4:
        raise RuntimeError(f"ONNX production preprocessing parity failed: max error {parity_error:.8f}")

    test_manifest = dict(manifest)
    test_manifest["split_groups"] = {"train": manifest["split_groups"]["train"],
                                     "validation": manifest["split_groups"]["validation"],
                                     "test": manifest["split_groups"]["test"]}
    test_manifest["samples"] = [row for row in manifest["samples"] if row["split"] == "test"]
    test_manifest_path = args.output_dir / "aider-held-out-test.json"
    test_manifest_path.write_text(json.dumps(test_manifest, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
    report = {
        "task": manifest["task"], "dataset": manifest["dataset"],
        "source": manifest["source"], "license": manifest["license"],
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "split_protocol": manifest["split_protocol"], "split_unit": manifest["split_unit"],
        "split_sample_counts": {split: sum(row["split"] == split for row in manifest["samples"])
                                for split in ("train", "validation", "test")},
        "class_split_counts": manifest["class_split_counts"],
        "class_order": list(AIDER_CLASSES),
        "training_config": {
            "architecture": "torchvision MobileNetV3-small scene classifier",
            "pretrained_imagenet": weights is not None,
            "pretrained_weights_provenance": pretrained_provenance,
            "seed": args.seed, "epochs": args.epochs, "batch_size": args.batch_size,
            "threads": args.threads, "device": str(device),
            "backbone_learning_rate": args.backbone_learning_rate,
            "head_learning_rate": args.head_learning_rate,
            "class_weights": [round(float(value), 6) for value in class_weights.cpu().tolist()],
            "num_workers": 0, "pin_memory": False,
            "augmentation": "horizontal/vertical flip and 90-degree rotations",
            "checkpoint_selection": "best validation macro-F1; held-out test untouched",
        },
        "environment": {
            "python": platform.python_version(), "torch": torch.__version__,
            "torchvision": torchvision.__version__, "numpy": np.__version__,
            "opencv": cv2.__version__, "onnx": onnx.__version__,
            "onnxruntime": onnxruntime.__version__,
        },
        "epoch_history": history, "best_epoch": checkpoint["epoch"],
        "validation_threshold_selection": threshold,
        "held_out_test_metrics": test_metrics,
        "onnx_production_preprocessing_parity": {"max_probability_absolute_error": parity_error,
                                                   "tolerance": 1e-4,
                                                   "passed": True},
        "image_level_operating_point_meets_targets": bool(
            threshold["meets_targets"] and evaluated_threshold is not None
            and test_metrics["traffic_incident_operating_point"] is not None
            and test_metrics["traffic_incident_operating_point"]["precision"] >= args.min_precision
            and test_metrics["traffic_incident_operating_point"]["recall"] >= args.min_recall
        ),
        "route_event_activation_eligible": False,
        "activation_blockers": [
            "AIDER contains still images and no ground-truth video incident/event IDs, so event-level candidate precision and recall are not measured.",
            "Campus UAV scenes and event review labels are not yet available.",
            "AIDER third-party redistribution and derivative-weight deployment rights remain unresolved.",
        ],
        "artifacts": {"checkpoint": str(best_checkpoint), "checkpoint_sha256": _sha256(best_checkpoint),
                      "onnx": str(onnx_path), "onnx_sha256": _sha256(onnx_path),
                      "test_manifest": str(test_manifest_path)},
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "limitations": manifest["limitations"] + [
            "Scene classification does not identify an accident vehicle, exact road position, cause, or severity.",
            "Public AIDER results are an initial scene-classification baseline and do not establish campus accident performance.",
            "A model that meets image-level precision/recall targets is still not enabled for route decisions without campus evidence and rights review.",
        ],
    }
    report_path = args.output_dir / "aider-training-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report_path), "model": str(onnx_path),
                      "test_manifest": str(test_manifest_path),
                      "threshold": threshold, "held_out_test_metrics": test_metrics,
                      "image_level_operating_point_meets_targets": report["image_level_operating_point_meets_targets"],
                      "route_event_activation_eligible": report["route_event_activation_eligible"],
                      "elapsed_seconds": report["elapsed_seconds"],
                      "limitations": report["limitations"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
