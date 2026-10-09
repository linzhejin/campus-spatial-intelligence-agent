import pytest

from vision.specialized import AIDER_CLASSES
from vision.aider_training import (
    build_aider_manifest,
    choose_accident_threshold,
    validate_aider_training_manifest,
)


def _dataset(root):
    for class_index, label in enumerate(AIDER_CLASSES):
        class_dir = root / "AIDER" / label
        class_dir.mkdir(parents=True)
        for image_index in range(5):
            (class_dir / f"image-{image_index}.jpg").write_bytes(
                f"{label}-{image_index}".encode("ascii"),
            )


def test_aider_manifest_is_deterministic_stratified_and_test_only_compatible(tmp_path):
    _dataset(tmp_path)

    first = build_aider_manifest(tmp_path, seed=17)
    second = build_aider_manifest(tmp_path, seed=17)

    assert first == second
    assert first["class_order"] == list(AIDER_CLASSES)
    assert first["split_unit"] == "exact_image_hash_cluster"
    assert all({row["label"] for row in first["samples"] if row["split"] == split} == set(AIDER_CLASSES)
               for split in ("train", "validation", "test"))
    assert validate_aider_training_manifest(first) == first


def test_identical_image_bytes_never_cross_aider_splits(tmp_path):
    _dataset(tmp_path)
    duplicate = tmp_path / "AIDER" / AIDER_CLASSES[0] / "image-duplicate.jpg"
    duplicate.write_bytes((tmp_path / "AIDER" / AIDER_CLASSES[0] / "image-0.jpg").read_bytes())

    manifest = build_aider_manifest(tmp_path, seed=11)
    copies = [sample for sample in manifest["samples"]
              if sample["group_id"] == manifest["samples"][0]["group_id"]]
    assert len(copies) == 2
    assert len({sample["split"] for sample in copies}) == 1


def test_conflicting_cross_class_exact_duplicates_are_excluded(tmp_path):
    _dataset(tmp_path)
    first = tmp_path / "AIDER" / AIDER_CLASSES[0] / "image-0.jpg"
    conflict = tmp_path / "AIDER" / AIDER_CLASSES[1] / "mislabelled-copy.jpg"
    conflict.write_bytes(first.read_bytes())

    manifest = build_aider_manifest(tmp_path, seed=13)

    assert len(manifest["excluded_cross_class_exact_duplicates"]) == 1
    assert first.relative_to(tmp_path).as_posix() not in {row["image"] for row in manifest["samples"]}
    assert conflict.relative_to(tmp_path).as_posix() not in {row["image"] for row in manifest["samples"]}


def test_aider_manifest_rejects_duplicate_groups_across_splits(tmp_path):
    _dataset(tmp_path)
    manifest = build_aider_manifest(tmp_path, seed=19)
    sample = manifest["samples"][0]
    other_split = next(split for split in ("train", "validation", "test")
                       if split != sample["split"])
    manifest["split_groups"][other_split].append(sample["group_id"])

    with pytest.raises(ValueError, match="overlap"):
        validate_aider_training_manifest(manifest)


def test_accident_threshold_requires_both_precision_and_recall_targets():
    labels = [True, True, False, False]
    probabilities = [0.95, 0.70, 0.90, 0.10]

    achievable = choose_accident_threshold(
        labels, probabilities, min_precision=0.9, min_recall=0.5,
    )
    impossible = choose_accident_threshold(
        labels, probabilities, min_precision=0.9, min_recall=0.8,
    )

    assert achievable["meets_targets"] is True
    assert achievable["validation_metrics"]["precision"] == 1.0
    assert achievable["validation_metrics"]["recall"] == 0.5
    assert impossible["meets_targets"] is False
    assert impossible["threshold"] is None


def test_accident_threshold_rejects_invalid_or_empty_validation_data():
    with pytest.raises(ValueError, match="equal non-empty"):
        choose_accident_threshold([], [])
    with pytest.raises(ValueError, match="between 0 and 1"):
        choose_accident_threshold([True], [1.2])
