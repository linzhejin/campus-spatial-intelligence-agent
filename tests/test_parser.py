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
)


class TestTaskIntentInstantiation:
    def test_task_intent_valid_path_planning(self):
        intent = TaskIntent(
            task_type="path_planning",
            start=PoiRef(name="牌坊", type="poi"),
            end=PoiRef(name="樱顶", type="poi"),
            constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
            weights=None,
            input_method="nl",
            ambiguity=None,
        )
        assert intent.task_type == "path_planning"
        assert intent.start.name == "牌坊"
        assert intent.end.name == "樱顶"
        assert intent.constraints.distance == "medium"

    def test_task_intent_weight_source_annotation(self):
        intent = TaskIntent(
            task_type="path_planning",
            start=PoiRef(name="牌坊", type="poi"),
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
            start=PoiRef(name="牌坊", type="poi"),
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
        assert processed.start.name == "樱顶"
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
            "start": {"name": "牌坊", "type": "poi"},
            "end": {"name": "樱顶", "type": "poi"},
        }
        merged = _merge_context_with_intent(intent, context, "继续")
        assert merged.start is not None
        assert merged.start.name == "牌坊"
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
            "start": {"name": "牌坊", "type": "poi"},
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
        assert completed.start.name == "牌坊"
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
                "start": {"name": "牌坊", "type": "poi"},
                "constraints": {"distance": "medium", "slope": "normal", "scenery": "high"},
                "weights": {"distance": 0.2, "slope": 0.1, "scenery": 0.7},
            }
        }
        completed = _resolve_ambiguity_completion(intent, "樱顶", context)
        assert completed.start is not None
        assert completed.start.name == "牌坊"
        assert completed.end is not None
        assert completed.end.name == "樱顶"
        assert completed.task_type == "path_planning"
        assert completed.ambiguity is None


class TestParseQueryOfflineMock:
    def test_parse_query_no_api_key_fallback(self):
        with patch("agents.parser.DEEPSEEK_API_KEY", ""):
            from agents.parser import parse_query
            result = parse_query("从牌坊到樱顶")
            assert isinstance(result, TaskIntent)
            assert result.ambiguity is not None
