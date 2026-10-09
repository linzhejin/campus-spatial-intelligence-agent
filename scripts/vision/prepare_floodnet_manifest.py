#!/usr/bin/env python3
"""Build an honest, stratified split manifest from FloodNet's labeled masks."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from vision.floodnet_data import build_floodnet_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, required=True,
                        help="extracted FloodNet root containing floodnet/Train/Labeled")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261010)
    parser.add_argument("--validation-fraction", type=float, default=0.15)
    parser.add_argument("--test-fraction", type=float, default=0.15)
    args = parser.parse_args()
    if not args.dataset_root.is_dir():
        parser.error("dataset root must be an existing directory")
    try:
        manifest = build_floodnet_manifest(
            args.dataset_root, seed=args.seed,
            validation_fraction=args.validation_fraction,
            test_fraction=args.test_fraction,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "dataset": manifest["dataset"],
        "split_unit": manifest["split_unit"],
        "samples": len(manifest["samples"]),
        "groups_by_split": {key: len(value) for key, value in manifest["split_groups"].items()},
        "limitations": manifest["limitations"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
