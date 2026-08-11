# T-024 Implementation Report · 快捷按钮与权重优先级

> 任务编号: T-024 (Phase 6 · 架构审核 N6)
> 执行角色: Developer Subagent · T-024
> 完成日期: 2026-08-11
> 验收结果: **Status DONE · 3 checks 3/3 · DECISION 新增 (DEC-013)**

---

## 1. 任务目标

实现架构审核 N6 优先级规则：**显式 NL(含偏好) > 快捷按钮 > 默认权重**

| # | 验收标准 § | 说明 |
|---|---|---|
| 1 | §1 三种优先级场景 | explicit_nl / shortcut / default 三场景正确打标 + 处理 |
| 2 | §2 解释文本标注来源 | explain 文本开头加"已按您的空间偏好推荐/快捷预设推荐/默认路线推荐" |
| 3 | §3 前端高亮 + 清除 | 快捷按钮点击加 `.shortcut-active` 高亮；LLM 解析出权重(weights!=null)清除高亮，input_method=shortcut 保留 |

---

## 2. 技术决策 (DEC-013)

已写入 `project-docs/06_DECISIONS.md` 新增 **DEC-013**：

- **选择**: 在 `TaskIntent` Pydantic BaseModel 新增可选字段
  ```python
  weight_source: Optional[Literal["explicit_nl", "shortcut", "default"]] = None
  ```
- **放弃方案**: 塞入 constraints dict / 单独 meta dict 字段 / routes.py 运行时推断
- **核心理由**: 向后兼容(Optional+默认值) + 类型安全(Literal+Pydantic) + 语义清晰(不污染 constraints)

---

## 3. 修改文件清单（按 Plan 严格 append-only / 最小改动）

| 文件 | 修改方式 | 改动点 |
|---|---|---|
| `agents/parser.py` | **BaseModel 加字段 + 末尾加函数** | ① `TaskIntent.weight_source` 新字段；② `_annotate_weight_source()` 辅助函数；③ `parse_query` 两个 return 路径（正常 + 兜底）调用打标 |
| `agents/explainer.py` | **新增函数 + 签名扩展** | ① `_build_weight_source_prefix()` 前缀映射；② `_build_template_explanation` 加 `weight_source` 参数并拼前缀；③ `generate_explanation` 签名扩展 + LLM user_content 含"偏好来源"字段 |
| `api/routes.py` | **两处返回扩展** | ① `/api/parse` 快捷模式返回体加 `weight_source: "shortcut"`；② `/api/chat` 调用 `generate_explanation` 时传 `intent_data.weight_source` |
| `static/js/app.js` | **末尾 append-only IIFE** | ① `window.addShortcutHighlight(btn)` + `window.clearShortcutHighlight()` 公开函数；② document 捕获阶段 click 监听加 `.shortcut-active`；③ fetch 猴子补丁，`/api/parse` 或 `/api/chat` 返回 `weights!=null && input_method!='shortcut'` 时清除高亮 |
| `project-docs/06_DECISIONS.md` | **新增 DEC-013** | 决策详情 + 3 放弃方案对比 + 影响说明 |
| `tests/test_t024_selfcheck.py` | **新增（验收自检）** | 3 条验收拆为 11 个 token 检查点，全部通过 |

---

## 4. 核心实现细节

### 4.1 parser.py · `_annotate_weight_source` 打标规则（架构审核 N6 优先级）

```python
def _annotate_weight_source(intent: TaskIntent) -> TaskIntent:
    if intent.input_method == "shortcut":           # 最高优先级 1: 快捷按钮显式触发
        intent.weight_source = "shortcut"
    elif intent.input_method == "nl":
        if intent.weights is not None:              # 优先级 2: NL 明确表达了偏好（LLM 输出了权重）
            intent.weight_source = "explicit_nl"
        else:                                        # 优先级 3: NL 无偏好（weights=null → fallback DEFAULT_WEIGHTS）
            intent.weight_source = "default"
    else:
        intent.weight_source = "default"
    return intent
```

调用点：
- 正常 LLM 返回：`return _annotate_weight_source(TaskIntent(**data))`
- 兜底降级返回：`return _annotate_weight_source(_fallback_task_intent(query))`

### 4.2 explainer.py · 来源前缀映射

```python
def _build_weight_source_prefix(weight_source):
    if weight_source == "explicit_nl": return "已按您的空间偏好推荐。"
    if weight_source == "shortcut":    return "已按快捷按钮预设偏好推荐。"
    if weight_source == "default":     return "已按默认路线推荐。"
    return ""
```

模板解释开头拼接前缀；LLM user_content 也加了 `偏好来源：xxx` 字段，确保即使不用模板，LLM 也会在解释中提及来源。

### 4.3 routes.py · 两处传值

```python
# /api/parse 快捷模式（不走 LLM，手动构造）
return _ok({
    ...
    "weight_source": "shortcut",  # ← 新增
})

# /api/chat 一站式：从 intent_data 取出后传给 explainer
weight_source = intent_data.get("weight_source")
explanation = generate_explanation(route_data_for_explainer, constraints, weights, weight_source)
```

### 4.4 app.js · 前端高亮 + 清除逻辑（Append-only 零侵入）

- **高亮**: `document.addEventListener('click', ..., true)` 捕获阶段监听，点击 `.shortcut-btn` 即加 `.shortcut-active`（同时兼容原 `.active` 类，不破坏原有逻辑）
- **清除**: `window.fetch` 猴子补丁（monkey patch），克隆响应体后判断：
  ```
  if (weights !== null && weights !== undefined && inputMethod !== 'shortcut') {
      clearShortcutHighlight();
  }
  ```
  - `input_method=shortcut` 时保留高亮（当前就是快捷按钮预设生效）
  - 仅当 LLM 解析出了显式权重（`weights!=null`）且不是 shortcut 来源，才清除高亮 → 含义："用户用 NL 重新表达了偏好，快捷按钮预设不再生效"

---

## 5. 自检结果（`python tests/test_t024_selfcheck.py`）

```
============================================================
T-024 快捷按钮与权重优先级 · 自检 (3 checks)
============================================================
[PASS] §1-1 显式 NL 含偏好 → weight_source=explicit_nl
[PASS] §1-2 快捷按钮模式 → weight_source=shortcut
[PASS] §1-3 NL 无偏好 → weight_source=default
[PASS] §1-4 向后兼容：不传 weight_source 仍可实例化
------------------------------------------------------------
[PASS] §2-1 explicit_nl → 解释含偏好标注
[PASS] §2-2 shortcut → 解释含快捷预设标注
[PASS] §2-3 default → 解释含默认路线标注
------------------------------------------------------------
[PASS] §3-1 含 .shortcut-active 高亮类切换
[PASS] §3-2 addShortcutHighlight 函数存在
[PASS] §3-3 clearShortcutHighlight 函数存在
[PASS] §3-4 清除逻辑：weights 非 null 时清除高亮（shortcut 例外）
============================================================
Result: 11/11 PASSED
============================================================
```

**3 条验收标准 3/3 全部通过（细分 11 个 token 检查点 11/11 PASSED）**

---

## 6. 风险规避（DEC-013 向后兼容）

- **TaskIntent 新字段 Optional+默认值 None**：旧代码不传 weight_source 仍可正常实例化
- **explainer.generate_explanation 新参数 Optional+默认值 None**：旧调用（routes.py 其他地方或测试）不传 weight_source 仍正常，前缀为空字符串不输出任何内容
- **前端 fetch 猴子补丁 try/catch**：JSON 解析失败或任何异常静默忽略，不影响原 API 链路
- **前端高亮类双写**：`.shortcut-active` 和原代码的 `.active` 同时加，防止其他逻辑依赖 `.active` 失效

---

## 7. 验收结论

| 维度 | 结果 |
|---|---|
| Status | **DONE** |
| §1 三种场景正确处理 | ✅ PASS (4/4 tokens) |
| §2 解释文本标注偏好来源 | ✅ PASS (3/3 tokens) |
| §3 前端高亮 + 清除逻辑 | ✅ PASS (4/4 tokens) |
| 合计检查点 | **11/11 PASSED** |
| 新增 DECISION | ✅ DEC-013（已写入 06_DECISIONS.md） |
| 破坏原 schema | ❌ 无（全向后兼容） |
