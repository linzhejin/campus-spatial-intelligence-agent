import importlib.util
import io
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_poi_catalog_does_not_publish_ratings_without_item_level_provenance():
    catalog = json.loads((ROOT / "data" / "pois.json").read_text(encoding="utf-8"))

    for poi in catalog["pois"]:
        if poi.get("rating") is not None:
            assert poi.get("rating_source"), poi["id"]
            assert poi.get("rating_updated_at"), poi["id"]


def test_legacy_amap_rating_script_fails_closed_without_rewriting_catalog(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "legacy_amap_ratings", ROOT / "scripts" / "fetch" / "fetch_ratings_amap.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    writes = []

    def fake_open(_path, mode="r", encoding=None):
        if "w" in mode:
            writes.append(_path)
            return io.StringIO()
        return io.StringIO('{"count":0,"pois":[]}')

    monkeypatch.setattr(module, "open", fake_open, raising=False)

    with pytest.raises(SystemExit) as exc:
        module.main()
    assert "不能写回" in str(exc.value)
    assert writes == []
