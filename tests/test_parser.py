import pytest
from unittest.mock import patch, MagicMock

from agents.parser import (
    TaskIntent,
    PoiRef,
    Constraints,
    _extract_json,
    _rule_based_classify,
    _merge_context_with_intent,
    _resolve_ambiguity_completion,
    _t011_post_process,
    _annotate_weight_source,
    detect_travel_mode,
)


class TestTaskIntentInstantiation:
    def test_task_intent_valid_path_planning(self):
        intent = TaskIntent(
            task_type="path_planning",
            start=PoiRef(name="珞珈门", type="poi"),
            end=PoiRef(name="樱顶", type="poi"),
            constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
            weights=None,
            input_method="nl",
            ambiguity=None,
        )
        assert intent.task_type == "path_planning"
        assert intent.start.name == "珞珈门"
        assert intent.end.name == "樱顶"
        assert intent.constraints.distance == "medium"

    def test_task_intent_weight_source_annotation(self):
        intent = TaskIntent(
            task_type="path_planning",
            start=PoiRef(name="珞珈门", type="poi"),
            end=PoiRef(name="樱顶", type="poi"),
            constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
            weights={"distance": 0.5, "slope": 0.2, "scenery": 0.3},
            input_method="nl",
        )
        intent = _annotate_weight_source(intent)
        assert intent.weight_source == "explicit_nl"

    def test_task_intent_shortcut_weight_source(self):
        intent = TaskIntent(
            task_type="path_planning",
            start=PoiRef(name="珞珈门", type="poi"),
            end=PoiRef(name="樱顶", type="poi"),
            constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
            weights=None,
            input_method="shortcut",
        )
        intent = _annotate_weight_source(intent)
        assert intent.weight_source == "shortcut"


class TestFourTaskTypes:
    @pytest.mark.parametrize("query,expected_type", [
        ("从牌坊到樱顶", "path_planning"),
        ("樱顶在哪里", "poi_query"),
        ("你能做什么", "help"),
        ("今天天气怎么样", "chat"),
    ])
    def test_rule_based_classify_four_types(self, query, expected_type):
        result = _rule_based_classify(query)
        if expected_type == "path_planning":
            assert result["task_type"] is None or result["task_type"] == "path_planning"
        else:
            assert result["task_type"] == expected_type

    def test_post_process_poi_query(self):
        base_intent = TaskIntent(
            task_type="path_planning",
            start=None,
            end=None,
            constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
        )
        processed = _t011_post_process(base_intent, "樱顶在哪里", None)
        assert processed.task_type == "poi_query"
        assert processed.start is not None
        assert processed.start.name == "武汉大学老斋舍"
        assert processed.end is None

    def test_post_process_help(self):
        base_intent = TaskIntent(
            task_type="path_planning",
            start=None,
            end=None,
            constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
        )
        processed = _t011_post_process(base_intent, "你能做什么", None)
        assert processed.task_type == "help"

    def test_post_process_unknown(self):
        base_intent = TaskIntent(
            task_type="path_planning",
            start=None,
            end=None,
            constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
        )
        # 真正无关的闲聊（股票）→ chat，不再 unknown 报错
        processed = _t011_post_process(base_intent, "今天股票怎么样", None)
        assert processed.task_type == "chat"
        assert processed.ambiguity is None

    def test_post_process_chat(self):
        """校园相关闲聊 → chat，不是 unknown"""
        base_intent = TaskIntent(
            task_type="path_planning",
            start=None,
            end=None,
            constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
        )
        processed = _t011_post_process(base_intent, "今天天气怎么样", None)
        assert processed.task_type == "chat"
        assert processed.start is None
        assert processed.end is None


class TestExtractJsonFallback:
    def test_extract_json_clean(self):
        text = '{"task_type":"path_planning"}'
        result = _extract_json(text)
        assert result["task_type"] == "path_planning"

    def test_extract_json_code_block(self):
        text = "```json\n{\"task_type\":\"poi_query\"}\n```"
        result = _extract_json(text)
        assert result["task_type"] == "poi_query"

    def test_extract_json_nested_in_text(self):
        text = "好的，这是解析结果：{\"task_type\":\"help\"} 请查收"
        result = _extract_json(text)
        assert result["task_type"] == "help"


class TestContextMerge:
    def test_merge_fills_missing_start_end(self):
        intent = TaskIntent(
            task_type="path_planning",
            start=None,
            end=None,
            constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
            ambiguity="请指定起点和终点",
        )
        context = {
            "start": {"name": "珞珈门", "type": "poi"},
            "end": {"name": "樱顶", "type": "poi"},
        }
        merged = _merge_context_with_intent(intent, context, "继续")
        assert merged.start is not None
        assert merged.start.name == "珞珈门"
        assert merged.end is not None
        assert merged.end.name == "樱顶"
        assert merged.ambiguity is None

    def test_merge_keeps_existing_fields(self):
        intent = TaskIntent(
            task_type="path_planning",
            start=PoiRef(name="梅园", type="poi"),
            end=None,
            constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
        )
        context = {
            "start": {"name": "珞珈门", "type": "poi"},
            "end": {"name": "樱顶", "type": "poi"},
        }
        merged = _merge_context_with_intent(intent, context, "继续")
        assert merged.start.name == "梅园"
        assert merged.end.name == "樱顶"


class TestAmbiguityCompletion:
    def test_ambiguity_fill_start(self):
        intent = TaskIntent(
            task_type="unknown",
            start=None,
            end=None,
            constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
        )
        context = {
            "previous_intent": {
                "ambiguity": "请指定起点",
                "end": {"name": "樱顶", "type": "poi"},
                "constraints": {"distance": "short", "slope": "avoid", "scenery": "normal"},
                "weights": {"distance": 0.8, "slope": 0.1, "scenery": 0.1},
            }
        }
        completed = _resolve_ambiguity_completion(intent, "牌坊", context)
        assert completed.start is not None
        assert completed.start.name == "珞珈门"
        assert completed.end is not None
        assert completed.end.name == "樱顶"
        assert completed.task_type == "path_planning"
        assert completed.ambiguity is None

    def test_ambiguity_fill_end(self):
        intent = TaskIntent(
            task_type="unknown",
            start=None,
            end=None,
            constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
        )
        context = {
            "previous_intent": {
                "ambiguity": "请指定终点",
                "start": {"name": "珞珈门", "type": "poi"},
                "constraints": {"distance": "medium", "slope": "normal", "scenery": "high"},
                "weights": {"distance": 0.2, "slope": 0.1, "scenery": 0.7},
            }
        }
        completed = _resolve_ambiguity_completion(intent, "樱顶", context)
        assert completed.start is not None
        assert completed.start.name == "珞珈门"
        assert completed.end is not None
        assert completed.end.name == "武汉大学老斋舍"
        assert completed.task_type == "path_planning"
        assert completed.ambiguity is None


class TestParseQueryOfflineMock:
    def test_parse_query_no_api_key_fallback(self):
        """无 API key 时走规则兜底：可解析 query 应成功提取起终点（两阶段规则后处理）。"""
        with patch("agents.parser.DEEPSEEK_API_KEY", ""):
            from agents.parser import parse_query
            result = parse_query("从牌坊到樱顶")
            assert isinstance(result, TaskIntent)
            assert result.task_type == "path_planning"
            assert result.start is not None and result.start.name == "珞珈门"
            assert result.end is not None and result.end.name == "武汉大学老斋舍"
            assert result.ambiguity is None

    def test_parse_query_no_api_key_unparseable(self):
        """无 API key 且规则也无法提取时，保留解析失败提示。"""
        with patch("agents.parser.DEEPSEEK_API_KEY", ""):
            from agents.parser import parse_query
            result = parse_query("随便说点什么没有地名")
            assert isinstance(result, TaskIntent)
            assert result.ambiguity is not None


class TestTravelModeDetection:
    """出行方式（walk/bike/drive）关键词检测与全链路解析。"""

    @pytest.mark.parametrize("query,expected_mode,expected_explicit", [
        ("骑车从牌坊到樱顶", "bike", True),
        ("开车去教五", "drive", True),
        ("步行去樱花大道", "walk", True),
        ("从牌坊到樱顶", "walk", False),
        # "校车/坐车问询"不含精确驾车词，不得误判为 drive
        ("校车在哪里坐", "walk", False),
        ("共享单车能进吗", "bike", True),
    ])
    def test_detect_travel_mode(self, query, expected_mode, expected_explicit):
        mode, explicit = detect_travel_mode(query)
        assert mode == expected_mode
        assert explicit == expected_explicit

    def test_task_intent_default_mode_walk(self):
        """TaskIntent 未指定 mode 时默认为 walk。"""
        intent = TaskIntent(
            task_type="path_planning",
            start=PoiRef(name="珞珈门", type="poi"),
            end=PoiRef(name="樱顶", type="poi"),
            constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
        )
        assert intent.mode == "walk"

    def test_post_process_keyword_overrides_llm_mode(self):
        """关键词后处理纠偏：query 含"骑车"时，即使 LLM/默认给了 walk 也强制改为 bike。"""
        intent = TaskIntent(
            task_type="path_planning",
            start=PoiRef(name="珞珈门", type="poi"),
            end=PoiRef(name="樱顶", type="poi"),
            constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
            mode="walk",
        )
        processed = _t011_post_process(intent, "骑车从牌坊到樱顶", None)
        assert processed.mode == "bike"

    def test_post_process_drive_keyword(self):
        """query 含"开车"时 mode 强制为 drive。"""
        intent = TaskIntent(
            task_type="path_planning",
            start=PoiRef(name="珞珈门", type="poi"),
            end=PoiRef(name="教五", type="poi"),
            constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
            mode="walk",
        )
        processed = _t011_post_process(intent, "开车从牌坊到教五", None)
        assert processed.mode == "drive"

    def test_context_inherits_previous_mode(self):
        """多轮承接：本轮未提出行方式但有延续语（"继续"）→ 继承 previous_intent.mode=bike。"""
        intent = TaskIntent(
            task_type="path_planning",
            start=None,
            end=None,
            constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
        )
        context = {
            "start": {"name": "珞珈门", "type": "poi"},
            "end": {"name": "樱顶", "type": "poi"},
            "previous_intent": {"mode": "bike"},
        }
        merged = _merge_context_with_intent(intent, context, "继续")
        assert merged.mode == "bike"

    def test_context_mode_not_inherited_without_guard(self):
        """守卫不满足（新话题、无延续语、无新地点）时不继承上轮 mode。"""
        intent = TaskIntent(
            task_type="path_planning",
            start=None,
            end=None,
            constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
        )
        context = {
            "start": {"name": "珞珈门", "type": "poi"},
            "end": {"name": "樱顶", "type": "poi"},
            "previous_intent": {"mode": "bike"},
        }
        merged = _merge_context_with_intent(intent, context, "皇冠幸福里怎么样")
        assert merged.mode == "walk"

    def test_parse_query_bike_keyword_fallback(self):
        """无 API key 规则兜底：含"骑车"的路径 query 解析出 mode=bike。"""
        with patch("agents.parser.DEEPSEEK_API_KEY", ""):
            from agents.parser import parse_query
            result = parse_query("骑车从牌坊到樱顶")
            assert result.task_type == "path_planning"
            assert result.mode == "bike"
