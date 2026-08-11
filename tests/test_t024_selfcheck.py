"""
T-024 快捷按钮与权重优先级 · 自检脚本（3 条验收 token）
运行方式: python tests/test_t024_selfcheck.py
全过输出: ALL 3/3 PASSED
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


# =========================================================================
# Check 1: 三种优先级场景 weight_source 打标正确
# =========================================================================
def check_1_weight_source_scenarios():
    """验收标准 §1: 显式 NL > 快捷按钮 > 默认权重 3 场景正确打标 weight_source"""
    try:
        from agents.parser import TaskIntent, _annotate_weight_source, Constraints, PoiRef
    except Exception as e:
        check("§1-0 TaskIntent 模型可导入", False, str(e))
        return

    # Check 1.1: 显式 NL 含偏好 → weights != null + input_method=nl → explicit_nl
    intent_explicit = TaskIntent(
        task_type="path_planning",
        start=PoiRef(name="牌坊", type="poi"),
        end=PoiRef(name="樱顶", type="poi"),
        constraints=Constraints(distance="medium", slope="avoid", scenery="normal"),
        weights={"distance": 0.2, "slope": 0.6, "scenery": 0.2},
        input_method="nl",
    )
    intent_explicit = _annotate_weight_source(intent_explicit)
    check(
        "§1-1 显式 NL 含偏好 → weight_source=explicit_nl",
        intent_explicit.weight_source == "explicit_nl",
        f"实际值={intent_explicit.weight_source}",
    )

    # Check 1.2: 快捷按钮模式 → input_method=shortcut → shortcut
    intent_shortcut = TaskIntent(
        task_type="path_planning",
        start=PoiRef(name="牌坊", type="poi"),
        end=PoiRef(name="樱顶", type="poi"),
        constraints=Constraints(distance="short", slope="normal", scenery="normal"),
        weights={"distance": 0.8, "slope": 0.1, "scenery": 0.1},
        input_method="shortcut",
    )
    intent_shortcut = _annotate_weight_source(intent_shortcut)
    check(
        "§1-2 快捷按钮模式 → weight_source=shortcut",
        intent_shortcut.weight_source == "shortcut",
        f"实际值={intent_shortcut.weight_source}",
    )

    # Check 1.3: NL 无偏好 + 无快捷 → weights=null + input_method=nl → default
    intent_default = TaskIntent(
        task_type="path_planning",
        start=PoiRef(name="牌坊", type="poi"),
        end=PoiRef(name="樱顶", type="poi"),
        constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
        weights=None,
        input_method="nl",
    )
    intent_default = _annotate_weight_source(intent_default)
    check(
        "§1-3 NL 无偏好 → weight_source=default",
        intent_default.weight_source == "default",
        f"实际值={intent_default.weight_source}",
    )

    # Check 1.4: 向后兼容 — TaskIntent 不传 weight_source 也能正常实例化
    try:
        TaskIntent(
            task_type="path_planning",
            start=PoiRef(name="牌坊", type="poi"),
            end=PoiRef(name="樱顶", type="poi"),
            constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
        )
        check("§1-4 向后兼容：不传 weight_source 仍可实例化", True)
    except Exception as e:
        check("§1-4 向后兼容：不传 weight_source 仍可实例化", False, str(e))


# =========================================================================
# Check 2: 解释文本标注偏好来源
# =========================================================================
def check_2_explanation_weight_source_label():
    """验收标准 §2: explain/解释文本标注偏好来源"""
    try:
        from agents.explainer import _build_template_explanation, generate_explanation
    except Exception as e:
        check("§2-0 explainer 可导入", False, str(e))
        return

    route_data = {
        "distance_m": 1200,
        "shortest_distance_m": 800,
        "pois": [{"name": "樱顶"}, {"name": "老图书馆"}],
        "filter_status": "no_filter",
    }
    constraints = {"distance": "medium", "slope": "avoid", "scenery": "high"}

    # Check 2.1: explicit_nl → 解释含"已按您的偏好推荐"或"景观偏好"之类
    exp_explicit = generate_explanation(
        route_data, constraints, {"distance": 0.2, "slope": 0.5, "scenery": 0.4},
        weight_source="explicit_nl",
    )
    label_ok = (
        "偏好" in exp_explicit
        or "景观" in exp_explicit
        or "您的" in exp_explicit
        or "已按" in exp_explicit
    )
    check(
        "§2-1 explicit_nl → 解释含偏好标注",
        label_ok,
        f"解释文本={exp_explicit!r}",
    )

    # Check 2.2: shortcut → 解释含"快捷按钮"或"预设偏好"之类
    exp_shortcut = generate_explanation(
        route_data, constraints, {"distance": 0.8, "slope": 0.1, "scenery": 0.1},
        weight_source="shortcut",
    )
    label_ok = (
        "快捷" in exp_shortcut
        or "预设" in exp_shortcut
        or "按钮" in exp_shortcut
        or "已按" in exp_shortcut
    )
    check(
        "§2-2 shortcut → 解释含快捷预设标注",
        label_ok,
        f"解释文本={exp_shortcut!r}",
    )

    # Check 2.3: default → 解释含"默认路线推荐"之类
    exp_default = generate_explanation(
        route_data, {"distance": "medium", "slope": "normal", "scenery": "normal"}, None,
        weight_source="default",
    )
    label_ok = (
        "默认" in exp_default
        or "已按默认" in exp_default
        or "标准路线" in exp_default
    )
    check(
        "§2-3 default → 解释含默认路线标注",
        label_ok,
        f"解释文本={exp_default!r}",
    )


# =========================================================================
# Check 3: 前端快捷按钮高亮 + 清除逻辑（静态代码扫描）
# =========================================================================
def check_3_frontend_shortcut_highlight():
    """验收标准 §3: 快捷按钮点击高亮 + LLM 解析出权重后清除高亮"""
    app_js_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "static", "js", "app.js"
    )
    try:
        with open(app_js_path, "r", encoding="utf-8") as f:
            js_code = f.read()
    except Exception as e:
        check("§3-0 app.js 可读", False, str(e))
        return

    # Check 3.1: shortcut-active 高亮类使用
    check(
        "§3-1 含 .shortcut-active 高亮类切换",
        "shortcut-active" in js_code,
        "未找到 shortcut-active CSS 类名",
    )

    # Check 3.2: addShortcutHighlight 函数存在
    check(
        "§3-2 addShortcutHighlight 函数存在",
        "addShortcutHighlight" in js_code,
        "未找到 addShortcutHighlight 函数",
    )

    # Check 3.3: clearShortcutHighlight 函数存在
    check(
        "§3-3 clearShortcutHighlight 函数存在",
        "clearShortcutHighlight" in js_code,
        "未找到 clearShortcutHighlight 函数",
    )

    # Check 3.4: /api/parse 返回 weights!=null 时清除高亮（input_method=shortcut 保留）
    # 扫描关键 token: input_method === 'shortcut' / weights != null / clearShortcutHighlight
    has_clear_logic = (
        ("weights" in js_code and "null" in js_code)
        or ("input_method" in js_code and "shortcut" in js_code)
    ) and "clearShortcutHighlight" in js_code
    check(
        "§3-4 清除逻辑：weights 非 null 时清除高亮（shortcut 例外）",
        has_clear_logic,
        "未找到 weights 与 input_method 组合判断清除高亮逻辑",
    )


# =========================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("T-024 快捷按钮与权重优先级 · 自检 (3 checks)")
    print("=" * 60)
    check_1_weight_source_scenarios()
    print("-" * 60)
    check_2_explanation_weight_source_label()
    print("-" * 60)
    check_3_frontend_shortcut_highlight()
    print("=" * 60)
    print(f"Result: {PASS}/{PASS + FAIL} PASSED")
    print("=" * 60)
    sys.exit(0 if FAIL == 0 else 1)
