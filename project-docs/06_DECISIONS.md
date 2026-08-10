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
