"""Reproducible AIDER image-level splits and operating-point selection."""
from __future__ import annotations

import hashlib
import math
import random
from collections import defaultdict
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Mapping

from vision.specialized import AIDER_CLASSES


# The split counts mirror TakuNet's public AIDER proportional protocol. AIDER
# does not include flight/incident IDs, so these are image-level splits only.
AIDER_PROPORTIONAL_SPLITS = {
    "collapsed_building": (335, 30, 146),
    "fire": (343, 30, 148),
    "flooded_areas": (346, 30, 150),
    "normal": (2450, 400, 1540),
    "traffic_incident": (316, 30, 139),
}
_SPLITS = ("train", "validation", "test")
_ZENODO_RECORD = "https://zenodo.org/records/3888300"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _target_counts(sample_count: int, expected: tuple[int, int, int] | None) -> dict[str, int]:
    if expected is not None:
        if (len(expected) != 3 or any(isinstance(value, bool) or not isinstance(value, int)
                                       or value < 1 for value in expected)):
            raise ValueError("AIDER split counts must be three positive integers")
        if sum(expected) == sample_count:
            return dict(zip(_SPLITS, expected))
        # Scale the upstream proportional protocol when a small number of
        # contradictory exact-duplicate labels have been excluded.
        exact = [sample_count * value / sum(expected) for value in expected]
        allocated = [int(value) for value in exact]
        for index in sorted(range(3), key=lambda item: exact[item] - allocated[item], reverse=True)[:
                sample_count - sum(allocated)]:
            allocated[index] += 1
        if min(allocated) < 1:
            raise ValueError("scaled AIDER protocol leaves a split empty")
        return dict(zip(_SPLITS, allocated))
    if sample_count < len(_SPLITS):
        raise ValueError("each AIDER class needs at least three images for train/validation/test")
    train = max(1, int(round(sample_count * 0.6)))
    validation = max(1, int(round(sample_count * 0.2)))
    test = sample_count - train - validation
    if test < 1:
        validation -= 1
        test = 1
    return {"train": train, "validation": validation, "test": test}


def _assign_hash_groups(groups: list[list[dict]], targets: dict[str, int], *, seed: int) -> dict[str, list[list[dict]]]:
    if len(groups) < len(_SPLITS):
        raise ValueError("each AIDER class needs at least three unique image hashes")
    ordered = list(groups)
    random.Random(seed).shuffle(ordered)
    assigned = {split: [] for split in _SPLITS}
    counts = {split: 0 for split in _SPLITS}
    tie_break = {split: rank for rank, split in enumerate(random.Random(seed ^ 0xA1DE).sample(_SPLITS, len(_SPLITS)))}

    for group in ordered:
        size = len(group)

        def imbalance(split: str) -> tuple[float, int]:
            score = 0.0
            for candidate in _SPLITS:
                projected = counts[candidate] + (size if candidate == split else 0)
                score += ((projected - targets[candidate]) / targets[candidate]) ** 2
            return score, tie_break[split]

        chosen = min(_SPLITS, key=imbalance)
        assigned[chosen].append(group)
        counts[chosen] += size

    if any(not assigned[split] for split in _SPLITS):
        raise ValueError("exact-image duplicates leave an empty AIDER split")
    return assigned


def build_aider_manifest(dataset_root: str | Path, *, seed: int = 20261010,
                          expected_split_counts: Mapping[str, tuple[int, int, int]] | None = None) -> dict:
    """Scan an extracted AIDER tree, group exact duplicates, and split per class.

    ``dataset_root`` must contain ``AIDER/<class>/*.jpg``. With
    ``expected_split_counts=AIDER_PROPORTIONAL_SPLITS`` the sample counts match
    the upstream TakuNet proportional protocol when there are no exact duplicate
    copies. Duplicate hashes are kept together, so actual counts may shift.
    """
    root = Path(dataset_root).resolve()
    class_root = root / "AIDER"
    if not class_root.is_dir():
        raise ValueError("dataset root must contain an AIDER directory")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")

    samples: list[dict] = []
    split_groups = {split: [] for split in _SPLITS}
    class_counts: dict[str, dict[str, int]] = {}
    files_by_class: dict[str, list[tuple[Path, str, str]]] = {}
    hash_labels: dict[str, set[str]] = defaultdict(set)
    for class_index, label in enumerate(AIDER_CLASSES):
        class_dir = class_root / label
        if not class_dir.is_dir():
            raise ValueError(f"AIDER class directory is missing: {label}")
        files = sorted(path for path in class_dir.rglob("*")
                       if path.is_file() and path.suffix.lower() == ".jpg")
        if not files:
            raise ValueError(f"AIDER class contains no JPEG images: {label}")
        records = []
        for path in files:
            relative = path.relative_to(root).as_posix()
            digest = _sha256(path)
            records.append((path, relative, digest))
            hash_labels[digest].add(label)
        files_by_class[label] = records

    conflicting_hashes = {digest for digest, labels in hash_labels.items() if len(labels) > 1}
    excluded_conflicts = [
        {"sha256": digest,
         "labels": sorted(hash_labels[digest]),
         "images": sorted(relative for rows in files_by_class.values()
                          for _, relative, row_digest in rows if row_digest == digest)}
        for digest in sorted(conflicting_hashes)
    ]
    for class_index, label in enumerate(AIDER_CLASSES):
        records = [row for row in files_by_class[label] if row[2] not in conflicting_hashes]
        by_hash: dict[str, list[dict]] = defaultdict(list)
        for _, relative, digest in records:
            by_hash[digest].append({"image": relative, "label": label})
        if not records:
            raise ValueError(f"all images in AIDER class {label} have conflicting duplicate labels")
        expected = expected_split_counts.get(label) if expected_split_counts is not None else None
        if expected_split_counts is not None and label not in expected_split_counts:
            raise ValueError(f"AIDER split protocol is missing class counts: {label}")
        targets = _target_counts(len(records), expected)
        groups = [sorted(rows, key=lambda row: row["image"]) for _, rows in sorted(by_hash.items())]
        assigned = _assign_hash_groups(groups, targets, seed=seed + class_index)
        class_counts[label] = {}
        for split in _SPLITS:
            split_count = 0
            for group in assigned[split]:
                group_id = _sha256(root.joinpath(*PurePosixPath(group[0]["image"]).parts))
                split_groups[split].append(group_id)
                for row in group:
                    item = {**row, "group_id": group_id, "sha256": group_id, "split": split}
                    samples.append(item)
                    split_count += 1
            class_counts[label][split] = split_count

    samples.sort(key=lambda row: (AIDER_CLASSES.index(row["label"]), row["image"]))
    manifest = {
        "schema_version": 1,
        "task": "accident_classification",
        "dataset": "AIDER aerial image scene classification",
        "source": _ZENODO_RECORD,
        "license_status": "unresolved_conflicting_notices",
        "license": ("Zenodo metadata lists CC-BY-4.0, while the record notes © 2020 IEEE, personal use only, "
                    "and requires IEEE permission for other uses; do not redistribute images or derived weights "
                    "until rights are clarified"),
        "split_protocol": ("TakuNet proportional counts with fixed seed and exact-hash grouping"
                           if expected_split_counts is not None
                           else "stratified 60/20/20 image-level split with fixed seed and exact-hash grouping"),
        "split_unit": "exact_image_hash_cluster",
        "seed": seed,
        "class_order": list(AIDER_CLASSES),
        "split_groups": split_groups,
        "class_split_counts": class_counts,
        "excluded_cross_class_exact_duplicates": excluded_conflicts,
        "samples": samples,
        "limitations": [
            "AIDER provides no source flight, incident, or scene IDs; exact image hashes are only duplicate protection, not incident-level independence.",
            "Near-duplicate images are not clustered; evaluation cannot establish cross-flight or campus generalization.",
            f"{len(excluded_conflicts)} exact-image group(s) carrying conflicting class labels were excluded from all splits.",
            "AIDER traffic_incident is a scene-level cue and does not localize a vehicle or prove a campus road incident.",
            "The Zenodo record lists CC-BY-4.0 metadata but also states © 2020 IEEE, personal use only, with IEEE permission required for other uses; treat redistribution and derived-weight rights as unresolved.",
        ],
    }
    return validate_aider_training_manifest(manifest)


def validate_aider_training_manifest(manifest: dict) -> dict:
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("AIDER training manifest schema_version must be 1")
    if manifest.get("task") != "accident_classification":
        raise ValueError("AIDER training manifest task must be accident_classification")
    if manifest.get("class_order") != list(AIDER_CLASSES):
        raise ValueError("AIDER training manifest class_order must match model output order")
    if manifest.get("split_unit") != "exact_image_hash_cluster":
        raise ValueError("AIDER split unit must state exact-image-hash clustering")
    license_note = manifest.get("license")
    if not isinstance(license_note, str) or "CC-BY-4.0" not in license_note or "IEEE" not in license_note:
        raise ValueError("AIDER license note must retain both the Zenodo metadata and IEEE notice")
    license_status = manifest.get("license_status")
    if license_status is None:
        # Normalize pre-status manifests only when their original notice already
        # records both sides of the rights conflict.
        license_status = "unresolved_conflicting_notices"
    if license_status != "unresolved_conflicting_notices":
        raise ValueError("AIDER license status must preserve the unresolved rights conflict")
    groups = manifest.get("split_groups")
    if not isinstance(groups, dict):
        raise ValueError("AIDER split_groups is required")
    normalized = {}
    for split in _SPLITS:
        values = groups.get(split)
        if not isinstance(values, list) or not values:
            raise ValueError(f"AIDER split_groups.{split} must be a non-empty list")
        values = [str(value).strip() for value in values]
        if any(not value for value in values) or len(values) != len(set(values)):
            raise ValueError(f"AIDER split_groups.{split} contains empty or duplicate IDs")
        normalized[split] = values
    flattened = [group for split in _SPLITS for group in normalized[split]]
    if len(flattened) != len(set(flattened)):
        raise ValueError("AIDER image-hash groups overlap across splits")

    samples = manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("AIDER samples must be a non-empty list")
    seen_paths = set()
    observed_groups = {split: set() for split in _SPLITS}
    group_labels: dict[str, str] = {}
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            raise ValueError(f"AIDER sample {index} must be an object")
        image = str(sample.get("image") or "").strip()
        posix = PurePosixPath(image.replace("\\", "/"))
        windows = PureWindowsPath(image)
        if (not image or posix.is_absolute() or windows.is_absolute() or ".." in posix.parts
                or (posix.parts and ":" in posix.parts[0])):
            raise ValueError(f"AIDER sample {index} has an unsafe image path")
        if image in seen_paths:
            raise ValueError("AIDER manifest contains a duplicate image path")
        seen_paths.add(image)
        label = sample.get("label")
        if label not in AIDER_CLASSES:
            raise ValueError(f"AIDER sample {index} has an unknown class")
        split = sample.get("split")
        group_id = str(sample.get("group_id") or "").strip()
        if split not in _SPLITS or not group_id or group_id not in normalized[split]:
            raise ValueError(f"AIDER sample {index} does not match its declared split group")
        digest = str(sample.get("sha256") or "").strip().lower()
        if digest != group_id or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError(f"AIDER sample {index} must use its SHA-256 as group ID")
        prior_label = group_labels.setdefault(group_id, label)
        if prior_label != label:
            raise ValueError("identical AIDER images have conflicting class labels")
        observed_groups[split].add(group_id)
    if any(observed_groups[split] != set(normalized[split]) for split in _SPLITS):
        raise ValueError("AIDER split_groups must exactly match the groups in samples")
    for label in AIDER_CLASSES:
        if any(not any(row["label"] == label and row["split"] == split for row in samples)
               for split in _SPLITS):
            raise ValueError(f"AIDER class {label} must appear in every split")
    result = dict(manifest)
    result["license_status"] = license_status
    result["split_groups"] = normalized
    return result


def choose_accident_threshold(actual_positive, probabilities, *, min_precision: float = 0.9,
                              min_recall: float = 0.8) -> dict:
    actual = list(actual_positive)
    scores = list(probabilities)
    if not actual or len(actual) != len(scores):
        raise ValueError("validation labels and probabilities must have equal non-empty lengths")
    if any(not isinstance(value, bool) for value in actual):
        raise ValueError("validation incident labels must be boolean")
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(float(value)) or not 0 <= float(value) <= 1 for value in scores):
        raise ValueError("validation probabilities must be finite values between 0 and 1")
    if (isinstance(min_precision, bool) or not isinstance(min_precision, (int, float))
            or not 0 <= float(min_precision) <= 1
            or isinstance(min_recall, bool) or not isinstance(min_recall, (int, float))
            or not 0 <= float(min_recall) <= 1):
        raise ValueError("precision and recall targets must be between 0 and 1")

    candidates = sorted({0.0, 1.0, *(float(score) for score in scores)}, reverse=True)
    eligible = []
    best_effort = None
    positive_count = sum(actual)
    for threshold in candidates:
        predicted = [float(score) >= threshold for score in scores]
        tp = sum(left and right for left, right in zip(actual, predicted))
        fp = sum((not left) and right for left, right in zip(actual, predicted))
        fn = sum(left and (not right) for left, right in zip(actual, predicted))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / positive_count if positive_count else 0.0
        row = {"threshold": round(threshold, 8), "tp": tp, "fp": fp, "fn": fn,
               "precision": round(precision, 6), "recall": round(recall, 6)}
        row["f1"] = round(2 * precision * recall / (precision + recall), 6) if precision + recall else 0.0
        if (best_effort is None or (row["f1"], row["precision"], row["recall"], threshold)
                > (best_effort["f1"], best_effort["precision"], best_effort["recall"], best_effort["threshold"])):
            best_effort = row
        if precision >= float(min_precision) and recall >= float(min_recall):
            eligible.append(row)
    if not eligible:
        return {"meets_targets": False, "threshold": None,
                "targets": {"precision": float(min_precision), "recall": float(min_recall)},
                "validation_metrics": best_effort, "positive_validation_samples": positive_count}
    selected = max(eligible, key=lambda row: (row["recall"], row["precision"], row["threshold"]))
    return {"meets_targets": True, "threshold": selected["threshold"],
            "targets": {"precision": float(min_precision), "recall": float(min_recall)},
            "validation_metrics": selected, "positive_validation_samples": positive_count}
