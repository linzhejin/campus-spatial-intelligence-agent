# 04 · API 参考

- 业务端点前缀 `/api`（Blueprint `url_prefix="/api"`）；健康检查 `GET /health` 无前缀（另有 `/api/health` 别名）。
- 统一响应：成功 `{"data": {...}}`，失败 `{"error": "code", "message": "..."}`。
- 主入口是 **POST /api/chat**（全 Agent）；`/parse` 与 `/route` 是拆分式旧管道，Agent 故障时服务端内部也用它们兜底。

## 健康检查

### GET /health

负载均衡/监控探活，无需鉴权。

```json
{"project": "珞珈智行", "status": "ok", "timestamp": 1789540308}
```

## 对话与规划（主链路）

### POST /api/chat ⭐

一站式入口：NL → Agent 循环（工具调用）→ 路径 / 候选 / 澄清 / 闲聊。

**请求体**

| 字段 | 类型 | 说明 |
|---|---|---|
| `query` | string | 必填，≤500 字 |
| `context` | object | 多轮上下文（start/end/history/previous_intent） |
| `travel_mode` | string | walk / bike / drive，前端方式切换传入 |
| `coord_start` / `coord_end` | object | GPS 端点 `{lng, lat}`，**WGS-84**；"我这/到我这"用 |
| `coord_waypoints` | array | 地图途经点 `[{lng, lat}, ...]`，WGS-84 |
| `whu_uid` / `uid` | string | 前端生成的持久用户 ID（画像与埋点用） |

**响应 data：按 response_kind 分四种形态**

1) `task_type: "path_planning"`（Agent 调了 plan_route / plan_via_route / plan_tour）

```json
{
  "task_type": "path_planning",
  "response_kind": "route",
  "route_kind": "direct",
  "explanation": "已为你规划好从珞珈门到樱顶的步行路线，约 830 米、12 分钟。",
  "start": {"name": "珞珈门", "type": "poi"},
  "end": {"name": "樱顶", "type": "poi"},
  "recommended": [{"lng": 114.358, "lat": 30.533}],
  "shortest": [{"lng": 114.358, "lat": 30.533}],
  "steps": [{"seq": 0, "type": "depart", "text": "...", "cumulative_m": 0, "point": {"lng": 0, "lat": 0}}],
  "pois": [{"name": "万林博物馆"}],
  "recommended_length_m": 830.0,
  "shortest_length_m": 760.0,
  "duration_min": 12.0,
  "applied_weights": {"distance": 0.8, "slope": 0.05, "scenery": 0.15},
  "mode": "walk",
  "filter_status": "no_filter",
  "suggestions": [{"label": "风景更好的", "query": "..."}],
  "via": {"...": "plan_via_route 时出现"},
  "tour": {"...": "plan_tour 时出现"},
  "legs": ["两段/多段路径包，via/tour 时出现"]
}
```

坐标字段（recommended/shortest/steps.point/POI）一律 **GCJ-02**，可直接上图。

2) `task_type: "candidates"`：`{"message", "candidates": [...]}`，前端渲染候选地点卡片。

3) `task_type: "unknown"`（clarify 反问）：`{"message", "clarify": {"question", "options": [...]}}`。

4) `task_type: "chat"`：`{"reply": "..."}`。

LLM API 故障时自动落旧管道，响应结构与上述保持兼容（旧管道另有 help / unknown 等分支文案）。

### POST /api/parse（旧管道·拆分接口）

NL 或快捷按钮 → TaskIntent。

```json
// 请求：NL 模式
{"query": "从珞珈门到樱顶，避开陡坡", "input_method": "nl"}
// 请求：快捷模式
{"start": {"name": "珞珈门"}, "end": {"name": "樱顶"}, "input_method": "shortcut", "mode": "distance_first"}
```

```json
{
  "task_type": "path_planning",
  "start": {"name": "珞珈门", "type": "poi"},
  "end": {"name": "樱顶", "type": "poi"},
  "constraints": {"distance": "medium", "slope": "avoid", "scenery": "normal"},
  "weights": {"distance": 0.2, "slope": 0.6, "scenery": 0.2},
  "mode": "walk",
  "weight_source": "explicit_nl",
  "ambiguity": null
}
```

`weight_source ∈ explicit_nl | shortcut | default`；task_type 另含 poi_query / help / chat / unknown。

### POST /api/route（旧管道·拆分接口）

结构化意图 → 路径。请求体含 start/end/constraints/weights/mode，可附 `coord_start/coord_end`。响应为路径包（字段同 /chat 的 path_planning 形态）+ `explanation`。

### POST /api/candidates

"只有起点 + 想去的类别"场景的候选 POI（路网距离排序，不经过 LLM）。

```json
// 请求
{"start": {"name": "珞珈门"}, "poi_type": "scenery", "keyword": "樱花"}
// 响应 data
{"candidates": [{"name": "樱花大道", "type": "scenery", "distance_m": 320}], "count": 5}
```

## POI

### GET /api/pois

Query 参数：`type`（类型）、`season`（spring/summer/autumn/winter）、`keyword`（模糊）。返回 `{"pois": [...]}`。

### GET /api/pois/&lt;name&gt;

名称/别名模糊查询，返回 `{"poi": {...}}`，未命中 404 `poi_not_found`。

## 天气

### GET /api/weather

```json
{
  "weather": "晴", "temperature": 23.5, "humidity": 61,
  "winddirection": "北", "windpower": "≤3", "reporttime": "...",
  "slippery": false, "hot": false, "low_visibility": false,
  "label": "适宜步行", "advice": "天气晴朗，适合步行赏景"
}
```

上游不可用时 503 `weather_unavailable`（不影响路径规划主流程）。

## 路况事件（5 个端点）

### GET /api/road-conditions

默认只返回**当前生效**事件（普通用户视角）：`{"conditions": [...], "count": n}`。
管理员（session 或 Token）加 `?all=1` 返回含 scheduled/expired 的全部事件并附 `status`。

### POST /api/road-conditions ⚠️ 管理员

事件由**服务端吸附到最近路段**（30m 容差），不接受手填 geometry/半径：

```json
{
  "type": "construction",
  "name": "樱花大道施工",
  "lng": 114.3651,
  "lat": 30.5362,
  "description": "施工期间禁止通行",
  "start_time": "2026-09-10T08:00",
  "end_time": "2026-09-12T18:00"
}
```

- `type`：`closure` 封闭 / `construction` 施工 / `flooding` 积水 / `accident` 事故 / `event` 活动
- 时间均可省略：省略 start=立即生效，省略 end=长期有效；支持 ISO 字符串或时间戳
- 201 返回 `{"condition": {...}, "snap": {...}}`

### GET /api/road-conditions/snap ⚠️ 管理员

`?lng=&lat=` 选点预览：把点击点吸附到最近路段，返回边标识、吸附点（GCJ-02）、道路名、几何，供前端画预览线。

### PATCH /api/road-conditions/&lt;id&gt; ⚠️ 管理员

支持 `{"action": "end"}`（立即结束，保留记录）、改名/描述、调整 start_time/end_time。

### DELETE /api/road-conditions/&lt;id&gt; ⚠️ 管理员

彻底删除事件（前端退出管理模式时同步清除所有本地标记）。

## 管理员鉴权（双通道）

| 通道 | 方式 | 配置 |
|---|---|---|
| 网页 session | `POST /api/admin/login {"password": "..."}` 写 session cookie | 环境变量 `ROAD_CONDITION_ADMIN_PASSWORD`（未配置则登录入口关闭） |
| 系统对接 Token | 请求头 `X-Admin-Token: <token>` 或 `Authorization: Bearer <token>`，hmac 常量时间比较 | 环境变量 `ROAD_CONDITION_ADMIN_TOKEN` |

- `POST /api/admin/login` / `POST /api/admin/logout` / `GET /api/admin/status`（返回 `{is_admin, login_enabled}`）
- 写操作（POST/PATCH/DELETE/snap）任一通道通过即可；普通用户只读且只见到生效中事件。

## 行为埋点

### POST /api/telemetry

前端行为回流，**任何失败都返回 ok，绝不影响主流程**；事件追加写入 `data/telemetry.jsonl`。

```json
{"uid": "whu_uid", "event": "route_accept", "applied_weights": {"distance": 0.2, "slope": 0.6, "scenery": 0.2}}
```

- `route_shown`：路线曝光，仅计 exposure，不学习权重
- `route_accept`：路线采纳，触发 profile.py 的 EMA 画像更新（需带 applied_weights）

## 路网

### POST /api/network/init

触发路网加载（生产启动时已后台预热）。`{"force": true}` 强制重新下载。返回 `{status, nodes, edges, cached}`。

## 出行方式约定

所有规划调用支持 `travel_mode` / `mode = walk | bike | drive`（默认 walk）。服务端最终取值优先级：**NL 显式关键词（骑车/开车/步行，正则后处理纠偏） > body 参数 > 上下文继承 > 默认 walk**。
