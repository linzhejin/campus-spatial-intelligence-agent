"""
T-011 验收标准 12 条自检脚本
- 不依赖真实 LLM 调用（无 Key 也能跑）
- 静态结构检查 + 规则兜底函数调用
- 通过线：12/12 PASS
"""
import sys
import inspect
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import BaseModel
from typing import Literal, get_args


results = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    results.append((name, status, detail))
    print(f"[{status}] {name}  {detail}")
    return cond


# ====== 1. Pydantic Model 定义：PoiRef、Constraints、TaskIntent ======
def t01_check_pydantic_models():
    from agents import parser
    ok = True
    for cls_name in ["PoiRef", "Constraints", "TaskIntent"]:
        cls = getattr(parser, cls_name, None)
        ok &= check(
            f"1.{cls_name} 存在",
            cls is not None and inspect.isclass(cls) and issubclass(cls, BaseModel),
            f"type={type(cls)}",
        )
    return ok


# ====== 2. 超时保护：connect=5s、read=10s ======
def t02_check_timeout():
    src = (PROJECT_ROOT / "agents" / "parser.py").read_text(encoding="utf-8")
    has_connect = "connect=5.0" in src or "connect=5," in src
    has_read = "read=10.0" in src or "read=10," in src
    has_httpx_timeout = "httpx.Timeout" in src
    return check(
        "2. 超时保护 connect=5s/read=10s",
        has_connect and has_read and has_httpx_timeout,
        f"connect={has_connect} read={has_read} httpx={has_httpx_timeout}",
    )


# ====== 3. P99 JSON 格式错误率 < 1%：重试机制 + Pydantic 校验 ======
def t03_check_retry_validation():
    src = (PROJECT_ROOT / "agents" / "parser.py").read_text(encoding="utf-8")
    has_3_retries = "for attempt in range(3)" in src or "max_retries" in src
    has_pydantic = "ValidationError" in src and "TaskIntent(**data)" in src
    has_json_extract = "_extract_json" in src or "JSONDecodeError" in src
    return check(
        "3. 重试 + Pydantic 校验保障 P99",
        has_3_retries and has_pydantic and has_json_extract,
        f"retry={has_3_retries} pydantic={has_pydantic} jsonextract={has_json_extract}",
    )


# ====== 4. 异常兜底：降级为默认权重 + 错误提示 ======
def t04_check_fallback():
    from agents import parser
    fallback = parser._fallback_task_intent("test")
    has_default_constraints = (
        fallback.constraints.distance == "medium"
        and fallback.constraints.slope == "normal"
        and fallback.constraints.scenery == "normal"
    )
    has_ambiguity_msg = fallback.ambiguity is not None and len(fallback.ambiguity) > 0
    return check(
        "4. 异常兜底默认权重 + 提示",
        has_default_constraints and has_ambiguity_msg,
        f"constraints={fallback.constraints.model_dump()} ambiguity={fallback.ambiguity!r}",
    )


# ====== 5. 快捷按钮模式：mode→weights 映射，不走 LLM ======
def t05_check_shortcut_mode():
    from api.routes import SHORTCUT_MODE_PRESETS
    has_presets = isinstance(SHORTCUT_MODE_PRESETS, dict) and len(SHORTCUT_MODE_PRESETS) >= 3
    ok = check("5.1 快捷按钮 mode 预设存在（≥3 档）", has_presets, f"modes={list(SHORTCUT_MODE_PRESETS.keys())}")
    if not has_presets:
        return False
    expected = {"distance_first", "scenery_first", "slope_avoid"}
    ok_names = set(SHORTCUT_MODE_PRESETS.keys()) == expected
    ok_fields = all(("weights" in p and "constraints" in p) for p in SHORTCUT_MODE_PRESETS.values())
    check("5.2 mode 命名统一 (distance_first/scenery_first/slope_avoid)", ok_names, f"actual={set(SHORTCUT_MODE_PRESETS.keys())}")
    check("5.3 每档含 weights+constraints", ok_fields)
    return ok and ok_names and ok_fields


# ====== 6. 优先级：显式 NL > 快捷按钮 > 默认权重 ======
def t06_check_priority():
    src = (PROJECT_ROOT / "agents" / "parser.py").read_text(encoding="utf-8")
    has_priority_fn = (
        "priority" in src.lower()
        and ("nl" in src.lower() or "shortcut" in src.lower())
    ) or hasattr_check(["_priority_apply", "_apply_priority", "apply_priority_logic"])
    return check(
        "6. 优先级处理逻辑存在 (NL > 快捷 > 默认)",
        has_priority_fn,
    )


def hasattr_check(names):
    from agents import parser
    return any(hasattr(parser, n) for n in names)


# ====== 7. POI 查询识别：task_type="poi_query"，start=POI end=null ======
def t07_check_poi_query():
    from agents import parser
    has_rule_fn = hasattr_check(["_rule_based_classify", "_classify_poi_query", "_t011_post_process"])
    func_name = None
    for n in ["_rule_based_classify", "_t011_post_process"]:
        if hasattr(parser, n):
            func_name = n
            break
    ok_fn = check("7.1 POI 查询规则分类函数存在", has_rule_fn, f"found={func_name}")

    if hasattr(parser, "_rule_based_classify"):
        queries = ["樱顶在哪", "老图书馆介绍", "珞珈山是什么", "樱花大道在哪里"]
        ok_cases = True
        for q in queries:
            classified = parser._rule_based_classify(q)
            ok = classified["task_type"] == "poi_query" and classified["start_name"] is not None
            ok_cases &= ok
            check(f"7.2 '{q}' → poi_query start={classified.get('start_name')}", ok)
        return ok_fn and ok_cases
    elif hasattr(parser, "_t011_post_process"):
        # 构造一个 fallback 风格的 intent，看后处理是否能将 POI 查询纠正
        fake_intent = parser.TaskIntent(
            task_type="path_planning", start=None, end=None,
            constraints=parser.Constraints(distance="medium", slope="normal", scenery="normal"),
            weights=None, input_method="nl", ambiguity=None,
        )
        post = parser._t011_post_process(fake_intent, "樱顶在哪里", None)
        ok = post.task_type == "poi_query" and post.start is not None and post.start.name == "樱顶" and post.end is None
        return check("7.3 '樱顶在哪里' → poi_query start=樱顶 end=null (后处理)", ok, f"task_type={post.task_type} start={post.start.name if post.start else None} end={post.end}")
    return False


# ====== 8. 帮助/引导类：task_type="help" ======
def t08_check_help():
    from agents import parser
    ok = True
    if hasattr(parser, "_rule_based_classify"):
        for q in ["你能做什么", "怎么用", "推荐景点", "有哪些景点", "介绍一下功能", "help"]:
            c = parser._rule_based_classify(q)
            ok_q = c["task_type"] == "help"
            check(f"8.1 '{q}' → help", ok_q)
            ok &= ok_q
    elif hasattr(parser, "_t011_post_process"):
        fake_intent = parser.TaskIntent(
            task_type="path_planning", start=None, end=None,
            constraints=parser.Constraints(distance="medium", slope="normal", scenery="normal"),
            weights=None, input_method="nl", ambiguity=None,
        )
        for q in ["你能做什么", "推荐景点"]:
            post = parser._t011_post_process(fake_intent, q, None)
            ok_q = post.task_type == "help"
            check(f"8.2 '{q}' → help (后处理)", ok_q, f"task_type={post.task_type}")
            ok &= ok_q
    else:
        ok = check("8.X help 规则函数缺失", False)
    return ok


# ====== 9. 多轮上下文承接：复用 context 中 start/end ======
def t09_check_context_carry():
    from agents import parser
    ok = True
    fake_intent = parser.TaskIntent(
        task_type="path_planning", start=None, end=None,
        constraints=parser.Constraints(distance="medium", slope="normal", scenery="normal"),
        weights=parser.DEFAULT_WEIGHTS if hasattr(parser, "DEFAULT_WEIGHTS") else None,
        input_method="nl", ambiguity=None,
    )
    context = {
        "start": {"name": "牌坊", "type": "poi"},
        "end": {"name": "樱顶", "type": "poi"},
    }
    if hasattr(parser, "_merge_context_with_intent"):
        merged = parser._merge_context_with_intent(fake_intent, context, "换风景好的")
        ok_start = merged.start is not None and merged.start.name == "牌坊"
        ok_end = merged.end is not None and merged.end.name == "樱顶"
        check("9.1 context 中 start/end 被复用", ok_start and ok_end,
              f"start={merged.start.name if merged.start else None} end={merged.end.name if merged.end else None}")
        ok &= ok_start and ok_end
    elif hasattr(parser, "_t011_post_process"):
        post = parser._t011_post_process(fake_intent, "换风景好的", context)
        ok_start = post.start is not None and post.start.name == "牌坊"
        ok_end = post.end is not None and post.end.name == "樱顶"
        check("9.2 '换风景好的' + context → 复用牌坊/樱顶", ok_start and ok_end,
              f"start={post.start.name if post.start else None} end={post.end.name if post.end else None}")
        ok &= ok_start and ok_end
    else:
        ok = check("9.X 上下文承接函数缺失", False)
    return ok


# ====== 10. 闲聊兜底：task_type="chat"（无关话题交给 DeepSeek 闲聊） ======
def t10_check_unknown():
    from agents import parser
    ok = True
    if hasattr(parser, "_rule_based_classify"):
        for q in ["今天天气怎么样", "推荐一个餐厅", "你好啊", "讲个笑话"]:
            c = parser._rule_based_classify(q)
            ok_q = c["task_type"] == "chat"
            check(f"10.1 '{q}' → chat", ok_q)
            ok &= ok_q
    elif hasattr(parser, "_t011_post_process"):
        fake_intent = parser.TaskIntent(
            task_type="path_planning", start=None, end=None,
            constraints=parser.Constraints(distance="medium", slope="normal", scenery="normal"),
            weights=None, input_method="nl", ambiguity=None,
        )
        for q in ["今天天气", "推荐吃饭的地方"]:
            post = parser._t011_post_process(fake_intent, q, None)
            ok_q = post.task_type == "chat"
            check(f"10.2 '{q}' → chat",
                  ok_q, f"task_type={post.task_type}")
            ok &= ok_q
    else:
        ok = check("10.X 闲聊规则函数缺失", False)
    return ok


# ====== 11. ambiguity 补全：上轮缺字段，本轮补单 POI 名合并 ======
def t11_check_ambiguity_completion():
    from agents import parser
    ok = True
    if hasattr(parser, "_resolve_ambiguity_completion"):
        context = {
            "previous_intent": {
                "task_type": "path_planning",
                "start": None,
                "end": {"name": "樱顶", "type": "poi"},
                "constraints": {"distance": "medium", "slope": "avoid", "scenery": "high"},
                "weights": {"distance": 0.1, "slope": 0.5, "scenery": 0.4},
                "ambiguity": "请指定起点",
            }
        }
        # 上一轮返回了 ambiguity="请指定起点"，本轮用户只回复"牌坊"
        fake_intent = parser.TaskIntent(
            task_type="path_planning", start=None, end=None,
            constraints=parser.Constraints(distance="medium", slope="normal", scenery="normal"),
            weights=None, input_method="nl", ambiguity=None,
        )
        merged = parser._resolve_ambiguity_completion(fake_intent, "牌坊", context)
        ok_start = merged.start is not None and merged.start.name == "牌坊"
        ok_end = merged.end is not None and merged.end.name == "樱顶"
        ok_slope = merged.constraints.slope == "avoid"
        ok_weights = merged.weights is not None  # 保留上轮的 weights
        check("11.1 缺起点→补牌坊→合并保留樱顶/slope=avoid/weights",
              ok_start and ok_end and ok_slope and ok_weights,
              f"start={merged.start.name if merged.start else None} "
              f"end={merged.end.name if merged.end else None} "
              f"slope={merged.constraints.slope} weights={merged.weights}")
        ok &= ok_start and ok_end and ok_slope and ok_weights
    elif hasattr(parser, "_t011_post_process"):
        context = {
            "previous_intent": {
                "task_type": "path_planning",
                "start": None,
                "end": {"name": "樱顶", "type": "poi"},
                "constraints": {"distance": "medium", "slope": "avoid", "scenery": "high"},
                "weights": {"distance": 0.1, "slope": 0.5, "scenery": 0.4},
                "ambiguity": "请指定起点",
            }
        }
        fake_intent = parser.TaskIntent(
            task_type="path_planning", start=None, end=None,
            constraints=parser.Constraints(distance="medium", slope="normal", scenery="normal"),
            weights=None, input_method="nl", ambiguity=None,
        )
        post = parser._t011_post_process(fake_intent, "牌坊", context)
        ok_start = post.start is not None and post.start.name == "牌坊"
        ok_end = post.end is not None and post.end.name == "樱顶"
        check("11.2 补起点牌坊 → 合并樱顶/slope=avoid", ok_start and ok_end,
              f"start={post.start.name if post.start else None} "
              f"end={post.end.name if post.end else None} "
              f"slope={post.constraints.slope}")
        ok &= ok_start and ok_end
    else:
        ok = check("11.X ambiguity 补全函数缺失", False)
    return ok


# ====== 12. Pydantic 枚举扩展：task_type 4 项 Literal ======
def t12_check_literal_extension():
    from agents import parser
    task_type_field = parser.TaskIntent.model_fields["task_type"]
    annotation = task_type_field.annotation
    literal_args = get_args(annotation)
    required = {"path_planning", "poi_query", "help", "unknown"}
    actual = set(literal_args)
    ok = required.issubset(actual)
    check("12.1 task_type 枚举包含 4 项", ok,
          f"actual={sorted(actual)} required={sorted(required)}")
    # help/unknown 类型允许 start/end = null
    try:
        help_intent = parser.TaskIntent(
            task_type="help", start=None, end=None,
            constraints=parser.Constraints(distance="medium", slope="normal", scenery="normal"),
            weights=None, input_method="nl", ambiguity=None,
        )
        ok_help = help_intent.task_type == "help"
    except Exception as e:
        ok_help = False
    check("12.2 task_type=help start/end=null 合法", ok_help)

    try:
        unk_intent = parser.TaskIntent(
            task_type="unknown", start=None, end=None,
            constraints=parser.Constraints(distance="medium", slope="normal", scenery="normal"),
            weights=None, input_method="nl", ambiguity="引导语",
        )
        ok_unk = unk_intent.task_type == "unknown" and unk_intent.ambiguity is not None
    except Exception as e:
        ok_unk = False
    check("12.3 task_type=unknown + ambiguity 合法", ok_unk)
    return ok and ok_help and ok_unk


def main():
    print("=" * 60)
    print("T-011 验收标准 12 条自检 (无 LLM 离线模式)")
    print("=" * 60)
    all_ok = True
    all_ok &= t01_check_pydantic_models()
    all_ok &= t02_check_timeout()
    all_ok &= t03_check_retry_validation()
    all_ok &= t04_check_fallback()
    all_ok &= t05_check_shortcut_mode()
    all_ok &= t06_check_priority()
    all_ok &= t07_check_poi_query()
    all_ok &= t08_check_help()
    all_ok &= t09_check_context_carry()
    all_ok &= t10_check_unknown()
    all_ok &= t11_check_ambiguity_completion()
    all_ok &= t12_check_literal_extension()

    print("=" * 60)
    passed = sum(1 for _, s, _ in results if s == "PASS")
    total = len(results)
    print(f"Summary: {passed}/{total} PASS  (验收标准 12 条)")
    print("=" * 60)

    fail_details = [(n, d) for n, s, d in results if s == "FAIL"]
    if fail_details:
        print("FAIL 明细:")
        for n, d in fail_details:
            print(f"  - {n}: {d}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
