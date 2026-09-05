# -*- coding: utf-8 -*-
"""两阶段 POI 识别测试（V2 数据扩展配套）

覆盖：
  1. POI 数据完整性（pois.json 合并结果：数量、字段、坐标范围）
  2. 后端归一化 _normalize_poi_refs（别名 / 模糊 / 未匹配保留 / 类型不动）
  3. prompt 瘦身（不含 POI 列表，两阶段架构生效）
  4. 规则兜底在新 320+ POI 库下工作正常
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.parser import (
    _build_poi_name_index,
    _load_system_prompt,
    _normalize_poi_refs,
    _rule_based_classify,
    Constraints,
    PoiRef,
    TaskIntent,
)
from spatial.poi import find_poi, find_poi_candidates, load_pois

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POIS_PATH = os.path.join(PROJECT_ROOT, "data", "pois.json")

# 校园 bbox（config.WHU_BBOX 同值，硬编码避免 env 依赖）
BBOX = {"north": 30.5510, "south": 30.5170, "east": 114.3880, "west": 114.3460}


# ===== 1. POI 数据完整性 =====

class TestPoiDataIntegrity:
    @pytest.fixture(scope="class")
    def pois_data(self):
        with open(POIS_PATH, encoding="utf-8") as f:
            return json.load(f)

    def test_poi_count_expanded(self, pois_data):
        """V2 合并后 POI 数量应显著超过原 24 条。"""
        assert pois_data["count"] >= 300
        assert len(pois_data["pois"]) == pois_data["count"]

    def test_poi_required_fields(self, pois_data):
        for poi in pois_data["pois"]:
            assert poi.get("name"), f"POI 缺 name: {poi}"
            coords = poi.get("coordinates") or {}
            assert "lng" in coords and "lat" in coords, f"POI 缺坐标: {poi['name']}"

    def test_coords_in_campus_bbox(self, pois_data):
        """所有 POI 坐标应落在扩展后的三学部 bbox 内（南缘食堂等允许少量越界，
        实测最近路网节点吸附距离 ≤ 200m，路由可用）。"""
        tol = 0.005
        for poi in pois_data["pois"]:
            lng = poi["coordinates"]["lng"]
            lat = poi["coordinates"]["lat"]
            assert BBOX["west"] - tol <= lng <= BBOX["east"] + tol, \
                f"{poi['name']} 经度越界: {lng}"
            assert BBOX["south"] - tol <= lat <= BBOX["north"] + tol, \
                f"{poi['name']} 纬度越界: {lat}"

    def test_no_duplicate_names(self, pois_data):
        names = [p["name"] for p in pois_data["pois"]]
        assert len(names) == len(set(names)), "存在重复 POI 名称"

    def test_load_pois_matches_json(self, pois_data):
        assert len(load_pois()) == pois_data["count"]


# ===== 2. 后端地名归一化（两阶段·第二阶段） =====

class TestNormalizePoiRefs:
    def _intent(self, start=None, end=None, task_type="path_planning"):
        return TaskIntent(
            task_type=task_type,
            start=PoiRef(name=start, type="poi") if start else None,
            end=PoiRef(name=end, type="poi") if end else None,
            constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
        )

    def test_alias_normalized(self):
        """「图书馆」应归一化为「总图书馆」。"""
        intent = self._intent(start="教五", end="图书馆")
        result = _normalize_poi_refs(intent)
        assert result.end.name == "总图书馆"

    def test_fuzzy_prefix_normalized(self):
        """「武大牌坊」应归一化为「牌坊」。"""
        result = _normalize_poi_refs(self._intent(start="武大牌坊"))
        assert result.start.name == "牌坊"

    def test_exact_name_unchanged(self):
        result = _normalize_poi_refs(self._intent(start="樱顶"))
        assert result.start.name == "樱顶"

    def test_unmatched_keeps_raw(self):
        """匹配不到的地名保留原样（由 route 层引导用户）。"""
        raw = "不存在的地方xyz"
        result = _normalize_poi_refs(self._intent(start=raw))
        assert result.start.name == raw

    def test_none_refs_untouched(self):
        result = _normalize_poi_refs(self._intent())
        assert result.start is None and result.end is None

    def test_chat_intent_skipped(self):
        """非路径/查询意图不做归一化。"""
        intent = TaskIntent(
            task_type="chat",
            start=None,
            end=None,
            constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
        )
        assert _normalize_poi_refs(intent).task_type == "chat"

    def test_both_ends_normalized(self):
        result = _normalize_poi_refs(self._intent(start="图书馆", end="樱顶"))
        assert result.start.name == "总图书馆"
        assert result.end.name == "樱顶"


# ===== 3. prompt 瘦身（两阶段·第一阶段） =====

class TestPromptSlim:
    def test_prompt_has_no_poi_list(self):
        prompt = _load_system_prompt()
        assert "poi_list_json" not in prompt
        assert "{poi_count}" not in prompt

    def test_prompt_size_bounded(self):
        """不含 POI 列表的 prompt 应远小于注入 320 POI 时的体量。"""
        assert len(_load_system_prompt()) < 10000

    def test_prompt_has_verbatim_rule(self):
        """prompt 应包含「原样提取」指令。"""
        prompt = _load_system_prompt()
        assert "原样提取" in prompt


# ===== 4. 规则兜底 + POI 索引 =====

class TestRuleFallbackWithExpandedPois:
    def test_name_index_built(self):
        names_set, names_lower = _build_poi_name_index()
        assert len(names_set) >= 300
        assert "总图书馆" in names_set

    def test_index_contains_aliases(self):
        _, names_lower = _build_poi_name_index()
        # 「图书馆」可能是别名或模糊可达，索引至少应能查到规范名
        assert any("图书馆" in k for k in names_lower)

    def test_classify_path_query(self):
        r = _rule_based_classify("从牌坊到樱顶")
        assert r["task_type"] == "path_planning"
        assert r["start_name"] == "牌坊"
        assert r["end_name"] == "樱顶"

    def test_classify_poi_query(self):
        """规则层返回原文名（两阶段：归一化由 _normalize_poi_refs 完成）。"""
        r = _rule_based_classify("图书馆怎么走")
        assert r["task_type"] == "poi_query"
        assert r["start_name"] == "图书馆"

    def test_pipeline_rule_then_normalize(self):
        """管线级：规则提取原文名 → 归一化得规范名。"""
        r = _rule_based_classify("图书馆怎么走")
        intent = TaskIntent(
            task_type=r["task_type"],
            start=PoiRef(name=r["start_name"], type="poi"),
            end=None,
            constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
        )
        intent = _normalize_poi_refs(intent)
        assert intent.start.name == "总图书馆"

    def test_classify_new_campus_poi(self):
        """三学部扩展后的新 POI 应能被规则兜底匹配。"""
        r = _rule_based_classify("信息学部第一教学楼在哪")
        assert r["task_type"] == "poi_query"


# ===== 6. 校园边界（V2.1：修复校外 POI/穿城路线）=====

class TestCampusBoundary:
    def test_all_pois_inside_campus_polys(self):
        """所有 POI 必须落在三学部校园多边形内。"""
        import networkx as nx  # noqa: F401
        from config import CAMPUS_POLYS_GCJ

        def pip(lng, lat, poly):
            inside = False
            j = len(poly) - 1
            for i in range(len(poly)):
                xi, yi = poly[i]
                xj, yj = poly[j]
                if ((yi > lat) != (yj > lat)) and \
                        (lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi):
                    inside = not inside
                j = i
            return inside

        pois = load_pois()
        assert len(pois) >= 300
        for p in pois:
            lng, lat = p["coordinates"]["lng"], p["coordinates"]["lat"]
            assert any(pip(lng, lat, poly) for poly in CAMPUS_POLYS_GCJ.values()), \
                f"校外 POI 未清理: {p['name']} ({lng}, {lat})"

    def test_blacklist_pois_absent(self):
        from config import POI_NAME_BLACKLIST
        names = [p["name"] for p in load_pois()]
        for bad in POI_NAME_BLACKLIST:
            assert not any(bad in n for n in names), f"黑名单 POI 仍存在: {bad}"

    def test_known_offcampus_pois_removed(self):
        """已确认的校外点必须不存在。"""
        names = {p["name"] for p in load_pois()}
        for bad in ("珞珈山剧院", "武汉工学院", "东湖食堂", "国家网络安全学院大楼"):
            assert bad not in names, f"校外 POI 未删除: {bad}"

    def test_network_clipped_to_campus(self):
        """路网节点应全部在校园多边形（WGS-84）缓冲带内，且三校区连通。"""
        import networkx as nx
        from shapely.geometry import Point, Polygon
        from shapely.ops import unary_union
        from config import CAMPUS_POLYS_GCJ
        from spatial.coord_transform import gcj02_to_wgs84

        G = nx.read_graphml(
            os.path.join(PROJECT_ROOT, "data", "whu_road_network.graphml"),
            node_type=int,
        )
        polys = [
            Polygon([gcj02_to_wgs84(lng, lat) for lng, lat in poly])
            for poly in CAMPUS_POLYS_GCJ.values()
        ]
        clip = unary_union(polys).buffer(0.0011)
        outside = [
            n for n, d in G.nodes(data=True)
            if not clip.covers(Point(float(d["x"]), float(d["y"])))
        ]
        assert len(outside) == 0, f"{len(outside)} 个路网节点在校园缓冲带外"

        # 三校区同属一个大连通分量（跨学部过街连接未被裁断）
        largest = max(nx.weakly_connected_components(G), key=len)
        assert len(largest) / G.number_of_nodes() > 0.95


# ===== 5. 匹配质量（别名/消歧） =====

class TestMatchingQuality:
    def test_find_poi_candidates_returns_scored(self):
        cands = find_poi_candidates("图书馆", limit=5)
        assert cands, "「图书馆」应有候选"
        assert cands[0][0]["name"] == "总图书馆"
        scores = [s for _, s in cands]
        assert scores == sorted(scores, reverse=True)

    def test_garbage_returns_none(self):
        assert find_poi("zzz不存在的地点qqq") is None
