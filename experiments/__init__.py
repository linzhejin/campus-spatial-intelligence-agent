"""实验评估框架 (E1 意图解析 + E2 路径质量)

子模块：
  - datasets/      种子查询与 gold 标注
  - baselines/     基线方法 (R1 最短路径 / R2 固定权重 / B1 规则兜底)
  - evaluator.py   E1 指标计算器
  - runner.py      批量评估 CLI
  - results/       实验输出 (CSV / 日志)
"""
