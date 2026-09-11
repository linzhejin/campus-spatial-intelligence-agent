"""GPS 坐标型起终点（type="coord"，WGS-84）测试。

覆盖：
  1. _apply_coord_override：合法坐标覆盖、非法值静默忽略；
  2. _endpoint_to_wgs：coord 直通 WGS-84，poi 走 GCJ-02→WGS-84；
  3. /api/chat 端到端：coord_start + POI 终点能规划出路线（需本地路网缓存）。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agents.parser import Constraints, PoiRef, TaskIntent  # noqa: E402
from api.routes import _apply_coord_override, _endpoint_to_wgs  # noqa: E402


def _intent(**kw):
    base = dict(
        task_type="path_planning",
        start=None,
        end=None,
        constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
    )
    base.update(kw)
    return TaskIntent(**base)


# ---------------------------------------------------------------------------
# _apply_coord_override
# ---------------------------------------------------------------------------
class TestApplyCoordOverride:
    def test_valid_start_override(self):
        intent = _intent(end=PoiRef(name="樱顶", type="poi"))
        _apply_coord_override(intent, {"lng": 114.362, "lat": 30.541}, None)
        assert intent.start is not None
        assert intent.start.type == "coord"
        assert intent.start.name == "我的位置"
        assert intent.start.coordinates == {"lng": 114.362, "lat": 30.541}

    def test_valid_end_override(self):
        intent = _intent(start=PoiRef(name="珞珈门", type="poi"))
        _apply_coord_override(intent, None, {"lng": 114.36, "lat": 30.54, "name": "终点"})
        assert intent.end is not None
        assert intent.end.type == "coord"
        assert intent.end.name == "终点"

    def test_invalid_coord_ignored(self):
        intent = _intent(start=PoiRef(name="珞珈门", type="poi"))
        # 非数值 / 越界都不能冲掉原有 POI 起点
        _apply_coord_override(intent, {"lng": "x", "lat": 1.0}, None)
        _apply_coord_override(intent, {"lng": 200.0, "lat": 30.0}, None)
        _apply_coord_override(intent, None, None)
        assert intent.start is not None
        assert intent.start.type == "poi"
        assert intent.start.name == "珞珈门"
        assert intent.end is None

    def test_coordinates_rounded(self):
        intent = _intent()
        _apply_coord_override(intent, {"lng": 114.362999999, "lat": 30.541000001}, None)
        assert intent.start.coordinates["lng"] == 114.363
        assert intent.start.coordinates["lat"] == 30.541


# ---------------------------------------------------------------------------
# _endpoint_to_wgs
# ---------------------------------------------------------------------------
class TestEndpointToWgs:
    def test_coord_passthrough(self):
        ref = {"type": "coord", "coordinates": {"lng": 114.36, "lat": 30.54}}
        lng, lat = _endpoint_to_wgs(ref, {})
        assert (lng, lat) == (114.36, 30.54)

    def test_poi_gcj_to_wgs(self):
        from spatial.coord_transform import wgs84_to_gcj02

        wgs_lng, wgs_lat = 114.3628, 30.5388
        gcj_lng, gcj_lat = wgs84_to_gcj02(wgs_lng, wgs_lat)
        ref = {"type": "poi"}
        poi = {"lon": gcj_lng, "lat": gcj_lat}
        lng, lat = _endpoint_to_wgs(ref, poi)
        assert lng == pytest.approx(wgs_lng, abs=1e-5)
        assert lat == pytest.approx(wgs_lat, abs=1e-5)


# ---------------------------------------------------------------------------
# /api/chat 端到端（需本地路网缓存，避免 CI 无网时触发下载）
# ---------------------------------------------------------------------------
_NETWORK_CACHE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "whu_road_network.graphml")
)


@pytest.mark.skipif(not os.path.exists(_NETWORK_CACHE),
                    reason="本地无路网缓存，跳过 coord 端到端测试")
class TestChatCoordEndpoint:
    @pytest.fixture(scope="class")
    def client(self):
        from app import create_app
        app = create_app()
        app.config["TESTING"] = True
        return app.test_client()

    def test_coord_start_to_poi_end(self, client):
        """「我的位置」(珞珈门 WGS-84 坐标) → 樱顶：返回 coord 起点 + 非空路线。"""
        from spatial.coord_transform import gcj02_to_wgs84
        from spatial.poi import find_poi_ambiguous

        gate, _alts = find_poi_ambiguous("珞珈门")
        assert gate is not None
        wgs_lng, wgs_lat = gcj02_to_wgs84(gate["lon"], gate["lat"])

        resp = client.post("/api/chat", json={
            "query": "从我的位置到樱顶",
            "coord_start": {"lng": wgs_lng, "lat": wgs_lat},
        })
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()["data"]
        assert data["start"]["type"] == "coord"
        assert data["start"]["coordinates"]["lng"] == pytest.approx(wgs_lng, abs=1e-5)
        assert data["end"]["name"] in ("武汉大学老斋舍", "樱顶", "老斋舍")
        assert len(data["recommended"]) > 1

    def test_identical_coord_and_poi_rejected(self, client):
        """起点坐标与终点 POI 重合（<10m）→ same_poi。"""
        from spatial.coord_transform import gcj02_to_wgs84
        from spatial.poi import find_poi_ambiguous

        gate, _alts = find_poi_ambiguous("珞珈门")
        wgs_lng, wgs_lat = gcj02_to_wgs84(gate["lon"], gate["lat"])

        resp = client.post("/api/chat", json={
            "query": "从我的位置到珞珈门",
            "coord_start": {"lng": wgs_lng, "lat": wgs_lat},
        })
        # 解析层 end 取珞珈门；坐标差 <10m 应返回 400 same_poi
        if resp.status_code == 200:
            data = resp.get_json().get("data", {})
            # 规则解析未识别出同名终点时会走引导，也算可接受的非规划响应
            assert not data.get("recommended")
        else:
            assert resp.get_json()["error"] == "same_poi"
