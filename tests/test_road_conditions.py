"""
特殊路况模块测试（边绑定模型）

覆盖：
  - snap_to_edge：GCJ-02 点击点吸附到最近边、距离阈值、WKT geometry
  - 边绑定双向生效 + 路网 id 失效几何回退 + 旧 radius 圆模型兼容
  - CONDITION_EFFECTS 影响矩阵：5 类事件 × walk/bike/drive 的硬封/软惩罚
  - 时间窗（未开始/已结束不生效）
  - HTTP：snap/POST/PATCH/DELETE、session 与 Token 双通道鉴权
"""

import math
import time

import networkx as nx
import pytest

from spatial import road_conditions as rc
from spatial.coord_transform import wgs84_to_gcj02, gcj02_to_wgs84


# ===================== 合成路网 =====================
#
#   4 ────── 5 ─────── 6 ────── 7     北绕路（40/80/40m）
#   │         │         │         │
#   0 ────── 1 ─────── 2 ────── 3     南主路（40/80/40m）
#         40m      80m      40m
#
# 竖边 0-4、3-7 约 44m。主路总长 160m，绕行约 249m。
# 对中间 80m 边(1,2)：2× 后主路 240m 仍短于绕行；3× 后 320m 长于绕行 → 改走北路。

def _haversine_m(lng1, lat1, lng2, lat2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _make_graph():
    G = nx.MultiDiGraph()
    pts = {
        0: (114.36000, 30.53000),
        1: (114.36042, 30.53000),
        2: (114.36125, 30.53000),
        3: (114.36167, 30.53000),
        4: (114.36000, 30.53040),
        5: (114.36042, 30.53040),
        6: (114.36125, 30.53040),
        7: (114.36167, 30.53040),
    }
    for nid, (x, y) in pts.items():
        G.add_node(nid, x=x, y=y)
    edges = [
        (0, 1, "自强大道"), (1, 2, "自强大道"), (2, 3, "自强大道"),
        (4, 5, "北环路"), (5, 6, "北环路"), (6, 7, "北环路"),
        (0, 4, ""), (3, 7, ""),
    ]
    for u, v, name in edges:
        length = _haversine_m(pts[u][0], pts[u][1], pts[v][0], pts[v][1])
        attrs = {"length": length, "highway": "residential"}
        if name:
            attrs["name"] = name
        G.add_edge(u, v, 0, **attrs)
        G.add_edge(v, u, 0, **attrs)
    return G


@pytest.fixture
def G():
    return _make_graph()


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    """每个测试独立的路况 JSON，不污染真实 data/road_conditions.json。"""
    path = tmp_path / "road_conditions.json"
    monkeypatch.setattr(rc, "_CONDITIONS_FILE", str(path))
    monkeypatch.setattr(rc, "_cache", None)
    monkeypatch.setattr(rc, "_cache_mtime", 0.0)
    yield


def _edge_condition(G, cond_type, u=1, v=2, **overrides):
    """直接在边 (u,v) 中点吸附，构造一条绑定该边的事件。"""
    x1, y1 = G.nodes[u]["x"], G.nodes[u]["y"]
    x2, y2 = G.nodes[v]["x"], G.nodes[v]["y"]
    mid_lng, mid_lat = (x1 + x2) / 2, (y1 + y2) / 2
    click_gcj = wgs84_to_gcj02(mid_lng, mid_lat)
    snap = rc.snap_to_edge(G, click_gcj[0], click_gcj[1])
    assert snap is not None
    cond = rc.add_condition(
        cond_type=cond_type,
        name="测试-" + rc.CONDITION_LABELS[cond_type],
        edge=snap,
        click_point={"lng": click_gcj[0], "lat": click_gcj[1]},
    )
    cond.update(overrides)
    return cond


def _path_with_penalty(G, penalties, start=0, end=3):
    # networkx 3.x MultiDiGraph 可调用 weight 第三参是 {edge_key: attr_dict}
    def weight(u, v, edges_by_key):
        k, d = next(iter(edges_by_key.items()))
        return d["length"] * penalties.get((u, v, k), 1.0)
    return nx.dijkstra_path(G, start, end, weight=weight)


# ===================== snap =====================

class TestSnap:
    def test_snap_midpoint_to_edge(self, G):
        mid_lng = (G.nodes[1]["x"] + G.nodes[2]["x"]) / 2
        mid_lat = G.nodes[1]["y"]
        gj_lng, gj_lat = wgs84_to_gcj02(mid_lng, mid_lat)
        snap = rc.snap_to_edge(G, gj_lng, gj_lat)
        assert snap is not None
        assert {snap["u"], snap["v"]} == {1, 2}
        assert snap["road_name"] == "自强大道"
        assert snap["dist_m"] < 5  # 转换往返后基本落在边上
        assert len(snap["geometry_gcj"]) >= 2
        # snap 点必须真的在路上：转回 WGS 后到原边距离≈0
        wlng, wlat = gcj02_to_wgs84(snap["snap_lng_gcj"], snap["snap_lat_gcj"])
        assert _haversine_m(wlng, wlat, mid_lng, mid_lat) < 5

    def test_snap_rejects_far_click(self, G):
        # 中点北偏约 110m，超出 30m 阈值
        gj_lng, gj_lat = wgs84_to_gcj02(G.nodes[1]["x"], G.nodes[1]["y"] + 0.001)
        assert rc.snap_to_edge(G, gj_lng, gj_lat) is None

    def test_snap_works_with_wkt_geometry(self, G):
        """graphml 加载后边 geometry 是 WKT 字符串，吸附必须照常工作。"""
        G.edges[1, 2, 0]["geometry"] = (
            "LINESTRING (%s %s, %s %s)" % (
                G.nodes[1]["x"], G.nodes[1]["y"],
                G.nodes[2]["x"], G.nodes[2]["y"],
            )
        )
        G.edges[2, 1, 0]["geometry"] = G.edges[1, 2, 0]["geometry"]
        mid_lng = (G.nodes[1]["x"] + G.nodes[2]["x"]) / 2
        gj = wgs84_to_gcj02(mid_lng, G.nodes[1]["y"])
        snap = rc.snap_to_edge(G, gj[0], gj[1])
        assert snap is not None and {snap["u"], snap["v"]} == {1, 2}


# ===================== 影响矩阵 =====================

class TestEffectMatrix:
    @pytest.mark.parametrize("mode", ["walk", "bike", "drive"])
    def test_closure_blocks_all_modes_both_directions(self, G, mode):
        _edge_condition(G, "closure")
        G2, penalties, closed, applied = rc.apply_conditions_to_graph(G, [rc.list_conditions()[0]], mode)
        assert applied == 1
        assert not G2.has_edge(1, 2, 0)
        assert not G2.has_edge(2, 1, 0)  # 双向封闭
        # 主路断开 → 绕行北路
        path = nx.dijkstra_path(G2, 0, 3, weight="length")
        assert 2 not in path and 5 in path

    @pytest.mark.parametrize("mode", ["bike", "drive"])
    def test_construction_blocks_vehicles(self, G, mode):
        _edge_condition(G, "construction")
        G2, _, closed, _ = rc.apply_conditions_to_graph(G, [rc.list_conditions()[0]], mode)
        assert (1, 2, 0) in closed
        path = nx.dijkstra_path(G2, 0, 3, weight="length")
        assert 2 not in path

    def test_construction_walk_soft_4x_detours(self, G):
        """施工对步行是 4× 软惩罚（可穿但不优先）：80m 变 320m，主路长于绕行。"""
        _edge_condition(G, "construction")
        G2, penalties, closed, applied = rc.apply_conditions_to_graph(G, [rc.list_conditions()[0]], "walk")
        assert not closed and penalties[(1, 2, 0)] == 4.0 and applied == 1
        assert 5 in _path_with_penalty(G2, penalties)

    @pytest.mark.parametrize("mode", ["walk", "bike"])
    def test_flooding_blocks_pedestrian(self, G, mode):
        _edge_condition(G, "flooding")
        G2, _, closed, _ = rc.apply_conditions_to_graph(G, [rc.list_conditions()[0]], mode)
        assert (1, 2, 0) in closed

    def test_flooding_drive_4x_detours(self, G):
        """机动车可慢速通过积水：4× 软惩罚导致绕行。"""
        _edge_condition(G, "flooding")
        G2, penalties, closed, _ = rc.apply_conditions_to_graph(G, [rc.list_conditions()[0]], "drive")
        assert not closed and penalties[(1, 2, 0)] == 4.0
        assert 5 in _path_with_penalty(G2, penalties)

    @pytest.mark.parametrize("mode", ["walk", "bike"])
    def test_accident_3x_detours_pedestrian(self, G, mode):
        """事故对步行/骑行 3×：80m→240m，主路 320m > 绕行 249m。"""
        _edge_condition(G, "accident")
        G2, penalties, _, _ = rc.apply_conditions_to_graph(G, [rc.list_conditions()[0]], mode)
        assert penalties[(1, 2, 0)] == 3.0
        assert 5 in _path_with_penalty(G2, penalties)

    def test_accident_blocks_drive(self, G):
        _edge_condition(G, "accident")
        G2, _, closed, _ = rc.apply_conditions_to_graph(G, [rc.list_conditions()[0]], "drive")
        assert (1, 2, 0) in closed

    @pytest.mark.parametrize("mode", ["walk", "bike"])
    def test_event_2x_still_main_road(self, G, mode):
        """活动人流 2×：240m 仍短于绕行 249m，路径不变但该边成本翻倍。"""
        _edge_condition(G, "event")
        G2, penalties, closed, applied = rc.apply_conditions_to_graph(G, [rc.list_conditions()[0]], mode)
        assert applied == 1 and not closed and penalties[(1, 2, 0)] == 2.0
        path = _path_with_penalty(G2, penalties)
        assert path == [0, 1, 2, 3]

    def test_event_blocks_drive(self, G):
        _edge_condition(G, "event")
        G2, _, closed, _ = rc.apply_conditions_to_graph(G, [rc.list_conditions()[0]], "drive")
        assert (1, 2, 0) in closed

    def test_no_conditions_is_noop(self, G):
        G2, penalties, closed, applied = rc.apply_conditions_to_graph(G, [], "walk")
        assert G2 is G and penalties == {} and closed == set() and applied == 0


# ===================== 绑定解析 / 时间窗 / 兼容 =====================

class TestResolveAndLifecycle:
    def test_edge_id_invalid_falls_back_to_snap_point(self, G):
        """路网重建导致 u/v 失效时，用吸附点 12m 几何回退仍能命中。"""
        cond = _edge_condition(G, "closure")
        cond["edge"]["u"] = 999001
        cond["edge"]["v"] = 999002
        keys = rc._resolve_edge_keys(G, cond)
        assert {1, 2} in ({k[0], k[1]} for k in keys)
        assert len(keys) == 2  # 双向

    def test_legacy_radius_model_still_works(self, G):
        mid_lng = (G.nodes[1]["x"] + G.nodes[2]["x"]) / 2
        gj_lng, gj_lat = wgs84_to_gcj02(mid_lng, G.nodes[1]["y"])
        legacy = {
            "id": "legacy01", "type": "closure", "name": "旧版半径事件",
            "coordinates": {"lng": gj_lng, "lat": gj_lat}, "radius_m": 20.0,
            "start_time": 0, "end_time": 0,
        }
        keys = rc._resolve_edge_keys(G, legacy)
        assert (1, 2, 0) in keys and (2, 1, 0) in keys

    def test_scheduled_and_expired_inactive(self, G):
        future = time.time() + 86400
        _edge_condition(G, "closure", start_time=future)
        assert rc.list_conditions() == []
        assert rc.apply_conditions_to_graph(G, None, "walk")[3] == 0

        _edge_condition(G, "closure", start_time=time.time() - 100, end_time=time.time() - 10)
        assert rc.list_conditions() == []

    def test_end_event_sets_end_time(self, G):
        cond = _edge_condition(G, "accident")
        updated = rc.update_condition(cond["id"], {"end_time": time.time()})
        assert updated["end_time"] > 0
        assert rc.list_conditions() == []
        # 管理端仍可看到（含已结束）
        assert rc.list_conditions(include_inactive=True)[0]["id"] == cond["id"]

    def test_remove_condition(self, G):
        cond = _edge_condition(G, "closure")
        assert rc.remove_condition(cond["id"]) is True
        assert rc.remove_condition("nope") is False

    def test_add_rejects_unknown_type(self, G):
        snap = rc.snap_to_edge(G, *wgs84_to_gcj02(G.nodes[1]["x"], G.nodes[1]["y"]))
        with pytest.raises(ValueError):
            rc.add_condition("earthquake", "地震", edge=snap)


# ===================== HTTP 层 =====================

@pytest.fixture
def client(G, monkeypatch):
    import config
    monkeypatch.setattr(config, "ROAD_CONDITION_ADMIN_PASSWORD", "test-pw-123")
    monkeypatch.setattr(config, "ROAD_CONDITION_ADMIN_TOKEN", "test-token-xyz")
    from app import create_app
    import api.routes as routes
    monkeypatch.setattr(routes, "_ensure_network", lambda: (G, None))
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _mid_12_gcj(G):
    """边 (1,2) 中点的 GCJ-02 坐标（端点会同时相邻两条边，用中点避免歧义）。"""
    mid_lng = (G.nodes[1]["x"] + G.nodes[2]["x"]) / 2
    return wgs84_to_gcj02(mid_lng, G.nodes[1]["y"])


class TestRoadConditionAPI:
    def test_snap_requires_auth(self, client):
        r = client.get("/api/road-conditions/snap?lng=114.36&lat=30.53")
        assert r.status_code == 401

    def test_snap_with_token(self, client, G):
        gj = _mid_12_gcj(G)
        r = client.get(
            f"/api/road-conditions/snap?lng={gj[0]}&lat={gj[1]}",
            headers={"X-Admin-Token": "test-token-xyz"},
        )
        assert r.status_code == 200
        snap = r.get_json()["data"]["snap"]
        assert {snap["u"], snap["v"]} == {1, 2}

    def test_snap_wrong_token(self, client):
        r = client.get(
            "/api/road-conditions/snap?lng=114.36&lat=30.53",
            headers={"X-Admin-Token": "wrong"},
        )
        assert r.status_code == 401

    def test_snap_bearer_auth(self, client, G):
        gj = _mid_12_gcj(G)
        r = client.get(
            f"/api/road-conditions/snap?lng={gj[0]}&lat={gj[1]}",
            headers={"Authorization": "Bearer test-token-xyz"},
        )
        assert r.status_code == 200

    def test_post_binds_edge_no_radius(self, client, G):
        gj = _mid_12_gcj(G)
        r = client.post("/api/road-conditions", json={
            "type": "closure", "name": "接口封闭测试",
            "lng": gj[0], "lat": gj[1],
        }, headers={"X-Admin-Token": "test-token-xyz"})
        assert r.status_code == 201
        cond = r.get_json()["data"]["condition"]
        assert cond["edge"]["road_name"] == "自强大道"
        assert "radius_m" not in cond
        assert cond["created_by"] == "token"

    def test_post_too_far(self, client):
        r = client.post("/api/road-conditions", json={
            "type": "closure", "name": "湖里",
            "lng": 114.380, "lat": 30.545,  # 东湖里，远离路网
        }, headers={"X-Admin-Token": "test-token-xyz"})
        assert r.status_code == 400
        assert r.get_json()["error"] == "too_far_from_road"

    def test_post_requires_auth(self, client):
        r = client.post("/api/road-conditions", json={
            "type": "closure", "name": "x", "lng": 114.36, "lat": 30.53})
        assert r.status_code == 401

    def test_session_login_then_patch_end_and_delete(self, client, G):
        # 网页 session 登录
        assert client.post("/api/admin/login", json={"password": "test-pw-123"}).status_code == 200
        gj = _mid_12_gcj(G)
        cid = client.post("/api/road-conditions", json={
            "type": "accident", "name": "session事故",
            "lng": gj[0], "lat": gj[1],
        }).get_json()["data"]["condition"]["id"]
        # 立即结束
        r = client.patch(f"/api/road-conditions/{cid}", json={"action": "end"})
        assert r.status_code == 200 and r.get_json()["data"]["condition"]["end_time"] > 0
        # 普通视图看不到，管理 all 视图能看到
        assert client.get("/api/road-conditions").get_json()["data"]["count"] == 0
        assert client.get("/api/road-conditions?all=1").get_json()["data"]["count"] == 1
        # 删除
        assert client.delete(f"/api/road-conditions/{cid}").status_code == 200
        assert client.get("/api/road-conditions?all=1").get_json()["data"]["count"] == 0

    def test_public_list_shape(self, client, G):
        gj = _mid_12_gcj(G)
        client.post("/api/road-conditions", json={
            "type": "flooding", "name": "公开视图积水",
            "lng": gj[0], "lat": gj[1],
        }, headers={"X-Admin-Token": "test-token-xyz"})
        data = client.get("/api/road-conditions").get_json()["data"]
        assert data["count"] == 1
        cond = data["conditions"][0]
        assert cond["type_label"] == "积水"
        assert cond["edge"]["geometry_gcj"]  # 前端需要几何画线
