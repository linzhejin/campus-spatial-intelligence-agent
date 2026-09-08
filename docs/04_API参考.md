# 04 · API 参考

所有端点前缀 `/api`。响应统一格式 `{"data": ...}` 或 `{"error": "code", "message": "..."}`。

## 健康检查

### GET /health · GET /api/health
返回服务状态。

```json
{"status":"ok","project":"珞珈智行","timestamp":1725789600}
```

## 天气

### GET /api/weather
获取武汉天气（含气温、天气、出行建议）。

```json
{
  "data": {
    "weather": "晴",
    "temperature": 23.5,
    "advice": "天气晴朗，适合步行赏景"
  }
}
```

## NL 解析与路径规划

### POST /api/parse
自然语言 → 结构化任务意图。

**请求**
```json
{"query": "从珞珈门到樱顶，避开陡坡"}
```

**响应**
```json
{
  "data": {
    "task_type": "path_planning",
    "start": {"name": "珞珈门", "type": "poi"},
    "end": {"name": "樱顶", "type": "poi"},
    "constraints": {"distance": "medium", "slope": "avoid", "scenery": "normal"},
    "weights": {"distance": 0.2, "slope": 0.6, "scenery": 0.2},
    "mode": "walk"
  }
}
```

### POST /api/route
任务意图 → 路径规划结果。

**请求**
```json
{
  "start": {"name": "珞珈门"},
  "end": {"name": "樱顶"},
  "constraints": {"distance": "medium", "slope": "avoid", "scenery": "normal"},
  "weights": {"distance": 0.2, "slope": 0.6, "scenery": 0.2},
  "mode": "walk"
}
```

**响应**
```json
{
  "data": {
    "route": [{"lng": 114.358, "lat": 30.533}, ...],
    "distance": 1234.5,
    "pois": [{"name": "万林博物馆", "lng": ..., "lat": ...}, ...],
    "costs": {"distance": 0.8, "slope": 0.3, "scenery": 0.4},
    "explanation": "从珞珈门出发，沿主干道..."
  }
}
```

### POST /api/chat ⭐
一站式：NL → 解析 + 路径规划 + 解释生成。

**请求**
```json
{"query": "从珞珈门到樱顶", "travel_mode": "walk", "context": {...}}
```

**响应**：合并 parse + route 的完整结果，含 `explanation` 字段。

## POI 查询

### GET /api/pois
POI 列表，支持 `type` / `season` 筛选。

```
GET /api/pois?type=scenery&season=spring
```

### GET /api/pois/<name>
单 POI 查询。模糊匹配，多个候选返回 ambiguity。

```
GET /api/pois/七舍
```

### POST /api/candidates
场景 3：按类型查找起点附近的候选 POI。

```json
{"start": {"name": "珞珈门"}, "poi_type": "scenery", "keyword": "樱花"}
```

## 路网

### POST /api/network/init
触发路网加载（生产模式已自动预热）。

## 路况事件

### GET /api/road-conditions
获取所有路况事件列表。

### POST /api/road-conditions ⚠️ 管理员
新增路况事件。需先 POST /api/admin/login 登录。

```json
{
  "type": "construction",
  "geometry": "LINESTRING (114.36 30.53, ...)",
  "description": "樱花大道部分施工"
}
```

type 取值：`closure`（封闭）/ `construction`（施工）/ `accident`（事故）。

### DELETE /api/road-conditions/<id> ⚠️ 管理员
删除路况事件。

## 管理员

### POST /api/admin/login
```json
{"password": "你的管理员密码"}
```
成功后写入 session `is_admin=true`。

### POST /api/admin/logout
退出管理员登录。

### GET /api/admin/status
查询当前是否管理员登录态。

## 出行方式参数

所有规划类端点（route / chat）支持 `travel_mode` 或在 query 中显式关键词：
- `walk` 步行（默认）
- `bike` 骑行（"骑车/单车/骑行"）
- `drive` 驾车（"开车/驾车/自驾"）

优先级：自然语言关键词 > body 参数 > intent.mode > 默认 walk。
