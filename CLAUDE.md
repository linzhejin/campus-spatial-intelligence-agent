# CLAUDE.md

本文件为 AI 编码助手提供代码库工作指南。**内容以代码为事实源**；详细文档在 `docs/`，架构决策在 `docs/development/06_DECISIONS.md`（v0.1）与 `12_V2决策日志.md`（v2，现行）。

## 项目概述

**珞珈智行 (WHU-Walker)** — 武汉大学校园空间智能体（在线 https://whuspati.online）。自然语言 → Agent 工具调用 → 三校区多因素路径规划 → 地图/语音交付。支持步行/骑行/驾车、按住说话语音输入、步行导航、Android APK。

核心研究问题：*大模型能否把模糊的自然语言空间偏好转换为可计算的空间参数（权重/约束）？*

## 常用命令

```bash
python app.py                      # Flask 开发服务器，端口 5000
python -m pytest tests/ -q         # 全部测试（373）
python -m pytest tests/test_routing.py -q
python scripts/validate/validate_osm_network.py   # 路网覆盖率校验
python scripts/validate/validate_all_data.py      # POI 数据校验
curl -s http://localhost:5000/health
```

## 系统架构（全 Agent，勿与旧 parser 管道混淆）

```
POST /api/chat（api/routes.py）
  → agents/planner.py  run_agent()  Plan-Act-Observe 循环（MAX_TURNS=6, 40s 预算）
      system 上下文：agent_system.txt + knowledge 任务卡 + profile 画像
                    + 出行方式/GPS/途经点/多轮历史
  → agents/tools.py  9 个 function-calling 工具（LLM 只决策，不计算）
      resolve_poi / search_poi_candidates
      plan_route / plan_via_route / plan_tour
      get_weather / list_road_conditions
      ask_user / suggest_followup
  → spatial/ 纯算法：routing.py（模式过滤→硬约束→路况/天气成本→加权 Dijkstra）
  → 路径包（GCJ-02）→ 前端 Leaflet / navigation.js
```

**旧管道**（parser.py → routing.py → explainer.py）只在 LLM API 故障抛 `PlannerError` 时兜底，不是并行通道。`parser.detect_travel_mode` 关键词纠偏仍被两管道共用。

### 模块职责

| 模块 | 职责 |
|---|---|
| `agents/planner.py` | Agent 循环、护栏（轮次/时长）、响应组装、DSML 清洗 |
| `agents/tools.py` | 9 工具 schema 与执行器，LLM 与空间层唯一通道；坐标回注压缩、artifact 直传 |
| `agents/parser.py` | 旧管道 NL→TaskIntent（DeepSeek temperature=0 + Pydantic + 规则后处理双轨） |
| `agents/explainer.py` | 旧管道解释/闲聊/跟进建议（模板兜底） |
| `agents/knowledge.py` | 任务卡关键词召回（≤2 张/轮，当季加权） |
| `agents/profile.py` | 服务端 EMA 画像（α=0.3，≥3 次采纳才注入，仅 route_accept 学习） |
| `spatial/routing.py` | **核心算法**。MODE_DEFAULT_WEIGHTS、filter_graph_for_mode、resolve_weights、compute_route/via/tour、build_turn_by_turn |
| `spatial/network.py` | OSMnx 路网缓存；合并 road_annotations 与 edge_overrides |
| `spatial/poi.py` | 420 POI、别名/中文数字归一、模糊匹配、类别检索、同分歧义 |
| `spatial/road_conditions.py` | 路况 CRUD、CONDITION_EFFECTS、边吸附（30m）与边链扩展、时间窗 |
| `spatial/coord_transform.py` | GCJ-02 ⇄ WGS-84 |
| `spatial/weather.py` | 高德天气 + 出行影响分级 |
| `static/` | index.html、js/app.js、navigation.js、voice-input.js、voice-output.js、sw.js、vendor/leaflet |
| `android-app/` | WebView 壳 MainActivity.java（定位/ASR/TTS/安装桥），当前 v1.4.1 versionCode 6 |

## 关键事实（改代码前必读）

- **默认权重按方式**（routing.py `MODE_DEFAULT_WEIGHTS`，唯一事实源）：walk {0.8,0.05,0.15}、bike {0.6,0.25,0.15}、drive {0.85,0.05,0.10}。注意 config.py 里 0.5/0.2/0.3 旧常量已无引用，别用它。
- **约束 ≠ 权重**（DEC-011）：硬约束过滤不可通行边（slope=avoid 删 level=5），软权重进成本函数 `Cost = w_d·D + w_s·S + w_v·(1−V)`；路径上限 min(最短×3, 2000m)。
- **路况全方式生效**：closure 全 block、construction 步行 1.5×/骑行驾车 block、flooding 驾车 1.5×/其余 block、accident 步行骑行 1.3×/驾车 block、event 步行骑行 1.2×/驾车 block；不可达降级大惩罚。
- **台阶/电梯/扶梯** bike/drive 一票否决；步行台阶 2.5×；edge_overrides.json 208 边人工覆盖（穿楼封禁、食堂 10× 防穿楼）。
- **坐标系**：POI/前端/路况点击 = GCJ-02；OSM 路网/DEM/API 入参 GPS = WGS-84。边界必须转换；前端只准 GCJ-02 瓦片（高德 webrd/webst），禁接 OSM/Esri。
- **POI 纪律**：仅三学部，排除校外/居民区，is_minor 标小商铺；别名须含数字归一且宽泛片区词不扩散。
- **路线成功即结束 Agent 循环**，不让 LLM 再调 suggest_followup（DSML 泄漏防护）。
- **切换方式**用上次路线起终点坐标直接重算，禁止把"我的位置"当 POI 名查。
- **前端请求序号**（requestSeq）防旧响应覆盖新状态；非路径响应要清空地图路线。
- **SW 纪律**：改 PRECACHE_URLS 内文件必须升 `sw.js` CACHE_NAME（当前 whu-walker-v47）。⚠️ 已知回归：前端目前**未注册** serviceWorker（"全新布局"提交移除），恢复缓存能力时要补回注册代码。

## 环境配置

`.env`（参考 .env.example）：`DEEPSEEK_API_KEY`、`AMAP_KEY`、`AMAP_SECURITY_CODE`、`AMAP_WEB_KEY`、`FLASK_ENV`、`SECRET_KEY`、`ROAD_CONDITION_ADMIN_PASSWORD`（网页 session）、`ROAD_CONDITION_ADMIN_TOKEN`（系统对接 X-Admin-Token）。
图片生成 API 仅 Trae IDE 可用，生产不可用。

## 数据与部署

- 数据文件见 docs/06_数据字典.md；改 POI/路网后跑 validate_all_data.py 与对应 pytest。
- 生产：腾讯云 CVM（ubuntu@152.136.102.172），路径 /home/ubuntu/campus-spatial-intelligence-agent，systemd `whu-walker`，Caddy HTTPS（whuspati.online）→ gunicorn 127.0.0.1:5000。
- gunicorn 必须 `--workers 2 --threads 4 --timeout 60 --max-requests 1000`；禁用 --preload/--keepalive/--max-requests-jitter。
- 发布流程（Gitee 为主源，GitHub 尽力）见 deploy-whu-walker skill：提交 → push gitee → ssh `git reset --hard origin/main` → restart → curl /health。
- APK 发布：升 versionCode/versionName（build.ps1）→ 更新 static/app/latest.json 的 sha256 → SW 对 APK network-only。

## 工作纪律

- 改 Agent 行为优先改 `agents/prompts/agent_system.txt` 与工具 schema；确定性逻辑放 tools.py/spatial，不要让 LLM 做计算。
- prompts 模板有进程内缓存，改后需重启服务。
- 新增工具：tools.py 加 schema + executor，同步 agent_system.txt 与 tests/test_planner.py。
