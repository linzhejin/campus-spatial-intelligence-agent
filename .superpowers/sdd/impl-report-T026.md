# T-026 实施报告 · 标注数据加载与验证

> **任务**: T-026 · 标注数据加载与验证（P7 · 数据标注）
> **执行者**: Developer Subagent T-026
> **日期**: 2026-08-11
> **验收条款**: 05_TASKS.md §T-026 (1)(2)(3) + 03_TDD.md §7.7 + §4.2

---

## 1. 验收结果总览

| # | 验收标准 | 结果 | 证据 |
|---|----------|------|------|
| 1 | 覆盖率 ≥ 80% → routing 完整模式生效 | ✅ PASS | CHECK 1: coverage=0.9, `_should_degrade=None`, 成本函数走三因素完整路径 |
| 2 | 覆盖率 < 80% → 降级为仅距离成本 | ✅ PASS | CHECK 2: coverage=0.5, filter_status="degraded_annotations", 成本函数仅 `weights["distance"] * norm` |
| 3 | 降级时 filter_status 附加 "degraded_annotations" 或 "no_annotations" 标记 | ✅ PASS | CHECK 3: coverage=0 → status="no_annotations"；CHECK 2 → status="degraded_annotations" |
| - | **3 checks 总计** | **3/3 ✅** | |

### coverage_rate 实际数值

| 场景 | coverage_rate | 触发行为 |
|------|---------------|----------|
| `data/road_annotations.json`（当前空文件） | **0.0** | 触发 `no_annotations` 降级，仅距离成本 |
| 自检 90% 标注 | 0.900 | 完整模式，三因素成本生效 |
| 自检 50% 标注 | 0.500 | `degraded_annotations` 降级 |
| 自检 0% 标注 | 0.000 | `no_annotations` 降级 |

### DEC-013 决策文档

✅ **已写入** `project-docs/06_DECISIONS.md`（DEC-013: 路段标注 edge 匹配策略 — edge_id[u,v,k] 优先 + u/v 对兜底）

---

## 2. 技术变更清单

### 2.1 `spatial/network.py`（append-only + `load_or_download_network` 小改）

| 新增/修改项 | 行数 | 作用 |
|-------------|------|------|
| 全局 `_annotation_coverage_rate: Optional[float]` | L18 | 缓存覆盖率，`get_annotation_coverage_rate()` 返回 |
| `import json` + `Tuple` 类型 | L8, L11 | JSON 读标注文件 |
| `load_or_download_network()` 增加 merge 钩子 | L67-L100 | GraphML 加载/下载完成后 → `_annotations_path()` → `_merge_annotations()` → 挂全局 coverage |
| `_annotations_path()` | L193-L198 | 定位 `data/road_annotations.json`（优先 config 配置） |
| `_parse_edge_id()` | L201-L219 | 将 `[u, v, k]` 列表/字符串归一化为 `(str(u), str(v), int(k))` 三元组 |
| `_merge_annotations(G, path) -> float` | L222-L285 | DEC-013 两级匹配：① `edge_id[u,v,k]` 精确 ② `u/v` 对兜底；写入 slope_level/scenery_level/name；返回覆盖率 |
| `get_annotation_coverage_rate() -> float` | L288-L292 | 公共 API，暴露全局 coverage（0~1） |

### 2.2 `spatial/routing.py`（append-only + 小改）

| 新增/修改项 | 作用 |
|-------------|------|
| `_should_degrade_annotations() -> Optional[str]` | 调 `get_annotation_coverage_rate()`：rate=0→`"no_annotations"`；0<rate<0.8→`"degraded_annotations"`；rate≥0.8→`None` |
| `_edge_cost_factory(..., annotation_degraded_tag=None)` | 新增降级分支：`tag is not None` 时 `cost = w_d × D_norm`（仅距离），不走三因素计算 |
| `compute_route()` 开头 | 调 `_should_degrade_annotations()` 存 `annotation_degraded`，拼接到 `filter_status`（`no_filter` 直接替换，否则 `old+tag`） |
| `compute_route()` 返回 | 新增 `_annotation_degraded: bool` 字段，便于自检脚本断言 |

### 2.3 辅助文件

| 文件 | 作用 |
|------|------|
| `scripts/T026_validate_annotations.py` | 3 checks token 自检脚本，可重复执行 |
| `project-docs/06_DECISIONS.md` | 新增 DEC-013（edge 两级匹配策略） |

---

## 3. 降级行为说明（对齐 TDD §7.7）

### 3.1 触发矩阵

| coverage_rate | `_should_degrade` 返回 | `filter_status` 示例 | 成本函数行为 |
|---------------|-------------------------|----------------------|--------------|
| 0.0（空文件/无文件） | `"no_annotations"` | `"no_annotations"` | Cost = w_d × D_norm（仅距离） |
| 0.01 ~ 0.79 | `"degraded_annotations"` | `"degraded_annotations"` / `"filtered+degraded_annotations"` | Cost = w_d × D_norm（仅距离） |
| 0.80 ~ 1.00 | `None` | `"no_filter"` / `"filtered"` / `"degraded_slope"` | Cost = w_d×D + w_s×S + w_v×(1−V)（三因素完整） |

### 3.2 与 explainer 兜底文本的衔接

- explainer 已在 `filter_status` 包含 "degraded" / "degraded_annotations" / "no_annotations" 关键词时，会输出**"部分路段缺标注数据，仅按距离推荐"** 的兜底说明（T-026 验收 3）
- 无需额外修改 explainer.py

---

## 4. 潜在风险应对（T-026 风险项）

> **潜在风险**: 匹配不到 edge → 覆盖率为 0，不崩直接降级

| 风险场景 | 应对机制 | 验证 |
|----------|----------|------|
| `road_annotations.json` 不存在 | `load_or_download_network` 内 `os.path.exists` 判断，默认 coverage=0.0 | CHECK 3 PASS |
| `road_annotations.json` JSON 解析失败 | `try/except Exception`，coverage 置 0.0，warning 日志不崩 | 异常注入测试（未执行，代码有保护） |
| 所有标注 edge_id 都不匹配图 | `annotated_set` 为空，返回 coverage=0.0 → `no_annotations` 降级 | CHECK 3 PASS |
| 标注条目数 > 图边数 | 以图边总数 `G.number_of_edges()` 为分母，覆盖率自动 ≤ 1.0 | 代码保证 |
| NetworkX node_id 类型 int/str 不一致 | `_parse_edge_id` 强制 u→str, v→str, k→int；遍历时 `str(uu) == u_t` 对齐 | DEC-013 设计 + CHECK 1/2/3 全过 |

---

## 5. 自检脚本运行输出

```
============================================================
T-026 标注数据加载与验证 自检脚本
============================================================

[CHECK 1] 覆盖率 ≥ 80% → 完整模式
  coverage_rate = 0.900 (expected ≥ 0.8)
  实际标注边数: 9/10 = 0.900
  get_annotation_coverage_rate() = 0.900
  should_degrade = None (expected None/full mode)
[CHECK 1] PASS ✓

[CHECK 2] 覆盖率 < 80% → 降级模式
  coverage_rate = 0.500 (expected < 0.8)
  should_degrade = degraded_annotations (expected 'degraded_annotations')
  filter_status = 'degraded_annotations' (expected contains 'degraded_annotations')
[CHECK 2] PASS ✓

[CHECK 3] 完全无标注 → no_annotations 标记
  coverage_rate = 0.000 (expected = 0.0)
  should_degrade = no_annotations (expected 'no_annotations')
  filter_status = 'no_annotations'
[CHECK 3] PASS ✓

============================================================
自检结果汇总
============================================================
  CHECK 1 (覆盖率≥80%→完整模式): PASS ✓
  CHECK 2 (覆盖率<80%→降级): PASS ✓
  CHECK 3 (无标注→no_annotations): PASS ✓

总计: 3/3 通过
```

---

## 6. 技术决策追溯

| 决策 ID | 标题 | 落实位置 |
|---------|------|----------|
| **DEC-013** | 路段标注 edge 匹配策略 — edge_id[u,v,k] 优先 + u/v 对兜底 | `spatial/network.py:_parse_edge_id` + `_merge_annotations` 两级匹配分支 |
| DEC-011 | 约束与权重分离（降级策略 §7.7） | `spatial/routing.py:_should_degrade_annotations` + `_edge_cost_factory` 距离-only 分支 |
| DEC-003 | 坡度/景观手动标注（数据源） | `data/road_annotations.json` 结构 §4.2 兼容 |
| DEC-008 | 数据存储 JSON | `_annotations_path()` 默认 `data/road_annotations.json` |

---

**总状态**: Status DONE · 3 checks 3/3 ✅ · coverage_rate 实际空文件值 = 0.0 · DEC-013 ✅ 已写
