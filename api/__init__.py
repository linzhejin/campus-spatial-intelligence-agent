"""
api — RESTful API 路由层

提供 5 个端点:
  POST /api/parse   — 自然语言 → 任务意图
  POST /api/route   — 任务意图 → 路径规划
  POST /api/chat    — 一站式 NL → 完整流程
  GET  /api/pois   — POI 列表
  GET  /api/pois/<name> — 单 POI 查询
"""