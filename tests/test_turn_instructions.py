# -*- coding: utf-8 -*-
"""转向指令 build_turn_by_turn 单元测试。"""
import networkx as nx
import pytest

from spatial.routing import (
    build_turn_by_turn,
    _classify_turn,
    _bearing,
    _human_distance,
)


# 1 度纬度 ≈ 111320m；经度方向乘 cos(30.54°)≈0.861
LNG_M = 111320 * 0.861
LAT_M = 111320.0


def _node(graph, nid, lng, lat):
    graph.add_node(nid, x=lng, y=lat)


def _edge(graph, u, v, length, name=None, highway="footway"):
    data = {"length": length, "highway": highway}
    if name is not None:
        data["name"] = name
    graph.add_edge(u, v, **data)


def _grid_graph():
    """
    十字形路网（角度为构造方便用近似米→度换算）：
        0 ── 1 ── 2
              │
              3
              │
              4 ── 5
    0→1→2 向东直行；1→3→4 向南；4→5 向东。
    """
    G = nx.MultiDiGraph()
    d_lng = 50.0 / LNG_M
    d_lat = 50.0 / LAT_M
    x0, y0 = 114.3600, 30.5400
    _node(G, 0, x0, y0)
    _node(G, 1, x0 + d_lng, y0)
    _node(G, 2, x0 + 2 * d_lng, y0)
    _node(G, 3, x0 + d_lng, y0 - d_lat)
    _node(G, 4, x0 + d_lng, y0 - 2 * d_lat)
    _node(G, 5, x0 + 2 * d_lng, y0 - 2 * d_lat)
    return G


class TestBearing:
    def test_cardinal_bearings(self):
        assert _bearing((0, 0), (0, 1)) == pytest.approx(0, abs=1e-6)       # 北
        assert _bearing((0, 0), (1, 0)) == pytest.approx(90, abs=1e-6)      # 东
        assert _bearing((0, 0), (0, -1)) == pytest.approx(180, abs=1e-6)    # 南
        assert _bearing((0, 0), (-1, 0)) == pytest.approx(270, abs=1e-6)    # 西


class TestClassifyTurn:
    def test_straight(self):
        assert _classify_turn(0, 5)[0] == "straight"
        assert _classify_turn(90, 95)[0] == "straight"

    def test_right_angles(self):
        # 北→东 = 右转 90
        assert _classify_turn(0, 90) == ("right", "右转")
        # 南→西 = 右转 90（顺时针）
        assert _classify_turn(180, 270)[0] == "right"
        # 北→西 = 左转 90
        assert _classify_turn(0, 270)[0] == "left"
        # 东→北 = 左转 90
        assert _classify_turn(90, 0)[0] == "left"

    def test_slight_and_sharp_and_uturn(self):
        assert _classify_turn(0, 15)[0] == "slight_right"
        assert _classify_turn(0, -15)[0] == "slight_left"
        assert _classify_turn(0, 150)[0] == "sharp_right"
        assert _classify_turn(0, -150)[0] == "sharp_left"
        assert _classify_turn(0, 180)[0] == "uturn"


class TestHumanDistance:
    def test_rounding(self):
        assert _human_distance(8) == "马上"
        assert _human_distance(22) == "约20米"
        assert _human_distance(48) == "约50米"
        assert _human_distance(103) == "约100米"
        assert _human_distance(127) == "约130米"


class TestBuildTurnByTurn:
    def test_empty_and_single_node(self):
        assert build_turn_by_turn(nx.MultiDiGraph(), []) == []

    def test_straight_same_road_no_turn(self):
        G = _grid_graph()
        _edge(G, 0, 1, 50, "求是一路")
        _edge(G, 1, 2, 50, "求是一路")
        steps = build_turn_by_turn(G, [0, 1, 2], end_name="信图")
        types = [s["type"] for s in steps]
        # 同名道路合并 → 只有出发 + 到达，没有 turn
        assert types == ["depart", "arrive"]
        assert steps[0]["text"] == "出发，沿求是一路前行"
        assert steps[0]["distance_m"] == pytest.approx(100, abs=0.5)
        assert steps[-1]["text"] == "到达终点（信图），导航结束"
        # 累计距离等于全长
        assert steps[-1]["cumulative_m"] == pytest.approx(100, abs=0.5)

    def test_right_angle_right_turn_with_road_change(self):
        G = _grid_graph()
        _edge(G, 0, 1, 50, "求是一路")
        _edge(G, 1, 3, 50, "求是二路")
        _edge(G, 3, 4, 50, "求是二路")
        steps = build_turn_by_turn(G, [0, 1, 3, 4])
        turn = [s for s in steps if s["type"] == "turn"]
        assert len(turn) == 1
        t = turn[0]
        # 向东（90°）→ 向南（180°）= 右转
        assert t["action"] == "right"
        assert "右转" in t["text"] and "求是二路" in t["text"]
        assert t["road_name"] == "求是一路"
        assert t["next_road_name"] == "求是二路"
        # 动作点在节点 1
        assert t["point"]["lng"] == pytest.approx(G.nodes[1]["x"], abs=1e-6)
        assert t["point"]["lat"] == pytest.approx(G.nodes[1]["y"], abs=1e-6)

    def test_left_turn(self):
        G = _grid_graph()
        # 2→1 向西（270°）→ 1→3 向南（180°）= 左转；3→4 继续向南同名合并
        _edge(G, 2, 1, 50, "求是一路")
        _edge(G, 1, 3, 50, "求是二路")
        _edge(G, 3, 4, 50, "求是二路")
        steps = build_turn_by_turn(G, [2, 1, 3, 4])
        turn = [s for s in steps if s["type"] == "turn"]
        assert len(turn) == 1
        assert turn[0]["action"] == "left"
        assert "左转" in turn[0]["text"]

    def test_unnamed_road_shown_as_xiaolu(self):
        G = _grid_graph()
        _edge(G, 0, 1, 50)  # 无名
        _edge(G, 1, 2, 50)
        steps = build_turn_by_turn(G, [0, 1, 2])
        assert steps[0]["road_name"] == "小路"
        assert "小路" in steps[0]["text"]

    def test_unnamed_placeholder_treated_as_unnamed(self):
        G = _grid_graph()
        _edge(G, 0, 1, 50, "未命名路 0-1")
        _edge(G, 1, 2, 50, "未命名路 1-2")
        steps = build_turn_by_turn(G, [0, 1, 2])
        assert steps[0]["road_name"] == "小路"

    def test_road_name_change_while_straight(self):
        G = _grid_graph()
        # 0→1→2 直线向东，但路名变化：直行进入新路
        _edge(G, 0, 1, 50, "求是一路")
        _edge(G, 1, 2, 50, "求是大道")
        steps = build_turn_by_turn(G, [0, 1, 2])
        turn = [s for s in steps if s["type"] == "turn"]
        assert len(turn) == 1
        assert turn[0]["action"] == "straight"
        assert "直行" in turn[0]["text"] and "求是大道" in turn[0]["text"]

    def test_cumulative_distances_monotonic(self):
        G = _grid_graph()
        _edge(G, 0, 1, 50, "求是一路")
        _edge(G, 1, 3, 60, "求是二路")
        _edge(G, 3, 4, 70, "求是二路")
        _edge(G, 4, 5, 40, "求是三路")
        steps = build_turn_by_turn(G, [0, 1, 3, 4, 5])
        cums = [s["cumulative_m"] for s in steps]
        assert cums == sorted(cums)
        assert cums[-1] == pytest.approx(220, abs=0.5)
        # 0→1 右转（东→南），4→5 左转（南→东）
        actions = [s["action"] for s in steps if s["type"] == "turn"]
        assert actions == ["right", "left"]
