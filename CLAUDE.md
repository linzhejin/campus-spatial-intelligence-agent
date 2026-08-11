# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 提供代码库工作指南。

## 项目概述

**漫步珞珈 (WHU-Walker)** — 武汉大学校园空间智能体。用户用自然语言描述出行需求（如"避开陡坡，从牌坊到樱顶走风景好的路"），系统返回多因素优化的路径并在地图上可视化展示。

核心研究问题：*大语言模型能否作为人类空间偏好的转换器，将模糊的自然语言转化为可计算的空间参数？*

## 常用命令

```bash
# 启动开发服务器（Flask，端口 5000）
python app.py

# 生产环境启动
gunicorn app:app --workers 1 --timeout 60 --bind 0.0.0.0:$PORT

# 运行全部测试
python -m pytest tests/ -q --tb=short

# 运行单个测试文件
python -m pytest tests/test_parser.py -q

# 运行指定测试用例
python -m pytest tests/test_routing.py::test_resolve_weights_default -q

# 验证路网覆盖率（首次运行会下载 OSM 数据，约 30 秒）
python scripts/validate_osm_network.py

# 自检脚本（项目专项验证）
python scripts/validate_t005_prompts.py    # Prompt few-shot 数量检查
python scripts/validate_t018.py            # PWA / CSS 验证
python scripts/T026_validate_annotations.py # 路段标注验证

# 健康检查
curl -s http://localhost:5000/health
```

## 系统架构

```
用户 NL 输入 → agents/parser.py   (LLM → TaskIntent)
             → api/routes.py      (坐标转换 + 路径计算)
             → spatial/routing.py (硬约束过滤 + 软成本 Dijkstra)
             → agents/explainer.py(LLM → 自然语言解释)
             → 前端地图渲染       (高德 JS API)
```

### 模块职责

| 层级 | 模块 | 职责 |
|------|------|------|
| 入口 | `app.py` | Flask 工厂函数，CORS 配置，静态文件服务，`/js/config.js` 注入高德 Key |
| API | `api/routes.py` | 7 个 REST 端点：`/parse`、`/route`、`/chat`、`/pois`、`/pois/<name>`、`/network/init`、`/candidates` |
| Agent | `agents/parser.py` | NL → `TaskIntent`（DeepSeek LLM + 规则兜底分类）。双轨策略：`parse_query()` 调用 LLM（3 次重试 + Pydantic 校验），`_t011_post_process()` 对 `help`/`unknown`/`poi_query` 类型做规则强制覆盖 |
| Agent | `agents/explainer.py` | 路径数据 → ≤150 字中文解释（LLM 优先，模板兜底） |
| Agent | `agents/prompts/` | `parse_system.txt`（8 个 few-shot）、`explain_system.txt`（3 个 few-shot） |
| GIS | `spatial/network.py` | OSMnx 路网下载/缓存。首次运行通过 `network_type="walk"` 下载，保存为 `data/whu_road_network.graphml`。加载时自动合并 `road_annotations.json` 到边的属性中 |
| GIS | `spatial/routing.py` | **核心算法**：两步路径计算（DEC-011）。(1) 硬约束过滤：`slope=avoid` 时移除 `slope_level=5` 的边，对 level=4 施加 2× 距离惩罚。(2) 软成本优化：`Cost = w_d×D + w_s×S + w_v×(1−V)`。路径长度上限为 `min(最短路径×3, 2000m)` |
| GIS | `spatial/coord_transform.py` | GCJ-02 ⇄ WGS-84 双向转换。POI（高德，GCJ-02）→ WGS-84 供 OSM 路径计算 → 转回 GCJ-02 供前端展示 |
| GIS | `spatial/poi.py` | 从 `data/pois.json` 加载 POI，模糊名称匹配，支持按类型/季节筛选 |
| 数据 | `data/pois.json` | ≥15 个 POI，包含名称、别名、坐标（GCJ-02）、类型、描述、季节标签、景观评分 |
| 数据 | `data/road_annotations.json` | 每条路段的人工坡度/景观标注（1-5 级），启动时合并到路网边属性 |
| 前端 | `static/` | 原生 JS + 高德 JS API 2.0 + PWA（Service Worker 三策略缓存）。珞珈主题色：樱花粉 + 翡翠绿 |

### 坐标系规则（关键）

- **POI 和前端**：GCJ-02（高德坐标系）
- **OSM 路网**：WGS-84
- **API 边界**：入参 `gcj02_to_wgs84()`，出参 `wgs84_to_gcj02()`
- 不转换会导致 50-200 米偏移 — 每个路径端点调用都必须转换

### TaskIntent 数据结构（`agents/parser.py`）

系统中流转的核心数据类型：
- `task_type`：`path_planning` | `poi_query` | `help` | `unknown`
- `start` / `end`：`PoiRef`，含 `name`、`type`（poi/coord）
- `constraints`：`{distance, slope, scenery}` — 硬约束等级
- `weights`：`{distance, slope, scenery}` — 软成本权重（null = 使用默认值）
- `weight_source`：`explicit_nl` | `shortcut` | `default`
- `ambiguity`：起终点无法解析时的错误/引导信息

### 关键设计决策（详见 `project-docs/06_DECISIONS.md`）

- **DEC-006**：LLM 选用 DeepSeek V4-Flash，通过 OpenAI SDK 调用（model: `deepseek-chat`，base_url: `https://api.deepseek.com/v1`）
- **DEC-010**：LLM 直接输出 `weights` 权重值（核心研究链路：NL → 空间认知 → GIS 决策），而非规则映射
- **DEC-011**：约束（硬过滤）与权重（软成本）分离 — "避开陡坡"意味着禁止 `slope_level=5` 的路段，而不仅是降低偏好
- **DEC-012**：使用 `network_type="walk"` 全量步行路网（不再二次筛选 footway/path）— OSM 在武大校园的 footway 覆盖率仅约 70%
- **DEC-013**：Parser 采用双轨策略 — LLM 主流程 + 规则后处理覆盖 `help`/`unknown`/`poi_query` 分类
- **DEC-004（修订）**：默认权重 `{distance: 0.5, slope: 0.2, scenery: 0.3}` — 无偏好时以距离为主导

## 环境配置

将 `.env.example` 复制为 `.env`，设置：
- `DEEPSEEK_API_KEY` — DeepSeek API 密钥（LLM 调用）
- `AMAP_KEY` — 高德 JS API 密钥（前端地图）
- `FLASK_ENV` — `development`（开启 debug）或 `production`（限制 CORS）
- 可选：`WHU_BBOX_*` 覆盖校园边界框

## 数据标注流程

路段坡度/景观数据由熟悉武大校园的人手动标注：

1. `python scripts/export_edges_for_annotation.py` → 生成路段清单 + Folium 可视化地图
2. 在 CSV 中逐条标注 `slope_level`（1-5）和 `scenery_level`（1-5）
3. `python scripts/csv_to_json.py data/road_annotations.csv` → 生成 `data/road_annotations.json`
4. 启动时 `spatial/network.py` 自动将标注合并到路网边属性
5. 覆盖率 < 80% 触发降级模式（未标注路段仅按距离成本计算）

## 任务管理

项目使用 `.superpowers/sdd/` 存放任务简报和实现报告。任务状态记录在 `project-docs/05_TASKS.md`（9 个阶段共 32 个任务）。当前进行中的任务记录在 `.superpowers/sdd/progress.md`。
