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
#            ╱           ╲
#           8             9          死胡同（10m，让 1/2 成为度=3 路口，
#                                      边链扩展在此停止；不构成绕行捷径）
#
# 竖边 0-4、3-7 约 44m。主路总长 160m，绕行约 249m。
# 对中间 80m 边(1,2)：新软惩罚 ≤1.5×，主路成本 160+80×1.5=280 仍 < 绕行 249？
# 实测约 40+80×1.5+40=200 远小于绕行 248，所以软惩罚下仍走主路（虚线=可通行）。
# 只有硬封(closure)才导致绕行。

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
        # 死胡同叶子：在节点 1、2 正南 10m
        8: (114.36042, 30.52991),
        9: (114.36125, 30.52991),
    }
    for nid, (x, y) in pts.items():
        G.add_node(nid, x=x, y=y)
    edges = [
        (0, 1, "自强大道"), (1, 2, "自强大道"), (2, 3, "自强大道"),
        (4, 5, "北环路"), (5, 6, "北环路"), (6, 7, "北环路"),
        (0, 4, ""), (3, 7, ""),
        (1, 8, ""), (2, 9, ""),
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

    def test_construction_walk_soft_1_5x_still_main_road(self, G):
        """施工对步行 1.5× 软惩罚：成本增加但主路仍比绕行短，路径不变（虚线=可通行）。"""
        _edge_condition(G, "construction")
        G2, penalties, closed, applied = rc.apply_conditions_to_graph(G, [rc.list_conditions()[0]], "walk")
        assert not closed and abs(penalties[(1, 2, 0)] - 1.5) < 1e-6 and applied == 1
        assert _path_with_penalty(G2, penalties) == [0, 1, 2, 3]

    @pytest.mark.parametrize("mode", ["walk", "bike"])
    def test_flooding_blocks_pedestrian(self, G, mode):
        _edge_condition(G, "flooding")
        G2, _, closed, _ = rc.apply_conditions_to_graph(G, [rc.list_conditions()[0]], mode)
        assert (1, 2, 0) in closed

    def test_flooding_drive_soft_1_5x_still_main_road(self, G):
        """机动车可慢速通过积水：1.5× 软惩罚但路径不变（主路仍短于绕行）。"""
        _edge_condition(G, "flooding")
        G2, penalties, closed, _ = rc.apply_conditions_to_graph(G, [rc.list_conditions()[0]], "drive")
        assert not closed and abs(penalties[(1, 2, 0)] - 1.5) < 1e-6
        assert _path_with_penalty(G2, penalties) == [0, 1, 2, 3]

    @pytest.mark.parametrize("mode", ["walk", "bike"])
    def test_accident_soft_1_3x_still_main_road(self, G, mode):
        """事故对步行/骑行 1.3× 软惩罚：成本微增，路径不变（虚线=可通行）。"""
        _edge_condition(G, "accident")
        G2, penalties, _, _ = rc.apply_conditions_to_graph(G, [rc.list_conditions()[0]], mode)
        assert abs(penalties[(1, 2, 0)] - 1.3) < 1e-6
        assert _path_with_penalty(G2, penalties) == [0, 1, 2, 3]

    def test_accident_blocks_drive(self, G):
        _edge_condition(G, "accident")
        G2, _, closed, _ = rc.apply_conditions_to_graph(G, [rc.list_conditions()[0]], "drive")
        assert (1, 2, 0) in closed

    @pytest.mark.parametrize("mode", ["walk", "bike"])
    def test_event_soft_1_2x_still_main_road(self, G, mode):
        """活动人流 1.2× 软惩罚：成本几乎不变，路径不变。"""
        _edge_condition(G, "event")
        G2, penalties, closed, applied = rc.apply_conditions_to_graph(G, [rc.list_conditions()[0]], mode)
        assert applied == 1 and not closed and abs(penalties[(1, 2, 0)] - 1.2) < 1e-6
        assert _path_with_penalty(G2, penalties) == [0, 1, 2, 3]

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
        """路网重建导致边链 id 全部失效时，用整链几何 12m 回退仍能命中。"""
        cond = _edge_condition(G, "closure")
        cond["edge"]["u"] = 999001
        cond["edge"]["v"] = 999002
        cond["edge"]["edges"] = []  # 链上 id 全部失效，触发几何回退
        keys = rc._resolve_edge_keys(G, cond)
        assert (1, 2, 0) in keys and (2, 1, 0) in keys

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


# ===================== 边链扩展（路口到路口整段） =====================

def _straight_road_graph(n_seg=4, names=None, start_id=10, extra_edges=None,
                         base=(114.37000, 30.54000), step_deg=0.00055):
    """水平直路 start_id … start_id+n_seg，每段约 53m（双向边）。

    extra_edges: [(a, b, name)] 在已有节点 a 上接一条向北的岔路到新节点 b。
    """
    G = nx.MultiDiGraph()
    ids = list(range(start_id, start_id + n_seg + 1))
    for nid in ids:
        i = nid - start_id
        G.add_node(nid, x=base[0] + step_deg * i, y=base[1])

    def _add_bidir(a, b, name):
        length = _haversine_m(G.nodes[a]["x"], G.nodes[a]["y"],
                              G.nodes[b]["x"], G.nodes[b]["y"])
        attrs = {"length": length, "highway": "residential"}
        if name:
            attrs["name"] = name
        G.add_edge(a, b, 0, **attrs)
        G.add_edge(b, a, 0, **attrs)

    if names is None:
        names = ["樱花大道"] * n_seg
    for i in range(n_seg):
        _add_bidir(ids[i], ids[i + 1], names[i])
    if extra_edges:
        for a, b, name in extra_edges:
            if b not in G:
                G.add_node(b, x=G.nodes[a]["x"], y=G.nodes[a]["y"] + step_deg)
            _add_bidir(a, b, name)
    return G, ids


def _undirected_edge_set(triples):
    return {frozenset((t[0], t[1])) for t in triples}


def _chain_condition(G, u, v, cond_type="closure"):
    x1, y1 = G.nodes[u]["x"], G.nodes[u]["y"]
    x2, y2 = G.nodes[v]["x"], G.nodes[v]["y"]
    click = wgs84_to_gcj02((x1 + x2) / 2, (y1 + y2) / 2)
    snap = rc.snap_to_edge(G, click[0], click[1])
    assert snap is not None
    return snap, rc.add_condition(
        cond_type=cond_type, name="链测试", edge=snap,
        click_point={"lng": click[0], "lat": click[1]},
    )


class TestEdgeChain:
    def test_chain_extends_through_degree2_to_both_ends(self):
        """直路中间边：链穿过所有 degree=2 节点，延伸到两端断头。"""
        G, ids = _straight_road_graph(n_seg=4)
        snap, _ = _chain_condition(G, 12, 13)
        assert _undirected_edge_set(snap["edges"]) == _undirected_edge_set(
            [(10, 11), (11, 12), (12, 13), (13, 14)]
        )
        assert len(snap["edges"]) == 4
        assert 200 < snap["chain_length_m"] < 225
        # 合并几何覆盖道路两端
        first = snap["geometry_gcj"][0]
        last = snap["geometry_gcj"][-1]
        g10 = wgs84_to_gcj02(G.nodes[10]["x"], G.nodes[10]["y"])
        g14 = wgs84_to_gcj02(G.nodes[14]["x"], G.nodes[14]["y"])
        assert abs(first[0] - g10[0]) < 1e-6 and abs(first[1] - g10[1]) < 1e-6
        assert abs(last[0] - g14[0]) < 1e-6 and abs(last[1] - g14[1]) < 1e-6

    def test_chain_stops_at_intersection(self):
        """端点是路口（无向度=3）时链停止，不串到岔路。"""
        G, ids = _straight_road_graph(n_seg=4, extra_edges=[(12, 20, "岔路")])
        snap, _ = _chain_condition(G, 11, 12)
        assert _undirected_edge_set(snap["edges"]) == _undirected_edge_set(
            [(10, 11), (11, 12)]
        )

    def test_chain_stops_on_road_name_change(self):
        """degree=2 直连点处道路名变化即停止（不串到另一条路）。"""
        G, ids = _straight_road_graph(
            n_seg=4, names=["樱花大道", "梅园路", "梅园路", "梅园路"]
        )
        snap, _ = _chain_condition(G, 10, 11)
        assert _undirected_edge_set(snap["edges"]) == _undirected_edge_set([(10, 11)])

    def test_chain_stops_on_sharp_turn(self):
        """degree=2 但相邻边夹角 >100°（急弯折返）时停止。"""
        G = nx.MultiDiGraph()
        G.add_node(30, x=114.37000, y=30.54000)
        G.add_node(31, x=114.37105, y=30.54000)   # 30→31 向东
        G.add_node(32, x=114.37032, y=30.53900)   # 31→32 向西南（夹角约 149°）
        for a, b in [(30, 31), (31, 32)]:
            length = _haversine_m(G.nodes[a]["x"], G.nodes[a]["y"],
                                  G.nodes[b]["x"], G.nodes[b]["y"])
            for u, v in ((a, b), (b, a)):
                G.add_edge(u, v, 0, length=length, highway="residential", name="环山道")
        snap, _ = _chain_condition(G, 30, 31)
        assert _undirected_edge_set(snap["edges"]) == _undirected_edge_set([(30, 31)])

    def test_chain_respects_600m_cap(self):
        """约 800m 直路：600m 预算下主边两侧各只延伸 2 段，整链 5 段。"""
        G, ids = _straight_road_graph(n_seg=8, step_deg=0.00105)  # 每段约 100m
        snap, _ = _chain_condition(G, 14, 15)
        assert _undirected_edge_set(snap["edges"]) == _undirected_edge_set(
            [(12, 13), (13, 14), (14, 15), (15, 16), (16, 17)]
        )
        assert snap["chain_length_m"] <= 601.0

    def test_resolve_edge_keys_covers_full_chain_both_directions(self):
        """规划期解析：链上每条边双向全部命中。"""
        G, ids = _straight_road_graph(n_seg=4)
        _, cond = _chain_condition(G, 12, 13)
        keys = rc._resolve_edge_keys(G, cond)
        assert len(keys) == 8
        for a, b in [(10, 11), (11, 12), (12, 13), (13, 14)]:
            assert (a, b, 0) in keys and (b, a, 0) in keys

    def test_persisted_condition_contains_chain(self):
        G, ids = _straight_road_graph(n_seg=4)
        _, cond = _chain_condition(G, 12, 13)
        edge = cond["edge"]
        assert [12, 13, 0] in edge["edges"]
        assert len(edge["edges"]) == 4
        assert edge["chain_length_m"] > 200
        assert len(edge["geometry_gcj"]) >= 5

    def test_chain_geometry_fallback_after_node_id_rebuild(self):
        """路网重建后 id 全变：靠整链几何回退仍能命中新图上整段（双向）。"""
        G1, _ = _straight_road_graph(n_seg=4, start_id=10)
        _, cond = _chain_condition(G1, 12, 13)
        G2, _ = _straight_road_graph(n_seg=4, start_id=1010)  # 坐标完全相同、id 全换
        keys = rc._resolve_edge_keys(G2, cond)
        assert len(keys) == 8
        for a, b in [(1010, 1011), (1011, 1012), (1012, 1013), (1013, 1014)]:
            assert (a, b, 0) in keys and (b, a, 0) in keys

    def test_legacy_condition_without_edges_list_still_resolves(self, G):
        """旧数据（只有 u/v、无 edges 链）继续按单条边双向解析。"""
        legacy = {
            "id": "old01", "type": "closure", "name": "旧单条边事件",
            "edge": {"u": 1, "v": 2, "key": 0, "snap": {"lng": 0, "lat": 0}},
        }
        keys = rc._resolve_edge_keys(G, legacy)
        assert keys == {(1, 2, 0), (2, 1, 0)}


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
