# 06 · 决策日志（Decision Log）

> 产出阶段: 全程
> 角色: 所有角色
> 状态: 持续更新

## 规则

涉及技术选型、架构调整、性能取舍、安全方案选择时，必须新增一条记录，编号从 DEC-001 递增。

## DEC-001: LLM 选型方向 — 国内低延迟模型

- 日期: 2026-08-09
- 阶段: Stage 1 PRD 修订
- Decision: LLM 服务选型方向
- 选择: 使用国内大模型 API（如 DeepSeek / 通义千问 / 智谱 GLM），不使用 OpenAI
- 原因: 国内访问 OpenAI 需跨境，RTT 200-500ms，两次串行 LLM 调用延迟 4-13s，SLA 15s 不可达。国内模型 RTT 20-50ms，两次调用 2-7s，P50 ≤ 15s 可达
- 放弃方案: OpenAI API（跨境延迟高，SLA 不可达）；解释改模板生成去掉第二次 LLM（降低解释质量）
- 影响: 具体模型选型留 Stage 3 TDD；PRD 非功能需求已写入"LLM 需支持国内低延迟访问，单次 P95 ≤ 3s"

## DEC-002: SLA 分开写 — 热启动与冷启动分离

- 日期: 2026-08-09
- 阶段: Stage 1 PRD 修订
- Decision: 端到端响应时间 SLA 策略
- 选择: P50 ≤ 15s（热启动），冷启动首次访问 ≤ 30s
- 原因: Render 免费版休眠后冷启动 0-30s 不可避免，与模型选型无关，需单独标注
- 放弃方案: 统一 ≤ 15s（冷启动时不可达）；放弃 Render 换 Vercel（增加部署复杂度）
- 影响: PRD 验收标准已修改为 P50 ≤ 15s + 冷启动 ≤ 30s；部署策略可能需调整（Stage 3）

## DEC-003: 坡度/景观数据方案 — 手动标注

- 日期: 2026-08-09
- 阶段: Stage 1 PRD 修订
- Decision: 多因素路径规划的数据源方案
- 选择: 坡度和景观由熟悉校园的开发者手动标注（1-5 级），不依赖 DEM 或自动计算
- 原因: 开发者是武大学生，对校园地形熟悉；手动标注数据质量高于 DEM 近似；景观维度无法从 DEM 推导
- 放弃方案: DEM 高程估算（需额外下载数据，景观无法推导）；路段到 POI 距离衰减近似（不准确）
- 影响: PRD 附录 C 已定义标注规范；Stage 3 需完成路网坡度/景观标注；重叠率 ≤ 70% 验收标准基于真实数据可达

## DEC-004: 多因素默认权重 — 景观 > 坡度 > 距离

- 日期: 2026-08-09
- 阶段: Stage 1 PRD 修订
- Decision: 多因素成本函数默认权重排序
- 选择: 默认权重 w_c > w_s > w_d（景观权重最高）
- 原因: 武大校园以景观为核心吸引力，用户主要需求是"多看景"；由用户（武大学生）确认
- 放弃方案: 等权（不体现校园特色）；距离优先（与高德无差异）
- 影响: config.py DEFAULT_WEIGHTS 需调整；具体数值 Stage 3 TDD 确定

## DEC-005: 多轮对话优先级提升 — P2 升 P1

- 日期: 2026-08-09
- 阶段: Stage 1 PRD 修订
- Decision: F-008 多轮对话的优先级
- 选择: 从 P2 升为 P1，纳入 MVP 范围（3 轮上下文 + 指代理解）
- 原因: 没有多轮对话 = 一次性工具，用户说错只能从头再来，留存率约等于零；微调操作（"换一条/更平坦/更短"）是地图产品标配
- 放弃方案: 保持 P2（留存率低）；仅保留 1 轮上下文（不够用）
- 影响: MVP 范围扩大 1 个功能；Stage 3 需实现上下文管理和指代理解

---

## Stage 3 TDD 决策（DEC-004 修订 + DEC-006 至 DEC-011）

## DEC-004 修订: 多因素默认权重调整 — 距离为基础

- 日期: 2026-08-09
- 阶段: Stage 3 TDD
- Decision: 多因素成本默认权重的具体数值
- 选择: `DEFAULT_WEIGHTS = {distance: 0.5, slope: 0.2, scenery: 0.3}`
- 原因: 默认场景"从A到B"用户未表达偏好，应接近普通导航基线；景观 0.5 会导致绕路违反用户直觉；坡度 0.2 反映"武大主要活动区相对平坦，坡度几乎不重要"的实际判断（武大学生开发者确认）
- 放弃方案:
  - 0.2/0.3/0.5（景观主导）：默认场景下绕路风险高，不符合用户直觉
  - 0.4/0.1/0.5（坡度极低）：景观权重 0.5 仍偏高
  - 0.4/0.3/0.3：景观与坡度并列，但实际场景中景观重要性 > 坡度
- 影响: `config.py` DEFAULT_WEIGHTS 调整；成本函数设计见 DEC-011

## DEC-006: LLM 具体选型 — DeepSeek V4-Flash

- 日期: 2026-08-09
- 阶段: Stage 3 TDD
- Decision: 国内 LLM 的具体模型选择（DEC-001 方向落地）
- 选择: DeepSeek V4-Flash（model id: `deepseek-chat`）
- 原因:
  - 性价比最高：输入 ¥1.01/百万 token、输出 ¥2.02/百万 token（2026-08 官方定价）
  - 缓存命中价 ¥0.02/百万 input（重复 system prompt 几乎不计费）
  - 国内 RTT 20-50ms + 推理 800-1500ms = 单次 1-2s，两次串行 P95 ≤ 3s 可达（满足 SLA）
  - OpenAI SDK 完全兼容，`config.py` 仅需改 `OPENAI_BASE_URL` 即可，无需替换 SDK
  - Function Calling 支持，社区反馈在结构化任务上稳定性好
  - 200 万免费额度，开发期 + 测试期完全够用
  - 单次 query 成本约 ¥0.0024（缓存优化后 ¥0.0008），月成本 < ¥10
- 放弃方案:
  - 通义千问 qwen3-max（¥2.5/¥10，价格 5 倍于 DeepSeek V4-Flash，性价比劣势）
  - 智谱 GLM-4.7-Flash（免费但生产环境限流策略不明，不建议作为生产依赖）
  - 智谱 GLM-4-Plus（¥5/¥5，性能相近价格更高）
  - DeepSeek V4-Pro（推理更强但价格 3 倍，本项目任务复杂度不需要 Pro 级推理）
  - 多模型 fallback（增加复杂度但本项目单模型已满足 SLA）
- 影响: `config.py` 修改：`LLM_MODEL = "deepseek-chat"`，`OPENAI_BASE_URL = "https://api.deepseek.com/v1"`；`requirements.txt` 保留 `openai>=1.0`

## DEC-007: 底图方案 — 高德 JS API + 坐标系转换层

- 日期: 2026-08-09
- 阶段: Stage 3 TDD
- Decision: 前端地图底图方案
- 选择: 高德 JS API（与 `00_PROJECT_CONTEXT.md` 第 4 项已确认一致）+ 后端 OSMnx 路网，API 边界做 GCJ-02 ⇄ WGS-84 坐标系转换
- 原因:
  - 武大校园内高德数据覆盖完整，OSM 在校园内有数据缺失（已知风险）
  - 高德原生优化移动端触摸交互
  - 高德矢量图层支持建筑轮廓可见（PRD F-004 要求）
  - 兼容主流移动端+桌面端浏览器（PRD §10.4 / §11.3 要求）
  - 个人开发者每日 30 万次配额足够
- 关键技术风险与应对: 后端 OSMnx 路网用 WGS-84，前端高德用 GCJ-02，两套坐标系偏移 50-200 米。`spatial/coord_transform.py` 模块封装转换：入参（前端→后端）GCJ-02→WGS-84，出参（后端→前端）WGS-84→GCJ-02
- 放弃方案:
  - OSM + Leaflet.js：武大校园内 OSM 数据缺失（已知风险），中文标注不完整，移动端体验需手动优化
- 影响: 前端引入高德 JS API；新增 `spatial/coord_transform.py` 模块

## DEC-008: 数据存储方案 — POI JSON + 路网 GraphML + 多轮对话 localStorage

- 日期: 2026-08-09
- 阶段: Stage 3 TDD
- Decision: 数据存储介质选择
- 选择:
  - POI 数据：`data/pois.json`（从 `config.py` 迁出，启动时加载到内存）
  - 路网数据：`data/whu_road_network.graphml`（OSMnx 首次下载，后续直接加载）
  - 路段坡度/景观标注：`data/road_annotations.json`（手动标注，Stage 4 完成，启动时 merge 到路网 edge 属性）
  - 多轮对话上下文（F-008）+ 用户偏好（F-009）：浏览器 localStorage（key: `whu_walker:context:{sessionId}` + `whu_walker:preferences`）
- 原因:
  - JSON 文件解耦数据与配置、可热加载、便于扩展、版本可控
  - SQLite 对 V1（12-20 个 POI、80-120 路段）杀鸡用牛刀
  - localStorage 与 PRD F-009 描述一致（"存储在 localStorage，不涉及账号系统"）
  - Render 免费版无持久化存储，服务端 session 不可靠
- 放弃方案:
  - config.py 硬编码 POI（数据与配置耦合，V2 扩展时重构）
  - SQLite 数据库（V1 数据量小，引入额外复杂度）
  - 服务端 session（Render 免费版冷启动后丢失）
  - 服务端 + Redis（Render 免费版不支持）
- 影响: 新增 `data/pois.json`、`data/road_annotations.json`；前端 localStorage 使用规范写入 TDD

## DEC-009: API 风格 — RESTful 薄 API 层 + 5 个端点

- 日期: 2026-08-09
- 阶段: Stage 3 TDD
- Decision: 后端 API 接口风格
- 选择: RESTful，5 个端点：
  - `POST /api/parse`：NL → 任务意图（独立调用，支持多轮上下文）
  - `POST /api/route`：任务意图 → 推荐路线 + 最短路径 + 成本 + POI + 解释
  - `POST /api/chat`：一站式接口（NL → 全流程，F-001 主流程）
  - `GET /api/pois`：POI 列表
  - `GET /api/pois/{name}`：单个 POI 查询
- 原因:
  - 解析/路径/解释可独立调用，便于多轮对话上下文管理
  - `/api/chat` 一站式接口简化前端调用主流程
  - RESTful 标准、易调试、易扩展
- 放弃方案:
  - GraphQL（V1 简单查询场景过度设计）
  - RPC 风格（不符合 Web 标准）
- 影响: 新增 `api/routes.py` 模块；`app.py` 注册路由

## DEC-010: 权重生成机制 — LLM 直接生成 + null fallback

- 日期: 2026-08-09
- 阶段: Stage 3 TDD
- Decision: 多因素权重的生成方式
- 选择: LLM 在解析阶段直接输出 `weights` 字段；用户输入无任何空间偏好时输出 `null`，后端 fallback 到 `DEFAULT_WEIGHTS`；后端校验每个权重 ∈ [0.05, 0.8] 并归一化
- 原因:
  - 体现 LLM → 空间认知 → GIS 决策 的核心研究链路（CSIA 项目核心假设）
  - LLM 负责空间偏好理解并直接生成用户效用参数，而非规则系统映射
  - null fallback 保留默认场景的稳定性
  - 后端校验防止 LLM 输出极端值
  - 符合本科科研原型定位，比"两层规则+LLM 调整"更有方法创新性
- 核心研究问题: LLM 能否作为人类空间偏好的转换器，将模糊语言转化为空间计算参数？
- 放弃方案:
  - 规则系统生成权重 + LLM 调整（两层规则）：本质是专家系统，创新性弱
  - LLM 只输出约束等级，规则映射权重（丢失 LLM 对语义细节的判断）
  - 完全无 LLM 参与，纯规则（不符合 CSIA 项目核心假设）
- 影响:
  - Prompt 工程需设计 few-shot 示例稳定 LLM 输出
  - 附录A 15 条 query 作为 golden test 保证一致性
  - LLM 解析输出 schema 扩展 `weights` 字段

## DEC-011: 约束与权重分离 — 硬约束过滤 + 软成本优化

- 日期: 2026-08-09
- 阶段: Stage 3 TDD
- Decision: 约束（constraints）与权重（weights）的关系
- 选择: LLM 同时输出 `constraints`（硬约束）和 `weights`（软成本）；GIS 路径计算分两步：
  1. **硬约束过滤**：基于 `constraints` 过滤不可通行路段（如 `slope=avoid` 时过滤 `slope_level=5`，`slope_level=4` 加 2× 距离惩罚）
  2. **软成本优化**：在可行路线集合上用 `weights` 计算成本，选最优路线
- 成本函数（三因素，景观作为收益取 1-V）：
  ```
  Cost = w_d × D + w_s × S + w_v × (1 − V)
  ```
  其中 D = 距离成本，S = 坡度成本，V = 景观收益（1-5 归一化到 [0,1]）
  三指标方向统一：越小越好
- 原因:
  - 权重 ≠ 约束：用户说"避开陡坡"不仅是"不喜欢"，而是"禁止"
  - 高权重只是降低偏好，不是禁止；硬约束才能保证"避开"
  - 标准优化思想：硬约束 → 可行路线集合 → 软优化 → 最佳路线
  - 景观作为"收益"取 1-V 转化为成本，三个指标方向统一为"越小越好"，数学上更优雅
- 硬约束规则:
  - `slope=avoid`：`slope_level=5` 完全过滤；`slope_level=4` 加 2× 距离惩罚；兜底：若无可行路径，自动放宽到 `slope_level=4` 可通行（加 3× 距离惩罚）
  - 其他约束等级（normal/any）：不过滤
- 放弃方案:
  - 仅软成本无硬约束（"避开陡坡"无法保证）
  - 仅硬约束无软成本（无法处理"想多看景"等软偏好）
- 影响:
  - LLM 输出 schema 同时含 `constraints` 和 `weights`
  - `spatial/routing.py` 实现两步路径计算
  - 解释生成时分别说明"已过滤 X 路段（硬约束）"和"已优化 Y 偏好（软成本）"

## DEC-012: OSM 步道验证覆盖率不足 80% 降级方案

- 日期: 2026-08-11
- 阶段: Stage 0 P0（路网验证失败后）
- Decision: OSM 路网 footway/path 覆盖率仅 70%（< 80% 阈值），触发 R3 降级
- 选择: **方案 A · 取消 highway 二次筛选，使用 osmnx `network_type="walk"` 全量路网**
  - 原 `filter_pedestrian_edges()` 严格筛选 `highway ∈ {footway, path}`，导致 3,844 条路段切成 235 个孤岛
  - 改为直接使用 osmnx 下载的 walk 全量路网（含 residential、service、pedestrian、steps 等步行可达路段类型），不再二次过滤
- 原因:
  - OSM 在武大校园内 footway/path 标注不完整，很多实际步道被标为 residential/service 等，严格筛选导致路网严重碎片化
  - osmnx `network_type="walk"` 已内置过滤器，下载的都是适合步行的路网，二次筛选过严
  - 方案 A 工作量最小（0 人工），相比手动补段（数小时）和高德 API 降级（架构大改）最优
- 放弃方案:
  - **方案 B · 手动补段**：用 QGIS 手动连接 235 个连通分量的断点，数小时人工投入，前期不现实
  - **方案 C · 降级为高德路径 API**：完全放弃 OSM 自建路径计算，"LLM→空间认知→GIS决策"的研究价值丧失
- 影响:
  - 路段数从 ~3,800 增至 ~9,000（包含所有 walk 类型），最大分量节点数应显著提升
  - 路径计算会包含少量 residential/service 路段，但 `network_type="walk"` 已排除机动车专用道，步行可达性安全
  - T-001 验证脚本需同步修改，且后续 spatial/network.py 的实际路网下载逻辑也需对齐（不再二次筛选 footway/path）

## DEC-013: T-011 Parser 双轨策略 — 规则兜底分类 + LLM 精细解析

- 日期: 2026-08-11
- 阶段: Stage 6 T-011（NL 解析 Agent 实现）
- Decision: Parser 对 task_type 的判定采用「规则优先覆盖 + LLM 精细解析」的双轨策略
- 选择:
  1. **LLM 主流程**：保留原 parse_query 内的 DeepSeek 调用 + 3 次重试 + Pydantic 校验（不动函数体）
  2. **规则后处理（_rule_based_classify）**：在 `_t011_post_process` 中用关键词规则对以下 3 类做**强制覆盖**，不依赖 LLM 是否命中：
     - `unknown`：命中闲聊关键词（天气/吃饭/笑话/你好...） → 强制 task_type=unknown + 引导语
     - `help`：命中功能/推荐关键词（你能做什么/推荐景点/怎么用...） → 强制 task_type=help
     - `poi_query`：命中「X 在哪 / X 介绍 / X 是什么」句式，且不含 A→B 路径动词 → 强制 task_type=poi_query，start=POI，end=null（不强行配对）
  3. **LLM + Prompt 层面同步注入**：parse_system.txt 的任务类型从 2 项扩展为 4 项，并加入对应 few-shot；即使规则层被绕过，LLM 层也能输出正确的 help/unknown
- 原因:
  - **稳定性**：TASKS §7/8/10 对 3 类对话有严格验收，单靠 LLM 可能因温度/上下文波动漏判；规则层提供 P100 级确定性兜底
  - **可维护性**：新增无关关键词只需追加 `_UNRELATED_KEYWORDS` 列表，无需改 Prompt 或重调 few-shot
  - **SLA 友好**：规则后处理为纯 CPU 字符串匹配，耗时 <1ms，不影响 P95 ≤3s 延迟
  - **研究链路不破坏**：路径规划的核心权重/约束仍由 LLM 直接生成（保留 DEC-010 研究链路），仅 help/unknown/poi_query 的 task_type 分类被规则覆盖，不影响 CSIA 核心假设的验证
- 放弃方案:
  - **纯 LLM 4 分类**：无规则层，仅靠 Prompt + few-shot 让 LLM 输出 4 类 task_type。风险：闲聊/POI 查询的句式波动大，可能把「樱顶在哪」误判成 path_planning 且乱配 end，违反 §7 验收
  - **纯规则全分类**：path_planning 也靠正则提取 A→B。风险：复杂偏好描述（"膝盖不好想看樱花走风景好的路"）规则维护成本高，且 DEC-010 的核心是"LLM 直接生成 weights"，违反研究定位
- 影响:
  - `agents/parser.py` 末尾追加 `_rule_based_classify`、`_t011_post_process` 等辅助函数（append-only，不动原 parse_query LLM 调用/重试/JSON 提取逻辑）
  - `agents/prompts/parse_system.txt` task_type 列表和 few-shot 对应扩展为 4 项
  - T-014 API 路由层对 4 种 task_type 分别分支（poi_query→POI 详情润色、help→功能说明+推荐列表、unknown→引导 Chip、path_planning→GIS 路径计算）

## DEC-014: T-011 多轮上下文与 ambiguity 补全 — 保留上轮约束权重合并策略

- 日期: 2026-08-11
- 阶段: Stage 6 T-011（NL 解析 Agent 实现）
- Decision: 多轮上下文承接（验收 9）和 ambiguity 补全（验收 11）采用「缺字段填充 + 保留上轮 constraints/weights」合并策略
- 选择:
  - **多轮上下文承接（_merge_context_with_intent）**：
    1. 若本轮 TaskIntent.start 为空，且 context.start 有 name → 用 context.start 填充 start
    2. 若本轮 task_type=path_planning 且 TaskIntent.end 为空，且 context.end 有 name → 填充 end
    3. 填充后若 start/end 齐全且原 ambiguity 仅为「请指定起点/终点」→ 清除 ambiguity（用户无需再被提示）
  - **ambiguity 补全（_resolve_ambiguity_completion）**：
    1. 仅当 context.previous_intent.ambiguity ∈ {「请指定起点」, 「请指定终点」} 且本轮 query 可 _fuzzy_match_poi_name 匹配到单个 POI 时触发
    2. 若上轮缺起点 + 本轮匹配 POI → start = 本轮 POI，end 从上轮 previous_intent.end 复制
    3. 若上轮缺终点 + 本轮匹配 POI → end = 本轮 POI，start 从上轮 previous_intent.start 复制
    4. **关键**：合并成功时，constraints 和 weights 都从上轮 previous_intent 复制，不使用 LLM 本轮重新识别的默认值（保证「膝盖不好 → 避开陡坡」等偏好在补全后不丢失）
  - **调用顺序**：`_t011_post_process` 中先执行 ambiguity 补全，再执行一般 context 承接，避免两种合并路径冲突
- 原因:
  - **用户体验**：用户说「我想看樱花膝盖不好」→ 后端回「请指定起点」→ 用户回「牌坊」，此时必须保留 slope=avoid 和 scenery=high 偏好，若仅补 start 而清空 constraints 会导致偏好丢失，用户需重复输入
  - **鲁棒性**：LLM 对单 POI 名（"牌坊"）的默认解析通常产出 constraints 默认值 + weights=null；上轮存储的约束/权重更可靠
  - **无冲突假设**：触发 ambiguity 补全的 query 严格限定为「单 POI 名 + 上轮 ambiguity 明确要求补起/终点」，不存在用户同时改偏好的语义冲突场景，保留上轮值安全
- 放弃方案:
  - **只补字段 + 用本轮 LLM 重新识别的 constraints/weights**：风险是单 POI 名 query LLM 识别为默认约束，用户偏好丢失
  - **让前端 routes.py 层做合并**：违反单一职责原则，合并逻辑应在 Parser 输出 TaskIntent 时完成，路由层只消费完整 TaskIntent
- 影响:
  - `agents/parser.py` 新增 `_merge_context_with_intent` 和 `_resolve_ambiguity_completion` 两个纯函数（可独立单测）
  - 前端 localStorage 的 whu_walker:context 结构必须包含 `previous_intent: {start, end, constraints, weights, ambiguity}` 字段快照（T-017 前端交互实现时对应）
  - T-014 routes.py 对应实现「ambiguity + context 字段合并」分支（验收 11 的端到端流程在路由层再兜底一次）

---

## DEC-015: TaskIntent 权重来源标注方式 — BaseModel 新增 Optional 字段
- 日期: 2026-08-11
- 阶段: Stage 6（T-024 快捷按钮与权重优先级）
- Decision: 权重来源（weight_source）元信息的存储方式
- 选择: 在 `TaskIntent` Pydantic BaseModel 中新增 `weight_source: Optional[Literal["explicit_nl", "shortcut", "default"]] = None` 可选字段，枚举值三选一：
  - `explicit_nl`：NL 输入明确表达了空间偏好（LLM 解析出 weights != null）
  - `shortcut`：通过快捷按钮模式传入（input_method=shortcut）
  - `default`：NL 输入无偏好 + 无快捷按钮（weights=null，后端 fallback 到 DEFAULT_WEIGHTS）
- 原因:
  1. **向后兼容**：Optional 字段且有默认值 None，旧代码不设置该字段也能正常实例化，不会破坏原 schema
  2. **类型安全**：Pydantic BaseModel 字段 + Literal 枚举，IDE 有类型提示、序列化/反序列化自动校验
  3. **语义清晰**：字段名直接表达"权重来源"含义，不污染 constraints 字段（constraints 语义是用户约束等级，不是来源）
  4. **便于传递**：从 parser → routes → explainer 链路中，model_dump() 序列化时自动带上该字段，无需单独处理 dict key
- 放弃方案:
  - **方案 A · 塞入 constraints dict**：如 `constraints["_weight_source"] = "explicit_nl"`，语义污染（constraints 本应是三因素等级），且无类型安全，后续人容易困惑
  - **方案 B · 单独 metadata dict 字段**：新增 `meta: Optional[dict] = None` 字段放 weight_source，扩展性好但过度设计，V1 只需要一个标注字段，dict 带来 key 拼写风险
  - **方案 C · 不进 schema，routes.py 单独推断**：不在 TaskIntent 存，routes.py 里根据 `input_method` + `weights` 是否 null 推断。缺点：推断逻辑重复（前端也需要知道），且无法被 serialize 后在前后端链路中一致传递
- 影响:
  - `agents/parser.py`：TaskIntent 模型新增字段，新增 `_annotate_weight_source` 辅助函数在返回前打标
  - `api/routes.py`：/api/parse 快捷模式也带上该字段；/api/chat 调用 generate_explanation 时传 weight_source 参数
  - `agents/explainer.py`：generate_explanation 新增可选 weight_source 参数，模板兜底解释中拼接"已按...推荐"说明来源

## DEC-016: T-005 Prompt Few-shot 补充策略 — Append-only 增量追加

- 日期: 2026-08-11
- 阶段: Stage 6-7 T-005 实施
- Decision: Prompt 模板 few-shot 不足时的补充策略
- 选择: **Append-only 增量追加**，不修改任何原有 prompt 行，仅在文件末尾追加 few-shot 块
  - parse_system.txt：原 0 个 few-shot → 追加 8 个（覆盖 path_planning 普通/避坡/最短/景观/缺起点 + poi_query + help + unknown 共 4 种 task_type）
  - explain_system.txt：原 1 个 few-shot → 追加 2 个（distance=short 场景 + scenery=high 模板兜底场景），共 3 个
- 原因:
  - Stage 6 计划明确要求「Append-only！」严禁误删/覆盖原有 prompt
  - few-shot 数量是硬验收指标（parse≥5、explain≥2），不达标则 T-011/T-012 无法通过
  - 增量追加零风险，不影响 parser.py/explainer.py 对 prompt 的读取逻辑
  - 额外覆盖 T-011 新增的 4 种 task_type（path_planning/poi_query/help/unknown）和 ambiguity 触发语义，提前对齐后续任务需求
- 放弃方案:
  - 直接重写整个 prompt 文件：可能破坏原有的规则说明或格式，风险过高
  - 仅补到刚好 5/2 个（即 parse 补 5 个、explain 补 1 个）：虽刚好达标但未覆盖 4 种 task_type，T-011 实施时还需再改，增加二次改动风险
- 影响:
  - parse_system.txt 行数从 51 行增至 77 行，few-shot 实际数量 8 个
  - explain_system.txt 行数从 20 行增至 34 行，few-shot 实际数量 3 个
  - 配套验证脚本 `scripts/validate_t005_prompts.py` 可重复执行验证 6/6 + 4/4 验收

## DEC-017: 路段标注 edge 匹配策略 — edge_id[u,v,k] 优先 + u/v 对兜底

- 日期: 2026-08-11
- 阶段: Stage 6 · T-026 实施
- Decision: `road_annotations.json` 标注数据 merge 到 NetworkX 图的边匹配策略
- 选择: **两级匹配策略**：
  1. **优先精确匹配**：解析 `edge_id` 字段为 `(u, v, k)` 三元组，和 NetworkX MultiDiGraph 的 `G.edges(keys=True)` 逐条对齐（字符串化 u/v + 整数化 k，避免 WGS-84 node id 类型不一致导致的匹配失败）
  2. **兜底 u/v 对匹配**：若 `edge_id` 解析失败或三元组未命中，则取标注中的 `u`、`v` 字段作为节点对，在该节点对下的所有并行边中选择第 0 条作为目标
  3. **容忍匹配失败**：某条标注无法匹配任何边时，静默跳过，不抛异常，仅影响覆盖率计算，最终覆盖率 < 0.8 时 routing 自动降级
- 原因:
  - TDD §4.2 定义的 `edge_id` 是 `[u, v, k]` 列表，和 NetworkX MultiDiGraph 的 key 结构一一对应，精确匹配准确率最高
  - 手动标注 CSV → JSON 转换时（csv_to_json.py）可能存在 edge_id 字符串化不一致（JSON 导出时 int 转字符串），字符串归一化匹配可避免类型不匹配
  - u/v 对兜底可兼容 `compute_route_with_annotations()` 中仅通过 `k == eid` 的旧匹配方式，以及只记录了 u/v 没有记录 k 的标注版本
  - 静默跳过匹配失败的边是 R5 风险（标注覆盖率不足）的要求，不应影响运行时稳定性
- 放弃方案:
  - **仅按 edge_id[u,v,k] 精确匹配**：鲁棒性不足，标注文件稍有类型不一致（int 对 str）就全部失配，覆盖率骤降为 0
  - **按欧氏距离最近边匹配**：计算复杂度高（N_edges × N_annotations），且相邻并行边几何上非常接近，容易误匹配
  - **按 name 属性模糊匹配**：路段 name 在 OSM 中大量为空，不可靠
- 影响:
  - `spatial/network.py:_merge_annotations()` 实现两级匹配 + `_parse_edge_id()` 做类型归一化
  - 覆盖率计算基于"实际成功匹配并写入 slope/scenery 的边数 / 总图边数"，而非"标注 JSON 中的条目数"
  - 匹配失败不崩、不 warn（避免日志噪音），仅通过覆盖率数值体现在 filter_status 降级标记上

## DEC-018: T-018 视觉/PWA 改动策略 — CSS Append-only + SW 显式三策略 + Manifest 主题色对齐

- 日期: 2026-08-11
- 阶段: Stage 6-7 · T-018 前端视觉设计与 PWA 配置
- Decision: T-018 14 条验收标准（7 基础 + 7 冷启动）的落地方式与风险控制
- 选择: **三文件分治 + 严格 append-only**：
  1. **`static/css/style.css`**：严禁修改/删除中间任何原有类，仅在文件末尾追加新规则；需覆盖的旧值用 `!important`（仅限 `.welcome-close`、`.help-btn` 两个按钮尺寸 36/32→44px 的 WCAG AA 达标场景）
  2. **`static/sw.js`**：整体重写为「三策略显式分发表」结构，按 TDD §9.4 注释标注①cache-first /②SWR /③network-only 三段，保留 PRECACHE_URLS 预缓存数组 + install/activate 生命周期不变
  3. **`static/manifest.json`**：仅改 2 个色值字段（`theme_color` 从蓝色 `#1976D2` → 樱花粉 `#E8929C`；`background_color` 从 `#F5F5F5` → 暖白底 `#FAF8F5`），其余 icons/shortcuts/name 均保持不动
- 原因:
  - Stage 6 Plan 明确第 4 条潜在风险：**覆盖原有视觉** → 必须 append-only，严禁在 style.css 中间插行或删改原类
  - T-018 §4 验收标准 44×44px 与原 `.welcome-close 36px`、`.help-btn 32px` 冲突，`!important` 覆盖是零风险方案（不触碰原规则）
  - SW 原实现仅做了"cache-first+fallback fetch"的混合，未显式区分 TDD §9.4 要求的三段式策略；重写分发表比在原代码上 patch 更清晰，自检脚本可通过字符串 `策略 ①②③` 精确校验
  - Manifest 原 `theme_color=#1976D2`（蓝）是 Material Design 默认色，与樱花粉/翡翠绿主题完全不符，修改为 `#E8929C` 可在 PWA 添加到主屏幕/Android 状态栏时显示品牌色，提升统一性
- 放弃方案:
  - **直接修改 CSS 中间原类的 36→44 值**：违反 append-only 承诺，可能破坏 welcome card 原布局（按钮位置 top/right 与尺寸强耦合），风险不可控
  - **SW 仅在末尾追加 patch 不重写**：旧 SW 没有 `/api/*` SWR 分支、没有 amap network-only 分支，追加补丁会与原 fetch 监听器重叠，可读性和可维护性差
  - **Manifest 新增 192/512 PNG 图标**：当前 SVG icons 已存在且 `sizes=192x192/512x512` 规范，PNG 需额外图片资源，验收标准未强制 PNG 格式，保持 SVG 可降低资源体积且避免生成二进制
- 影响:
  - `static/css/style.css` 新增 42 行（§4 触摸目标达标覆盖规则），总行数从 1260 → 1292，未删改任何原行
  - `static/sw.js` 总行数从 69 → 126，三策略注释自检可过，`validate_t018.py` §5/§5.1 双 PASS
  - `static/manifest.json` theme_color 从蓝→粉，background_color 统一，PWA 在桌面/移动端添加到主屏幕时状态栏色与樱花主题一致
  - 配套自检脚本 `scripts/validate_t018.py` 可重复执行，14/14 checks 稳定通过

## DEC-019: T-022 场景 3 候选 POI 交付方式 — /api/parse 响应内联附加 candidates 字段
- 日期: 2026-08-11
- 阶段: Stage 6-7 · T-022 场景 3 候选 POI 交互闭环
- Decision: 场景 3（start/end 候选 ≥ 2）的候选组合交付方式
- 选择: **方案 A · /api/parse 响应体内联附加 `candidates` 字段（无新增端点）**
  1. 在 `/api/parse` 响应中追加 `candidates` Optional 字段：
     - 场景 1（start/end 唯一匹配）：`candidates = [ {start: {...}, end: {...}, confirmed: true, ...} ]`（单元素数组，`confirmed=true` 表示可直接 compute_route，前端无需展示选择 UI）
     - 场景 3（start 候选 ≥ 2 或 end 候选 ≥ 2）：`candidates` 为 Top-3 `TaskIntent` 组合数组，每个组合含：
       - `start_candidate`: `{short_id: "S1"~"S3", poi: {...}, confidence_score: 0~1}`
       - `end_candidate`: `{short_id: "E1"~"E3", poi: {...}, confidence_score: 0~1}`
       - `combined_score`: 综合评分 0~1（start_confidence × end_confidence），按此降序 Top-3
  2. 原有 `ambiguity` 字段保持不变，仅当 `candidates.length > 1 || !candidates[0].confirmed` 时前端才展示选择 UI
  3. 新增前端 `window.renderCandidates(candidates_arr)` 公开函数，基于 `.ambiguity-container` 容器注入 3×3 候选卡片网格 + 「确认选择」按钮；确认后调 `window.onCandidateConfirmed(start_id, end_id)` 重新发起 `/api/chat`
- 原因:
  1. **零网络往返节省**：场景 3 的候选组合在 Parser 消歧阶段即可同步生成，无需额外 `/api/candidates` 端点，减少 1 次 HTTP 往返（从 2 次 → 1 次），对 P50 ≤ 15s SLA 友好
  2. **向后兼容（Plan 风险 4）**：`candidates` 为 Optional 字段，旧前端忽略此字段不影响原有逻辑；新增字段默认值为 `null` 或 `undefined`，旧代码不访问即不报错
  3. **单一职责清晰**：消歧逻辑（候选生成）属于 Parser 层职责，和 `/api/parse` 的"自然语言 → 结构化意图"职责一致，新增端点会导致消歧逻辑分散在路由层两个端点，可维护性差
  4. **前端实现简单**：场景 1（`confirmed=true`）的单候选，前端直接走原 `compute_route` 链路，无需特殊分支；场景 3 的 3×3 网格只需在 `.ambiguity-container`（T-017 已建）中注入，不改动原 HTML 结构
- 放弃方案:
  - **方案 B · 新增独立 `POST /api/candidates` 端点**：优点是职责解耦、响应体不受原 `/api/parse` schema 限制；缺点是多 1 次 HTTP 往返（延迟 ~50-200ms）、前端需先调 parse 再调 candidates 两步、路由层逻辑分散，与 Plan 第 ③ 条建议「省得再发一次请求」直接冲突
  - **方案 C · 仅返回 start_candidates + end_candidates 两个独立数组，不做 Top-3 组合**：优点是传输数据量略小；缺点是前端需要自行笛卡尔积组合并排序，逻辑复杂且容易和后端评分不一致，直接违反「后端 Top-3 组合」验收要求
- 影响:
  - `api/routes.py` 末尾 append-only 新增 `_gen_top3_candidate_combinations()` 辅助函数 + 在 `/api/parse` NL 分支返回前附加 `candidates` 字段（快捷模式也附加 confirmed=true 单候选，保证 schema 一致）
  - `static/js/app.js` 末尾 append-only IIFE，挂载 `window.renderCandidates` 和 `window.onCandidateConfirmed` 两个公开函数，基于已有 `.ambiguity-container` DOM 注入
  - 自检脚本 `scripts/T022_selfcheck.py` 覆盖 3 条验收：① 场景 1 candidates[0].confirmed=true 可直通 compute_route；② 场景 3 返回 Top-3 组合且 short_id/confidence 字段完整；③ 前端 window 对象上存在 renderCandidates 和 onCandidateConfirmed 两个函数

---

> 模版（新增决策时复制以下格式）：

## DEC-020: T-022 架构审核 N4 实现 — 独立 POST /api/candidates 端点

- 日期: 2026-08-11
- 阶段: Stage 6-7 · T-022 场景 3 候选 POI 交互闭环（架构审核 N4）
- Decision: 场景 3（start POI + POI type）的候选 POI 交付方式采取独立端点
- 选择: **新增独立 `POST /api/candidates` 端点**
  1. 端点职责单一：接收 `{start: {name}, poi_type, keyword}` 返回候选 POI 列表
  2. 每个候选包含：`name`, `type`, `scenery_score`, `distance_m`（路网距离或 haversine 兜底）, `description`
  3. 排序：scenery_score 降序 + distance_m 升序，Top 5
  4. 无候选时返回 `no_candidates` 错误码（404）+ 友好中文提示
  5. 前端 `window.showCandidateCards(candidates, startName)` 渲染可点击卡片
  6. 卡片点击自动构造 "从X到Y" 查询文本 → 填写输入框 → 触发提交按钮，复用现有路线规划流程
- 原因:
  1. **场景区分清晰**：此端点服务于"只有起点 + POI 类型"的场景（如"从牌坊出发，想去赏樱的地方"），与 /api/parse 的 NL 解析 + 消歧职责不同
  2. **减少 LLM 调用**：此场景不需要 LLM 解析自然语言（用户已通过快捷按钮或结构化输入指定 start 和 type），独立端点可跳过 Parser，降低延迟和成本
  3. **路网距离计算**：独立端点可以集成路网距离计算（Dijkstra），为候选排序提供更准确的依据
  4. **前端简洁**：直接通过 fetch 调用 `/api/candidates`，返回即渲染，无需经过 parse → route 两步
- 放弃方案:
  - **方案 A（DEC-017 原设计）· /api/parse 响应体内联 candidates**：适合"用户输入 NL 且有多候选"的歧义消解场景，但场景 3 不涉及 NL 解析和消歧，是 POI 类型查询 + 路网排序问题，放在 parse 响应中增加耦合
- 影响:
  - `api/routes.py` 新增 1 个端点（POST /api/candidates，~90 行）
  - `api/routes.py` imports 新增 `import networkx as nx` + `from spatial.routing import _path_length`
  - `static/js/app.js` 末尾 append-only IIFE（~200 行），挂载 `window.showCandidateCards` 和 `window.hideCandidateCards`
  - 自检脚本 `scripts/T022_selfcheck.py` 重写为 4 条验收（端点结构 + 前端卡片 + 点击规划 + 空候选处理）
  - 与 DEC-017 方案 A 互斥，N4 审核确认使用独立端点方案

---
> 模版（新增决策时复制以下格式）：
>
> ## DEC-XXX: {决策标题}
> - 日期: YYYY-MM-DD
> - 阶段: Stage {X}
> - Decision: {决策标题}
> - 选择: {最终选定方案}
> - 原因: {选择依据}
> - 放弃方案: {被否决的备选方案及否决原因}
> - 影响: {该决策对后续阶段的影响评估}
