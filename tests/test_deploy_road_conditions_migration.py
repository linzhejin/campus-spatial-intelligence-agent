import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy" / "migrate_road_conditions.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("road_condition_migration", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_event():
    return {
        "id": "event-1",
        "type": "closure",
        "name": "测试封闭",
        "start_time": 0,
        "end_time": 0,
        "edge": {"u": 1, "v": 2, "key": 0},
    }


def test_migration_validates_and_atomically_installs_legacy_snapshot(tmp_path):
    module = _load_module()
    source = tmp_path / "legacy.json"
    target = tmp_path / "runtime" / "road_conditions.json"
    source.write_text(json.dumps([_valid_event()]), encoding="utf-8")

    assert module.migrate(source, target) == "migrated"
    assert json.loads(target.read_text(encoding="utf-8")) == [_valid_event()]
    assert json.loads(source.read_text(encoding="utf-8")) == [_valid_event()]


def test_migration_rejects_malformed_existing_runtime_instead_of_trusting_it(tmp_path):
    module = _load_module()
    source = tmp_path / "legacy.json"
    target = tmp_path / "runtime" / "road_conditions.json"
    source.write_text(json.dumps([_valid_event()]), encoding="utf-8")
    target.parent.mkdir(parents=True)
    target.write_text("[incomplete", encoding="utf-8")

    with pytest.raises(ValueError, match="运行路况文件无效"):
        module.migrate(source, target)
    assert target.read_text(encoding="utf-8") == "[incomplete"


def test_interrupted_install_never_leaves_partial_final_file_and_can_retry(tmp_path, monkeypatch):
    module = _load_module()
    source = tmp_path / "legacy.json"
    target = tmp_path / "runtime" / "road_conditions.json"
    source.write_text(json.dumps([_valid_event()]), encoding="utf-8")
    real_replace = module.os.replace
    monkeypatch.setattr(module.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("interrupted")))

    with pytest.raises(OSError, match="interrupted"):
        module.migrate(source, target)
    assert not target.exists()

    monkeypatch.setattr(module.os, "replace", real_replace)
    assert module.migrate(source, target) == "migrated"
    assert json.loads(target.read_text(encoding="utf-8")) == [_valid_event()]
