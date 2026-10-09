from PIL import Image
import numpy as np
import pytest

from vision.floodnet_data import build_floodnet_manifest


def _make_labeled_data(root, count=5):
    for label in ("Flooded", "Non-Flooded"):
        image_dir = root / "floodnet" / "Train" / "Labeled" / label / "image"
        mask_dir = root / "floodnet" / "Train" / "Labeled" / label / "mask"
        image_dir.mkdir(parents=True)
        mask_dir.mkdir(parents=True)
        for index in range(count):
            Image.fromarray(np.zeros((16, 16, 3), dtype=np.uint8)).save(image_dir / f"{label}-{index}.jpg")
            Image.fromarray(np.full((16, 16), 3 if label == "Flooded" else 4, dtype=np.uint8)).save(
                mask_dir / f"{label}-{index}_lab.png"
            )


def test_manifest_is_deterministic_stratified_and_honestly_image_split(tmp_path):
    _make_labeled_data(tmp_path)

    first = build_floodnet_manifest(tmp_path, seed=22, validation_fraction=0.2, test_fraction=0.2)
    second = build_floodnet_manifest(tmp_path, seed=22, validation_fraction=0.2, test_fraction=0.2)

    assert first == second
    assert first["split_unit"] == "labeled_image_id"
    assert first["pixel_alignment"]["rule"].startswith("resize RGB to paired categorical-mask grid")
    assert first["pixel_alignment"]["reference"].startswith("https://competitions.codalab.org/")
    assert len(first["samples"]) == 10
    for split in ("train", "validation", "test"):
        labels = {sample["flood_status"] for sample in first["samples"] if sample["split"] == split}
        assert labels == {"Flooded", "Non-Flooded"}
    assert all(sample["road_surface_polygon"] == [[0, 0], [1, 0], [1, 1], [0, 1]]
               for sample in first["samples"])


def test_manifest_fails_instead_of_silently_dropping_unpaired_masks(tmp_path):
    _make_labeled_data(tmp_path, count=3)
    mask = tmp_path / "floodnet" / "Train" / "Labeled" / "Flooded" / "mask" / "Flooded-0_lab.png"
    mask.unlink()

    with pytest.raises(ValueError, match="missing mask"):
        build_floodnet_manifest(tmp_path, seed=1, validation_fraction=0.2, test_fraction=0.2)
