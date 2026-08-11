# 07 · 测试计划

> 产出阶段: Stage 9 · QA 测试
> 角色: QA Engineer
> 状态: ✅ 已完成
> 更新日期: 2026-08-11

---

## 1. 测试范围

### 1.1 包含模块

| 模块 | 文件 | 测试重点 |
|------|------|---------|
| NL 解析 Agent | `agents/parser.py` + `parse_system.txt` | 4 种 task_type、权重生成、constraints 提取、多轮上下文 |
| 解释生成 Agent | `agents/explainer.py` + `explain_system.txt` | 模板兜底、LLM 解释、weight_source 标注 |
| 坐标转换 | `spatial/coord_transform.py` | GCJ-02 ↔ WGS-84 往返精度、中国境外直通 |
| POI 匹配 | `spatial/poi.py` | 精确/模糊/别名匹配、消歧、类型/季节筛选 |
| 路网加载 | `spatial/network.py` | GraphML 缓存、标注 merge、覆盖率计算 |
| 路径计算 | `spatial/routing.py` | 权重校验、硬约束过滤、软成本优化、降级策略 |
| API 路由 | `api/routes.py` | 7 端点正确性、坐标系转换、错误码 |
| 前端交互 | `static/js/app.js` + `index.html` | NL 输入、快捷按钮、候选卡片、PWA |
| 前端视觉 | `static/css/style.css` | 响应式断点、WCAG AA 触摸目标、主题色 |
| 部署 | `render.yaml` + `README.md` | gunicorn 启动、环境变量、15min Hello World |

### 1.2 不包含范围

- **V1 明确排除**: 多 POI 串联 TSP (P2)、实时人流数据、用户账号系统
- **需在线环境**: 端到端 LLM 调用测试 (标记 e2e，离线时跳过)

---

## 2. 测试策略

### 2.1 分层策略

```
┌─────────────────────────────────────────┐
│  L4 · E2E 测试 (pytest -m e2e)          │  ← 需 Flask 服务器 + DeepSeek API
│  15 条标准 query + 10 条多轮对话          │
├─────────────────────────────────────────┤
│  L3 · 集成测试 (pytest)                  │  ← 离线可跑
│  API 端点结构 + parser 后处理管道         │
├─────────────────────────────────────────┤
│  L2 · 单元测试 (pytest)                  │  ← 离线可跑，CI 自动化
│  coord / poi / routing / parser 核心函数  │
├─────────────────────────────────────────┤
│  L1 · 自检脚本 (scripts/validate_*.py)   │  ← 离线 token 级静态检查
│  Prompt few-shot / CSS 视觉 / annotations │
└─────────────────────────────────────────┘
```

### 2.2 测试类型分布

| 类型 | 用例数 | 自动化 | 说明 |
|------|--------|--------|------|
| **功能测试** | 35 | ✅ pytest | 核心函数输入→输出正确性 |
| **边界测试** | 12 | ✅ pytest | 权重边界 [0.05, 0.8]、空输入、超长字符串 |
| **异常测试** | 10 | ✅ pytest + 手工 | LLM 不可用、路网未加载、POI 不存在 |
| **回归测试** | 8 | ✅ 自检脚本 | Prompt 变更检测、CSS 覆盖检查 |
| **兼容性测试** | 8 | ⚠️ 手工 | 5 浏览器 × 核心功能 (附录 A Checklist) |
| **性能测试** | 3 | ✅ validate_sla.py | 热/冷启动 P50、连续 15 query 稳定性 |

---

## 3. 测试用例

### 模块 1: 坐标转换 (`spatial/coord_transform.py`)

| 编号 | 描述 | 前置条件 | 操作步骤 | 预期结果 |
|------|------|---------|---------|---------|
| TC-C001 | GCJ-02 → WGS-84 已知点转换 | 武大牌坊 GCJ-02 (114.35806, 30.53318) | 调用 gcj02_to_wgs84() | 偏移量 ∈ [0.0001°, 0.02°]，方向正确 |
| TC-C002 | WGS-84 → GCJ-02 一致性 | 已知 WGS-84 点 | wgs84→gcj→wgs84 往返 | 误差 ≤ 1e-7° |
| TC-C003 | GCJ → WGS → GCJ 往返精度 | 9 个中国境内点 | gcj→wgs→gcj 往返 | 误差 ≤ 0.0001° |
| TC-C004 | WGS → GCJ → WGS 往返精度 | 4 个点 (武大+北京+上海+广州) | wgs→gcj→wgs 往返 | 误差 ≤ 0.0001° |
| TC-C005 | 中国境外直通 (无变换) | 5 个境外点 (NY/东京/伦敦/0,0/悉尼) | gcj02_to_wgs84() | 输入 = 输出，无偏移 |
| TC-C006 | 武大牌坊与 OSM 参考值对比 | 独立 OSM 参考坐标 | gcj→wgs84 后与参考值比较 | 偏差 ≤ 500m |

### 模块 2: POI 匹配 (`spatial/poi.py`)

| 编号 | 描述 | 前置条件 | 操作步骤 | 预期结果 |
|------|------|---------|---------|---------|
| TC-P001 | 精确名称匹配 | pois.json 已加载 | find_poi("牌坊") | 返回 poi_001 牌坊 |
| TC-P002 | 别名匹配 | 同上 | find_poi("珞珈门") | 返回 poi_001 牌坊 |
| TC-P003 | 模糊匹配 (相似度 ≥ 0.6) | 同上 | find_poi("老图") | 返回 poi_004 老图书馆 |
| TC-P004 | 无匹配返回 None | 同上 | find_poi("不存在的POI") | 返回 None |
| TC-P005 | 大小写不敏感 (中文) | 同上 | find_poi("老圖書館") | 返回 poi_004 (繁体→简体) |
| TC-P006 | 多候选消歧 | 同上 | find_poi_candidates("梅") | 返回梅园等高相似度结果，按 score 降序 |
| TC-P007 | 按类型筛选 | 同上 | get_pois_by_type("scenery") | 返回所有 type=scenery 的 POI |
| TC-P008 | 按季节筛选 | 同上 | list_all_pois(season="spring") | 返回含 spring 标签的 POI |
| TC-P009 | 关键词搜索 | 同上 | search_pois("樱花") | 返回樱花大道等匹配 POI |
| TC-P010 | JSON 不可用时 config fallback | 删除 pois.json | load_pois() | 从 config.WHU_POIS 回退加载 |

### 模块 3: 路径计算 (`spatial/routing.py`)

| 编号 | 描述 | 前置条件 | 操作步骤 | 预期结果 |
|------|------|---------|---------|---------|
| TC-R001 | 默认权重 (null fallback) | weights=None | resolve_weights(None) | 返回 {distance: 0.5, slope: 0.2, scenery: 0.3} |
| TC-R002 | 权重下界裁剪 | weights={distance: 0.01} | resolve_weights() | distance=0.05 (不低于 min) |
| TC-R003 | 权重上界裁剪 | weights={distance: 0.99} | resolve_weights() | distance=0.8 (不高于 max) |
| TC-R004 | 归一化 | weights={d: 1, s: 1, v: 1} | resolve_weights() | 总和 = 1.0 |
| TC-R005 | 硬约束 slope=avoid 过滤 level=5 | constraints={slope: "avoid"} | _filter_by_constraints() | level=5 边被移除 |
| TC-R006 | slope=avoid 对 level=4 加 2× 惩罚 | 同上 | _filter_by_constraints() | penalty_map 含 level=4 边，乘数 = 2.0 |
| TC-R007 | 约束过严降级 | 过滤后无可行路径 | compute_route() | filter_status="degraded_slope" |
| TC-R008 | 三因素成本计算 | mock edge data | _compute_edge_cost() | Cost = w_d×D + w_s×S + w_v×(1-V) |
| TC-R009 | 起终点不可达 | 不连通图 | compute_route() | 抛出 ValueError |
| TC-R010 | 标注覆盖率 < 80% 降级 | coverage_rate=0.5 | compute_route() | filter_status 含 "degraded_annotations" |
| TC-R011 | 路径长度上限裁剪 | recommended > shortest × 3 | compute_route() | length_capped=true, recommended=shortest |

### 模块 4: NL 解析 Agent (`agents/parser.py`)

| 编号 | 描述 | 前置条件 | 操作步骤 | 预期结果 |
|------|------|---------|---------|---------|
| TC-A001 | path_planning 标准解析 | LLM 可用 | parse_query("从牌坊到樱顶") | task_type=path_planning, start=牌坊, end=樱顶 |
| TC-A002 | slope=avoid 约束识别 | LLM 可用 | parse_query("避开陡坡从行政楼到枫园") | constraints.slope="avoid" |
| TC-A003 | scenery=high 偏好识别 | LLM 可用 | parse_query("走风景好的路线") | constraints.scenery="high" |
| TC-A004 | weights 生成 (含偏好) | LLM 可用 | parse_query("膝盖不好想看樱花") | weights != null, slope 权重大 |
| TC-A005 | weights=null (无偏好) | LLM 可用 | parse_query("从教五到图书馆") | weights=null → fallback 默认权重 |
| TC-A006 | poi_query 规则分类 | 离线规则 | _rule_based_classify("樱顶在哪里") | task_type=poi_query, start=樱顶 |
| TC-A007 | help 规则分类 | 离线规则 | _rule_based_classify("你能做什么") | task_type=help |
| TC-A008 | unknown 规则分类 | 离线规则 | _rule_based_classify("今天天气怎么样") | task_type=unknown |
| TC-A009 | 多轮 context 补全起点 | context={start: 牌坊} | _merge_context_with_intent() | intent.start 从 context 补全 |
| TC-A010 | ambiguity 补全 | 上轮 ambiguity="请指定起点" | _resolve_ambiguity_completion() | 本轮单 POI→补 start，保留上轮 constraints |
| TC-A011 | LLM 不可用 fallback | DEEPSEEK_API_KEY="" | parse_query("从牌坊到樱顶") | 返回 _fallback_task_intent() |
| TC-A012 | JSON 提取容错 | LLM 返回带 markdown 的 JSON | _extract_json() | 正确去除 ```json``` 包裹 |
| TC-A013 | 3 次重试机制 | LLM 第 1/2 次返回非法 JSON | parse_query() | 重试 3 次后 fallback |
| TC-A014 | weight_source 标注 (NL 有偏好) | input_method=nl, weights!=null | _annotate_weight_source() | weight_source="explicit_nl" |
| TC-A015 | weight_source 标注 (快捷按钮) | input_method=shortcut | _annotate_weight_source() | weight_source="shortcut" |
| TC-A016 | 500 字截断保护 | query 长度 > 500 | parse_query() | query[:500] 截断 |

### 模块 5: API 路由 (`api/routes.py`)

| 编号 | 描述 | 前置条件 | 操作步骤 | 预期结果 |
|------|------|---------|---------|---------|
| TC-API01 | POST /api/parse (NL 模式) | JSON body: {query, input_method:"nl"} | HTTP POST | 返回 task_type + start/end + constraints + weights |
| TC-API02 | POST /api/parse (快捷模式) | JSON body: {start, end, mode, input_method:"shortcut"} | HTTP POST | 直接返回预设 weights，不走 LLM |
| TC-API03 | POST /api/route (路径规划) | 路网已加载 | JSON body: {start, end, constraints, weights} | 返回 recommended + shortest + costs + pois |
| TC-API04 | POST /api/chat (一站式) | 路网+LLM 可用 | JSON body: {query: "从牌坊到樱顶"} | 返回完整结果含 explanation |
| TC-API05 | POST /api/candidates (候选 POI) | 路网已加载 | JSON body: {start: {name: "牌坊"}, poi_type: "scenery"} | 返回 Top-5 候选含 scenery_score + distance_m |
| TC-API06 | GET /api/pois | 无 | HTTP GET | 返回 24 个 POI 列表 |
| TC-API07 | GET /api/pois/<name> | 无 | HTTP GET /api/pois/樱顶 | 返回单 POI 详情 |
| TC-API08 | POST /api/network/init | 首次调用 | HTTP POST | 下载 OSM 路网，返回 nodes/edges 数量 |
| TC-API09 | GCJ-02 → WGS-84 入参转换 | POI 坐标 GCJ-02 | /api/route 内部 | get_nearest_node 前正确转换 |
| TC-API10 | 路网未加载 503 错误 | 未调用 /api/network/init | POST /api/route | 返回 503 + "路网尚未加载" |
| TC-API11 | POI 不存在 404 错误 | start_name 不存在 | POST /api/route | 返回 404 + "起点 'X' 未找到" |
| TC-API12 | 不合法 JSON 400 错误 | 非 JSON body | POST /api/parse | 返回 400 + "请求体必须为合法 JSON" |
| TC-API13 | task_type 非 path_planning 返回 400 | query 为 poi_query | POST /api/chat | 返回 400 + "暂不支持的任务类型" |

### 模块 6: 前端交互 (`static/js/app.js`)

| 编号 | 描述 | 前置条件 | 操作步骤 | 预期结果 |
|------|------|---------|---------|---------|
| TC-FE01 | NL 文本输入 + 回车提交 | 页面加载完成 | 输入"从牌坊到樱顶"→ 回车 | 调用 /api/chat，渲染路线 |
| TC-FE02 | 快捷按钮点击高亮 | 页面加载完成 | 点击"风景优先"按钮 | 按钮 .shortcut-active 高亮 |
| TC-FE03 | 候选卡片渲染 | 调用 showCandidateCards() | 传入 3 个候选 POI | 渲染 3 张 .candidate-card |
| TC-FE04 | 候选卡片点击→自动规划 | 卡片已渲染 | 点击第 1 张候选卡片 | 自动填充输入框 + 触发提交 |
| TC-FE05 | 冷启动欢迎卡片弹出 | 首次访问 (无 localStorage) | DOMContentLoaded 后 150ms | 遮罩层 + 欢迎卡片弹出 |
| TC-FE06 | 欢迎卡片 5 种关闭方式 | 欢迎卡片可见 | X/mask/Esc/开始使用/shortcut 点击 | 卡片关闭，遮罩消失 |
| TC-FE07 | POI Marker 渲染 | 地图加载完成 | 检查地图上 Marker 数量 | 24 个 Marker，按 type 着色 |
| TC-FE08 | InfoWindow 弹窗 | 点击 POI Marker | 点击"樱顶" Marker | 弹窗含名称、别名、介绍、起终点按钮 |
| TC-FE09 | 路线双线渲染 | 路径规划完成 | 检查地图线条 | 推荐路线粉实线 + 最短路径灰虚线 |
| TC-FE10 | 响应式布局 | 调整浏览器宽度 | 480/768/1024/1440 四断点 | 无横向滚动条，布局适配 |

### 模块 7: 前端视觉 + PWA

| 编号 | 描述 | 前置条件 | 操作步骤 | 预期结果 |
|------|------|---------|---------|---------|
| TC-V01 | 主题色正确 | CSS 加载 | 检查 --pink 变量 | #E8929C 樱花粉 |
| TC-V02 | 触摸目标 ≥ 44px | 移动端视口 | 检查 .welcome-close, .help-btn | height ≥ 44px |
| TC-V03 | Service Worker 注册 | 页面加载 | 检查 navigator.serviceWorker.controller | 注册成功 |
| TC-V04 | SW 三策略 (cache-first/SWR/network-only) | SW 已激活 | 检查 sw.js 源码 | 三段策略注释 + fetch 分发 |
| TC-V05 | Manifest 主题色 | 页面加载 | 检查 manifest.json | theme_color=#E8929C, display=standalone |
| TC-V06 | PWA 图标就位 | 静态资源目录 | 检查 icon-192.png, icon-512.png | 文件存在且尺寸正确 |

### 模块 8: 异常场景兜底 (PRD 附录 E)

| 编号 | 描述 | 前置条件 | 操作步骤 | 预期结果 |
|------|------|---------|---------|---------|
| TC-E01 | LLM API Key 未配置 | DEEPSEEK_API_KEY="" | POST /api/chat | 规则兜底解析，返回默认路径 |
| TC-E02 | LLM 返回非法 JSON | 模拟 LLM 返回纯文本 | parse_query() | 3 次重试后 fallback |
| TC-E03 | 路网未初始化 | 未调 network/init | POST /api/route | 503 + 提示先初始化 |
| TC-E04 | 起点/终点不可达 | 路网不连通 | POST /api/route | 404 + "不可达" |
| TC-E05 | 硬约束过严无可行路径 | slope=avoid 全过滤 | compute_route() | 自动降级放宽 |
| TC-E06 | 请求体非 JSON | Content-Type: text/plain | POST /api/parse | 400 + "必须为合法 JSON" |
| TC-E07 | query 为空字符串 | query="" | POST /api/parse (NL) | 400 + "query 字段必填" |
| TC-E08 | 不相关问题兜底 | query="今天天气怎么样" | POST /api/chat | task_type=unknown + 友好引导 |
| TC-E09 | Help 引导闭环 | query="你能做什么" | POST /api/chat | task_type=help + 功能说明 |
| TC-E10 | 多轮 ambiguity 补全 | 上轮缺起点→用户补"牌坊" | POST /api/chat (带 context) | 补全 start + 保留上轮偏好 |

---

## 4. 测试环境

| 维度 | 配置 |
|------|------|
| **操作系统** | Windows 11 (dev) / Render Ubuntu 22.04 (prod) |
| **Python** | 3.11+ |
| **浏览器 (桌面)** | Chrome 120+, Firefox 120+, Edge 120+ |
| **浏览器 (移动)** | iOS Safari 15+, Android Chrome 10+ |
| **LLM** | DeepSeek V4-Flash (deepseek-chat) |
| **路网数据** | OSMnx walk network, WGS-84, ~9000 edges |
| **POI 数据** | data/pois.json, 24 POI, GCJ-02 |
| **标注数据** | data/road_annotations.json, 137 edges, coverage_rate=1.0 |
| **测试框架** | pytest 8.4 + pytest.ini markers (e2e) |

---

## 5. 测试数据

### 5.1 标准 15 条 NL Query (PRD 附录 A)

见 [tests/test_end_to_end.py](../tests/test_end_to_end.py) `STANDARD_QUERIES`

### 5.2 多轮 10 条测试集 (TDD §16.2)

见 [tests/test_multiturn.py](../tests/test_multiturn.py) `MULTITURN_TEST_CASES`

### 5.3 已知坐标参考点

| 地点 | GCJ-02 (lng, lat) | WGS-84 参考 (lng, lat) |
|------|-------------------|----------------------|
| 武大牌坊 | 114.35806, 30.53318 | ~114.3515, 30.5347 |
| 樱顶 | 114.36400, 30.53950 | — |
| 老图书馆 | 114.36450, 30.53900 | — |
