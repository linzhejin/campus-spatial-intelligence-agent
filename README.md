# 珞珈智行 (WHU-Walker) 🌸

武汉大学校园空间智能体 — 多出行方式路径规划与自然语言交互。

## 核心特性

- 🗺️ **多因素路径规划**：距离、坡度、景观三维加权
- 🚶🚴🚗 **三种出行方式**：步行 / 骑行 / 驾车，自然语言识别 + 切换按钮
- 💬 **NL 交互**：一句话完成解析 + 规划 + 解释，多轮对话承接上下文
- 🏔️ **智能避坡**：DEM 坡度数据，"膝盖不好"自动避开陡坡
- 🌸 **季节感知**：POI 季节标签，推荐当季景观路线
- 🚧 **路况上报**：管理员登录后可上报封闭/施工/事故，按类型分级处理
- 📱 **PWA**：可添加到手机主屏幕，Service Worker v3 版本化缓存

## 在线访问

http://152.136.102.172:5000

## 快速开始

```bash
git clone <repo-url>
cd campus-spatial-intelligence-agent
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

创建 `.env`（参考 `.env.example`）：
```env
DEEPSEEK_API_KEY=sk-your-deepseek-key
AMAP_KEY=your-amap-js-api-key
AMAP_SECURITY_CODE=your-amap-security-code
```

初始化路网 + 启动：
```bash
python scripts/validate/validate_osm_network.py
python app.py
```

访问 http://localhost:5000，试试：
- 「从珞珈门到樱顶」
- 「从珞珈门骑车到樱花大道」
- 「我想避开陡坡，从行政楼到枫园」

## 技术栈

| 层级 | 选型 |
|---|---|
| 前端 | 高德 JS API 2.0 + Vanilla JS + PWA |
| 后端 | Flask + gunicorn |
| 路网 | OSMnx + NetworkX (WGS-84) |
| AI | DeepSeek (OpenAI SDK 兼容) |
| 坐标 | GCJ-02 ↔ WGS-84 双向转换 |
| 部署 | 腾讯云 CVM + systemd |

## 文档

完整文档位于 [docs/](docs/)：

- [产品介绍](docs/01_产品介绍.md)
- [快速开始](docs/02_快速开始.md)
- [部署指南](docs/03_部署指南.md)
- [API 参考](docs/04_API参考.md)
- [架构设计](docs/05_架构设计.md)
- [数据字典](docs/06_数据字典.md)
- [前端指南](docs/07_前端指南.md)
- [开发指南](docs/08_开发指南.md)
- [故障排查](docs/09_故障排查.md)
- [数据校验报告](docs/武汉大学三校区步行导航数据校验报告.docx)

开发期历史文档：[docs/development/](docs/development/)

## 测试

```bash
pytest  # 232 passed
python scripts/validate/validate_all_data.py
```

## License

MIT
