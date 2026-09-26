# 珞珈智行 (WHU-Walker) 🌸

武汉大学校园空间智能体 —— 用一句自然语言解决"去哪儿、怎么走"：全 Agent 理解需求、调用空间工具，在三校区路网上规划多因素最优路径，支持步行/骑行/驾车、语音输入、步行导航，提供 PWA 与 Android App。

- 在线访问：**https://whuspati.online**
- Android：https://whuspati.online/app/whu-walker.apk （v1.4.1，应用内自动更新）

## 核心特性

- 🤖 **全 Agent 交互**：Plan-Act-Observe 循环 + function calling。"上课路上顺便买杯咖啡""带我逛赏樱路线""我这到樱顶"都由模型自主编排工具
- 🗺️ **多因素路径规划**：距离 / 坡度 / 景观加权（LLM 直出权重），硬约束（避陡坡）与软偏好分离；推荐线与最短线双线对比
- 🚶🚴🚗 **三种出行方式**：步行默认；骑行/驾车一票否决台阶电梯，模式默认权重各自调优
- 🧠 **两层记忆**：静态校园任务卡 + 从采纳行为 EMA 学习的个人偏好画像
- 🚧 **实时路况**：封路/施工/积水/事故/活动绑定路段、定时生效，三种方式统一绕行；管理端双通道鉴权
- 🌦️ **实时天气** + 🌸 **季节场景**（赏樱等任务卡经验）
- 🎙️ **按住说话语音输入** + 🔊 **语音播报** + 🧭 **路口级步行导航**（路段方位角纠偏、偏航静默重算）
- 📱 **PWA + Android WebView 壳**：Leaflet 本地化地图，App 原生桥提供定位/语音/TTS

## 技术栈

| 层级 | 选型 |
|---|---|
| Agent/后端 | DeepSeek（`deepseek-chat`，OpenAI SDK）· Flask · gunicorn |
| 空间计算 | OSMnx + NetworkX · 多因素加权 Dijkstra · DEM 坡度 |
| 前端 | Leaflet 1.9.4 + 高德 GCJ-02 瓦片 · 原生 JS · PWA |
| 移动端 | Android WebView 壳（minSdk 26），JS 桥：定位/ASR/TTS/安装 |
| 数据 | 420 POI · 12 校门 · 12,550 边裁剪路网 · GCJ-02 ⇄ WGS-84 |
| 部署 | 腾讯云 CVM + Caddy 自动 HTTPS + systemd |

## 快速开始

```bash
git clone <repo-url>
cd campus-spatial-intelligence-agent
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # 填入 DEEPSEEK_API_KEY、AMAP_KEY 等
python scripts/validate/validate_osm_network.py   # 首次下载路网（约 30s）
python app.py                     # http://localhost:5000
```

试试：
- 「从珞珈门到樱顶，避开陡坡」
- 「去教五上课路上顺便买杯咖啡」
- 「第一次来武大，带我逛一条赏樱路线」
- 「现在哪里在修路？」（按 🎙️ 也可以直接说）

## 测试与校验

```bash
python -m pytest tests/ -q               # 381 passed, 5 skipped, 1 xfailed
python scripts/validate/validate_all_data.py
```

## 文档

完整文档见 [docs/](docs/)：[产品介绍](docs/01_产品介绍.md) · [架构设计](docs/05_架构设计.md) · [API 参考](docs/04_API参考.md) · [数据字典](docs/06_数据字典.md) · [部署指南](docs/03_部署指南.md) · [移动端与语音导航](docs/10_移动端与语音导航.md) · [故障排查](docs/09_故障排查.md)。

架构决策记录在 [docs/development/](docs/development/)（DEC-001~020 v0.1 卷 + DEC-021 起 V2 卷）。

## License

MIT
