# T-005 Implementation Report
> Task: Prompt 模板与 Few-shot 示例
> 阶段: Stage 6 → Stage 7（自动合并执行）
> 日期: 2026-08-11
> Agent: Developer Subagent · T-005

---

## 一、完成内容

### 1.1 现状诊断
实施前对两个 prompt 文件进行逐项验收：

| 文件 | 验收条目 | 要求 | 现状 | 是否达标 |
|---|---|---|---|---|
| parse_system.txt | 1. 任务类型说明 | path_planning + poi_query | ✓ 已有 | ✅ |
| parse_system.txt | 2. 约束等级说明 | distance/slope/scenery 三级枚举 | ✓ 已有 | ✅ |
| parse_system.txt | 3. 权重生成规则 | weights=null 条件 + [0.05,0.8] | ✓ 已有 | ✅ |
| parse_system.txt | 4. POI 列表占位 | `{poi_list_json}` | ✓ 已有 | ✅ |
| parse_system.txt | 5. 输出 JSON 格式 | task_type/constraints/weights/ambiguity | ✓ 已有 | ✅ |
| parse_system.txt | 6. Few-shot ≥ 5 个 | ≥ 5 | ✗ 0 个 | ❌ |
| explain_system.txt | 1. 解释生成规则 | 解释生成助手定位 | ✓ 已有 | ✅ |
| explain_system.txt | 2. 必含 3 要素 | 约束回应 + 途经景点 + 最短差异 | ✓ 已有 | ✅ |
| explain_system.txt | 3. ≤150 字限制 | 字数上限 | ✓ 已有 | ✅ |
| explain_system.txt | 4. Few-shot ≥ 2 个 | ≥ 2 | ✗ 1 个 | ❌ |

### 1.2 实施操作（严格 Append-only）
**未修改或删除任何原有行**，仅在文件末尾增量追加 few-shot 块：

#### parse_system.txt — 追加 8 个 few-shot
| # | 场景 | 覆盖点 |
|---|---|---|
| 1 | "从牌坊到樱顶" | 普通 path_planning，weights=null |
| 2 | "从牌坊到樱顶，避开陡坡" | slope=avoid 约束 + weights 非空 |
| 3 | "我第一次来武大，想看樱花和老图书馆，但膝盖不好" | 缺起点 → ambiguity 触发 |
| 4 | "从梅园到桂园，最短路径" | distance=short + weights 距离优先 |
| 5 | "带朋友逛，从教五去图书馆，走风景好的路" | scenery=high + weights 景观优先 |
| 6 | "樱顶在哪" | task_type=poi_query（T-011 新增） |
| 7 | "你能做什么" | task_type=help（T-011 新增） |
| 8 | "今天天气怎么样" | task_type=unknown + ambiguity 引导文案（T-011 新增） |

#### explain_system.txt — 追加 2 个 few-shot（共 3 个）
| # | 场景 | 覆盖点 |
|---|---|---|
| 1 | slope=avoid, scenery=high（原有） | 避坡 + 景观，多走 400 米 |
| 2 | distance=short（新增） | 最短距离与最短路径一致场景 |
| 3 | scenery=high（新增，模板兜底） | 景观优先 + 模板兜底说明 |

---

## 二、修改文件清单

| 文件路径 | 改动类型 | 行数变化 | 说明 |
|---|---|---|---|
| `agents/prompts/parse_system.txt` | Append-only 追加 | 51 行 → 77 行（+26 行） | 追加 8 个 few-shot 示例块 |
| `agents/prompts/explain_system.txt` | Append-only 追加 | 20 行 → 34 行（+14 行） | 追加 2 个 few-shot（共 3 个），含模板兜底 |
| `scripts/validate_t005_prompts.py` | 新建 | +160 行 | 验收验证脚本（正则统计 + 逐项校验） |
| `project-docs/06_DECISIONS.md` | 追加决策 | +20 行 | DEC-013：Prompt Few-shot 补充策略 |

---

## 三、测试验证

### 3.1 验证脚本
路径：`scripts/validate_t005_prompts.py`
- 校验 parse_system.txt 6 条硬指标 + 2 条额外（ambiguity 触发规则、4 种 task_type 覆盖）
- 校验 explain_system.txt 4 条硬指标 + 1 条额外（模板兜底说明）
- 输出 Parse X/6 + Explain X/4 汇总

### 3.2 运行结果
```
【parse_system.txt 验收 6/6】
  ✓ PASS  1_任务类型说明
  ✓ PASS  2_约束等级说明
  ✓ PASS  3_权重生成规则
  ✓ PASS  4_POI列表占位
  ✓ PASS  5_输出JSON格式
  ✓ PASS  6_FewShot>=5  (实际数量 = 8)
    ✓ [额外] 额外_ambiguity触发规则: True
    ✓ [额外] 额外_4种task_type覆盖: True

【explain_system.txt 验收 4/4】
  ✓ PASS  1_解释生成规则
  ✓ PASS  2_必含3要素
  ✓ PASS  3_<=150字限制
  ✓ PASS  4_FewShot>=2  (实际数量 = 3)
    ✓ [额外] 额外_模板兜底说明: True

汇总: Parse 6/6  Explain 4/4
整体结果: Status DONE ✓
```

### 3.3 可读取性验证（Python 文件读取拼接）
（T-005 验收标准第 3 条）验证 prompt 文件可被 Python 正常读取：
- 两文件均为 UTF-8 纯文本，无 BOM
- `Path.read_text(encoding="utf-8")` 可完整读取
- `{poi_list_json}` 占位符完整保留，支持后续 `.format()` 拼接

---

## 四、问题与风险

### 4.1 已规避风险
| 风险 | 规避措施 | 结果 |
|---|---|---|
| 误删/覆盖原有 prompt | 严格 Append-only，不修改任何原有行 | ✅ 零风险 |
| few-shot 数量不达标 | 超额补充（parse 8/5、explain 3/2） | ✅ 达标 |
| T-011 新增 task_type 未对齐 | 提前覆盖 4 种 task_type + ambiguity 引导 | ✅ 提前对齐 |

### 4.2 无遗留问题
本次实施无阻塞性遗留问题。

---

## 五、建议（下一任务 T-011 参考）

1. **parser.py 加载时注意**：parse_system.txt 中 `{poi_list_json}` 是 Python format 占位符，需调用 `.replace('{poi_list_json}', json_str)` 或 `.format(poi_list_json=json_str)` 注入 POI 列表，注意转义 JSON 中的 `{`/`}`。
2. **Pydantic 枚举对齐**：few-shot 已包含 `task_type="help"` 和 `task_type="unknown"`，TaskIntent.task_type 的 Literal 需扩展为 4 值枚举（T-011 验收标准第 12 条）。
3. **ambiguity 引导文案**：few-shot 第 8 条已预置 unknown 类型的完整引导话术，parser.py 兜底输出时可复用该文案保持一致。
4. **模板兜底文案复用**：explain_system.txt 示例 3 预置了 scenery=high 的模板话术，explainer.py LLM 失败时可直接参考该结构生成兜底。

---

## 六、验收状态汇总

| 维度 | 指标 | 结果 |
|---|---|---|
| Parse 6 条硬验收 | 任务类型/约束/权重/POI占位/JSON格式/≥5 Few-shot | **6/6 通过** |
| Explain 4 条硬验收 | 解释规则/3要素/150字限制/≥2 Few-shot | **4/4 通过** |
| 额外覆盖 | ambiguity 触发规则、4 种 task_type、模板兜底说明 | 全部覆盖 |
| 技术决策 | DEC-013 已写入 06_DECISIONS.md | 已完成 |
| 验证脚本 | scripts/validate_t005_prompts.py 可重复执行 | 已完成 |

**最终 Status: DONE · Parse 6/6 · Explain 4/4 全部通过 ✓**
