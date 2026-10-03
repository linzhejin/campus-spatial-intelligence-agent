#!/usr/bin/env python3
"""Validate and atomically install the persistent road-condition snapshot."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from spatial.road_conditions import _validate_condition_record


def _read_valid(path: Path, *, runtime: bool) -> list[dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ValueError("路况文件必须是对象数组")
        for item in value:
            _validate_condition_record(item)
        return value
    except (OSError, json.JSONDecodeError, ValueError) as error:
        label = "运行路况文件无效" if runtime else "历史路况文件无效"
        raise ValueError(f"{label}: {path}") from error


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def migrate(source: Path | str, target: Path | str) -> str:
    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        _read_valid(target, runtime=True)
        return "validated"

    records = _read_valid(source, runtime=False) if source.is_file() else []
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=target.parent,
            prefix=".road-conditions-migrate-", suffix=".json", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(records, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        _read_valid(temporary, runtime=True)
        os.replace(temporary, target)
        temporary = None
        _sync_directory(target.parent)
        _read_valid(target, runtime=True)
        return "migrated"
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: migrate_road_conditions.py SOURCE TARGET", file=sys.stderr)
        return 2
    print(migrate(argv[1], argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
