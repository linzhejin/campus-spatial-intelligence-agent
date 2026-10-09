"""FloodNet archive and manifest preparation helpers."""
from __future__ import annotations

import random
from pathlib import Path

from vision.floodnet_training import validate_floodnet_manifest


_LABELED_CLASSES = ("Flooded", "Non-Flooded")


def build_floodnet_manifest(root, *, seed: int = 20261010,
                            validation_fraction: float = 0.15,
                            test_fraction: float = 0.15) -> dict:
    """Create deterministic, class-stratified image-ID splits from labeled masks.

    FloodNet's rehosted archive only supplies pixel masks for its 398 labeled
    training images. It does not expose a flight/scene identifier, so this
    manifest explicitly uses image IDs and must not be described as flight-wise.
    """
    base = Path(root).resolve()
    dataset_root = base / "floodnet"
    if not dataset_root.is_dir():
        raise ValueError("dataset root must contain floodnet/Train/Labeled")
    fractions = (validation_fraction, test_fraction)
    if any(not isinstance(value, (int, float)) or not 0 < value < 1 for value in fractions):
        raise ValueError("validation and test fractions must be between zero and one")
    if validation_fraction + test_fraction >= 1:
        raise ValueError("validation and test fractions must sum to less than one")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")

    records = []
    split_groups = {"train": [], "validation": [], "test": []}
    used_group_ids = set()
    for class_index, flood_status in enumerate(_LABELED_CLASSES):
        class_root = dataset_root / "Train" / "Labeled" / flood_status
        image_dir = class_root / "image"
        mask_dir = class_root / "mask"
        if not image_dir.is_dir() or not mask_dir.is_dir():
            raise ValueError(f"missing labeled {flood_status} image or mask folder")
        images = sorted(path for path in image_dir.iterdir()
                        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"})
        masks = {path.stem.removesuffix("_lab"): path for path in mask_dir.glob("*_lab.png") if path.is_file()}
        if len(images) < 3:
            raise ValueError(f"at least three labeled {flood_status} images are required for three splits")
        image_stems = {image.stem for image in images}
        if image_stems != set(masks):
            missing = sorted(image_stems - set(masks))
            orphaned = sorted(set(masks) - image_stems)
            if missing:
                raise ValueError(f"missing mask for {flood_status} image: {missing[0]}")
            raise ValueError(f"orphan {flood_status} mask without image: {orphaned[0]}")

        shuffled = images.copy()
        random.Random(seed + class_index).shuffle(shuffled)
        test_count = max(1, round(len(shuffled) * test_fraction))
        validation_count = max(1, round(len(shuffled) * validation_fraction))
        while test_count + validation_count >= len(shuffled):
            if validation_count >= test_count:
                validation_count -= 1
            else:
                test_count -= 1
        assignments = {
            "test": shuffled[:test_count],
            "validation": shuffled[test_count:test_count + validation_count],
            "train": shuffled[test_count + validation_count:],
        }
        for split, split_images in assignments.items():
            for image in split_images:
                group_id = f"floodnet-image-{flood_status.lower()}-{image.stem}"
                if group_id in used_group_ids:
                    raise ValueError(f"duplicate image ID across labeled classes: {image.stem}")
                used_group_ids.add(group_id)
                group_parts = Path("Train") / "Labeled" / flood_status
                image_relative = (Path("floodnet") / group_parts / "image" / image.name).as_posix()
                mask_relative = (Path("floodnet") / group_parts / "mask" / masks[image.stem].name).as_posix()
                sample = {
                    "image": image_relative,
                    "mask": mask_relative,
                    "split": split,
                    "group_id": group_id,
                    "flood_status": flood_status,
                    "flooded_road_class_id": 3,
                    "road_surface_polygon": [[0, 0], [1, 0], [1, 1], [0, 1]],
                    "evaluation_roi_note": "full image; use the public pixel-level flooded_road class label",
                }
                records.append(sample)
                split_groups[split].append(group_id)

    manifest = {
        "schema_version": 1,
        "task": "flooded_road_segmentation",
        "dataset": "FloodNet-Supervised-v1.0",
        "source": "https://huggingface.co/datasets/torchgeo/floodnet",
        "original_source": "https://github.com/BinaLab/FloodNet-Supervised_v1.0",
        "license": "CDLA-Permissive-1.0",
        "split_unit": "labeled_image_id",
        "split_seed": seed,
        "class_split_strategy": "deterministic_stratified_by_flood_status",
        "pixel_alignment": {
            "rule": "resize RGB to paired categorical-mask grid when dimensions differ; never resize class-index masks",
            "basis": "FloodNet Track 1 organizer states that masks use the 4000x3000 ground-truth grid and evaluation resizes predictions to that grid",
            "reference": "https://competitions.codalab.org/forums/26986/5443/",
        },
        "limitations": [
            "Only the 398 pixel-labeled training images have segmentation masks in this archive.",
            "The public archive exposes no flight/scene grouping; disjoint image IDs do not prove flight-wise independence.",
            "Images depict post-Hurricane Harvey flooding, not Wuhan University shallow campus water.",
            "RGB/mask size mismatches are normalized to the official mask grid; this is the benchmark pixel frame, not a geographic orthorectification.",
        ],
        "split_groups": split_groups,
        "samples": records,
    }
    return validate_floodnet_manifest(manifest)
