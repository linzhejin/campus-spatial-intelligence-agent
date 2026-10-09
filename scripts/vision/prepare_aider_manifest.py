#!/usr/bin/env python3
"""Create a reproducible, duplicate-aware AIDER train/validation/test manifest."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from vision.aider_training import AIDER_PROPORTIONAL_SPLITS, build_aider_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, required=True,
                        help="extracted directory containing AIDER/<class>/*.jpg")
    parser.add_argument("--output", type=Path, required=True,
                        help="write the local manifest outside the source repository")
    parser.add_argument("--seed", type=int, default=20261010)
    parser.add_argument("--split-protocol", choices=("takunet-proportional", "stratified-60-20-20"),
                        default="takunet-proportional")
    args = parser.parse_args()
    if not args.dataset_root.is_dir():
        parser.error("dataset-root must be an existing directory")
    try:
        args.output.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        parser.error("output must be outside the source repository")
    expected = (AIDER_PROPORTIONAL_SPLITS
                if args.split_protocol == "takunet-proportional" else None)
    try:
        manifest = build_aider_manifest(
            args.dataset_root, seed=args.seed, expected_split_counts=expected,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = {split: sum(row["split"] == split for row in manifest["samples"])
              for split in ("train", "validation", "test")}
    print(json.dumps({"manifest": str(args.output), "samples": len(manifest["samples"]),
                      "license_status": manifest["license_status"],
                      "split_counts": counts, "class_split_counts": manifest["class_split_counts"],
                      "sha256_groups": {split: len(manifest["split_groups"][split])
                                        for split in manifest["split_groups"]},
                      "limitations": manifest["limitations"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
