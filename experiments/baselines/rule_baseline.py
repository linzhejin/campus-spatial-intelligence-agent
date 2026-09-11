"""
B1 规则兜底解析基线 (Rule-Based Baseline)

不调用 LLM，纯规则解析自然语言 → TaskIntent：
  - task_type / 起终点：复用 agents.parser._rule_based_classify + _fuzzy_match_poi_name
  - 约束检测：关键词匹配 distance/slope/scenery
  - 权重映射：离散映射（单维度 0.8/0.1/0.1，多维度均分），无连续插值
  - 出行方式：复用 detect_travel_mode

用于评估"无 LLM、纯规则"的意图解析质量，作为 E1 的下界基线。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# 注入项目根目录，使 from spatial/agents 可直接导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.parser import (
    _rule_based_classify,
    _fuzzy_match_poi_name,
    TaskIntent,
    PoiRef,
    Constraints,
    _annotate_weight_source,
    detect_travel_mode,
    DEFAULT_CONSTRAINTS_DICT,
)

# 约束关键词模式（与 parser.py 中 LLM prompt 的语义对齐）
_SLOPE_AVOID_PATTERNS = [
    r"避开.*(?:陡坡|爬坡|坡)", r"不走.*坡", r"平坦", r"避坡",
    r"(?:陡坡|爬坡).{0,4}避开", r"膝盖不好", r"少爬坡",
]
_SCENERY_HIGH_PATTERNS = [
    r"风景.{0,2}(?:好|美|优|漂亮)", r"走风景", r"赏樱", r"赏花",
    r"最美的.*(?:校园|路线|景)", r"看.*樱花", r"景色好",
]
_DISTANCE_SHORT_PATTERNS = [
    r"最近", r"最短", r"短路", r"距离短",
]


def _detect_constraints(query: str) -> dict:
    """关键词检测约束，返回 {distance, slope, scenery}（默认 medium/normal/normal）。"""
    cons = dict(DEFAULT_CONSTRAINTS_DICT)  # medium/normal/normal
    if any(re.search(p, query) for p in _SLOPE_AVOID_PATTERNS):
        cons["slope"] = "avoid"
    if any(re.search(p, query) for p in _SCENERY_HIGH_PATTERNS):
        cons["scenery"] = "high"
    if any(re.search(p, query) for p in _DISTANCE_SHORT_PATTERNS):
        cons["distance"] = "short"
    return cons


def _map_constraints_to_weights(constraints: dict):
    """把约束等级离散映射为权重向量。
    单维度：该维 0.8，其余各 0.1；
    多维度：活跃维度均分（2 活跃→各 0.45，其余 0.1；3 活跃→各 1/3）；
    无约束：返回 None（走 compute_route 默认权重）。
    """
    active = []
    if constraints.get("distance") == "short":
        active.append("distance")
    if constraints.get("slope") == "avoid":
        active.append("slope")
    if constraints.get("scenery") == "high":
        active.append("scenery")

    if not active:
        return None

    weights = {"distance": 0.1, "slope": 0.1, "scenery": 0.1}
    if len(active) == 1:
        weights[active[0]] = 0.8
    elif len(active) == 2:
        for d in active:
            weights[d] = 0.45
    else:  # 3 个全部活跃
        for d in active:
            weights[d] = round(1.0 / 3.0, 3)
    return weights


def rule_parse(query: str) -> TaskIntent:
    """B1：规则兜底解析 query → TaskIntent（不调用 LLM）。

    流程：
      1. _rule_based_classify 取 task_type + 起终点地名
      2. _fuzzy_match_poi_name 把地名归一化为 POI 规范名
      3. _detect_constraints 关键词检测约束
      4. _map_constraints_to_weights 离散映射权重
      5. detect_travel_mode 检测出行方式
      6. 组装 TaskIntent 并 _annotate_weight_source 打标
    """
    classified = _rule_based_classify(query)
    constraints = _detect_constraints(query)
    weights = _map_constraints_to_weights(constraints)

    # 起终点：规则分类已做模糊匹配，直接取其规范名；为空则置 None
    start_name = classified.get("start_name")
    end_name = classified.get("end_name")
    start = PoiRef(name=start_name, type="poi") if start_name else None
    end = PoiRef(name=end_name, type="poi") if end_name else None

    # task_type：_rule_based_classify 返回 None 时回退 path_planning
    task_type = classified.get("task_type") or "path_planning"

    # 出行方式：detect_travel_mode 显式关键词命中则采纳，否则默认 walk
    mode, _explicit = detect_travel_mode(query)

    intent = TaskIntent(
        task_type=task_type,
        start=start,
        end=end,
        constraints=Constraints(**constraints),
        mode=mode,
        weights=weights,
        input_method="nl",
        ambiguity=None,
    )
    # 打标 weight_source（nl + weights!=null → explicit_nl；否则 default）
    intent = _annotate_weight_source(intent)

    # path_planning 缺端点时补 ambiguity 引导（与 parser.py 行为一致）
    if intent.task_type == "path_planning":
        if intent.start is None and intent.end is None:
            intent.ambiguity = "解析失败，请尝试更明确的描述"
        elif intent.start is None:
            intent.ambiguity = "请指定起点"
        elif intent.end is None:
            intent.ambiguity = "请指定终点"

    return intent


if __name__ == "__main__":
    # CLI 自检：对几条标准 query 跑规则解析
    import json

    samples = [
        "从珞珈门到樱顶",
        "我想避开陡坡，从行政楼到枫园",
        "从总图书馆到凌波门，走风景好的路线",
        "哪条路去樱顶最近",
        "从老斋舍到宋卿体育馆，避开爬坡，走风景好的路",
        "从牌坊骑车到樱顶",
        "樱顶在哪里",
        "你能做什么",
        "今天天气怎么样",
    ]
    for q in samples:
        it = rule_parse(q)
        d = it.model_dump() if hasattr(it, "model_dump") else it.dict()
        print(f"{q}\n  -> {json.dumps(d, ensure_ascii=False)}")
