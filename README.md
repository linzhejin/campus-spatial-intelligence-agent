# 漫步珞珈 (WHU-Walker) 🌸

武汉大学校园空间智能体 — 多因素路径规划与 NL 交互。

## 15 分钟 Hello World

### 1. 环境准备
```bash
git clone <repo-url>
cd campus-spatial-intelligence-agent
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 配置环境变量
创建 `.env` 文件：
```env
DEEPSEEK_API_KEY=sk-your-deepseek-key
AMAP_KEY=your-amap-js-api-key
```

### 3. 初始化路网
```bash
python scripts/validate_osm_network.py
```
首次运行会自动下载武大周边 OSM 路网数据（约 30s），后续从缓存加载（<1s）。

### 4. 启动服务
```bash
python app.py
```
打开浏览器访问 `http://localhost:5000`。

### 5. 试试这些 Query
- "从牌坊到樱顶怎么走"
- "我想避开陡坡，从行政楼到枫园"
- "从总图书馆到凌波门，走风景好的路线"

## 技术架构

| 层级 | 技术选型 |
|------|---------|
| 前端 | 高德 JS API 2.0 + Vanilla JS + PWA |
| 后端 | Flask + gunicorn |
| 路网 | OSMnx + NetworkX (WGS-84) |
| AI | DeepSeek V4-Flash (OpenAI SDK 兼容) |
| 坐标 | GCJ-02 ↔ WGS-84 双向转换 |
| 部署 | Render (新加坡) |

## 核心特性

- 🗺️ **多因素路径规划**: 距离、坡度、景观三维加权
- 💬 **NL 交互**: 自然语言输入 + 快捷按钮 + 多轮对话
- 🏔️ **智能避坡**: 硬约束过滤陡坡路段，含兜底降级策略
- 🌸 **季节感知**: POI 标注季节标签（春樱/秋桂/冬梅）
- 📱 **PWA 支持**: 可添加到手机主屏幕
- 🎨 **珞珈主题**: 樱花粉 + 翡翠绿视觉系统

## API 端点

| Method | Path | 说明 |
|--------|------|------|
| POST | /api/parse | NL → 结构化意图 |
| POST | /api/route | 意图 → 路径规划 |
| POST | /api/chat | 一站式 NL → 解析+路径+解释 |
| POST | /api/candidates | 场景3候选POI |
| GET | /api/pois | POI 列表 |
| GET | /api/pois/<name> | 单POI查询 |
| POST | /api/network/init | 路网初始化 |

## 部署 (Render)

1. Fork 本仓库
2. 在 Render Dashboard 创建新的 Web Service
3. 连接 GitHub 仓库（render.yaml 自动检测）
4. 设置环境变量：
   - `DEEPSEEK_API_KEY`: DeepSeek API Key
   - `AMAP_KEY`: 高德 JS API Key
5. 在高德控制台设置 Referer 白名单：`*.onrender.com`
6. Deploy → 等待构建完成 → 获取 `*.onrender.com` 公网 URL

## 项目文档

| 文档 | 说明 |
|------|------|
| [PRD](project-docs/01_PRD.md) | 产品需求文档 |
| [TDD](project-docs/03_TDD.md) | 技术设计文档 |
| [ARCH_REVIEW](project-docs/04_ARCH_REVIEW.md) | 架构审核 |
| [TASKS](project-docs/05_TASKS.md) | 32 任务清单 |
| [DECISIONS](project-docs/06_DECISIONS.md) | 技术决策日志 |
| [QA_REPORT](project-docs/08_QA_REPORT.md) | 测试与质量报告 |

## License

MIT
