# 珞珈智行（WHU-Walker）

武汉大学校园空间智能体。用户可用自然语言提出地点、出行方式、途经点、时间和偏好需求；Agent 调用地点检索、路线规划、天气和路况等工具，向网页和 Android App 返回路线与解释。在线地址：[whuspati.online](https://whuspati.online)。

> 截至 2026-10-01 的进度、基础评测和未完成验收项见[项目进度与基础评测](docs/development/18_项目进度与基础评测_20261001.md)。本页只作入口；旧设计文档中的数量和部署状态可能是历史快照。

## 当前组成

| 部分 | 当前实现 |
|---|---|
| Agent | LangGraph 编排、14 个工具、版本化会话与任务状态；PostgreSQL 持久化，后台任务 worker |
| 空间计算 | NetworkX/OSMnx 路网、校方课程数据融合、分方式通行过滤、距离/坡度/景观权重 |
| 地图与数据 | 高德底图与线上 API；正式库 440 个 POI、17 个校门、5,087 个节点和 14,622 条有向边 |
| 用户端 | 手机与电脑网页、Android WebView App、语音输入/播报、路线切换和步行导航 |
| 管理端 | 独立 `/manager` 界面，人工维护路况；视觉事件审核管线已有代码，生产视觉 worker 尚未启用 |

日常通勤与“最短路径”策略的距离/坡度/景观权重为 `1/0/0`；推荐、风景优先和平坦优先使用其他预设。路线可计算或画出，不等于每条边都已实地证明可通行。当前开放候选、风险路段与路线样本仍需核查，详见评测报告。

## 本地运行与检查

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python app.py
```

按 `.env.example` 配置自己的服务凭据。项目数据和路网缓存按仓库及开发文档的说明准备。

```powershell
python scripts/validate/validate_all_data.py
python scripts/validate/audit_campus_master.py
python scripts/validate/check_deployment_readiness.py
python -m pytest tests/ -q
```

前两项检查数据结构和来源绑定；发布就绪检查还要求候选裁决、人工通行证据和路线对照，当前会返回未通过。请勿把结构校验通过写成实地质量验收通过。

## 文档

- [当前交接说明](HANDOFF.md)
- [项目进度与基础评测](docs/development/18_项目进度与基础评测_20261001.md)
- [文档索引](docs/README.md)
- [本地研究资料索引](research/README.md)

## 许可

项目代码的许可见仓库声明。OSM 派生数据、校方资料和地图商 API 各有不同的使用边界；引用、再分发或公开数据前应逐项核对来源与授权。
