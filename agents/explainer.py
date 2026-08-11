import json
import logging
from pathlib import Path
from typing import Optional

import httpx

from config import DEEPSEEK_API_KEY, OPENAI_BASE_URL, LLM_MODEL

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

MAX_EXPLANATION_LENGTH = 150


_system_prompt_cache = None


def _load_system_prompt() -> str:
    global _system_prompt_cache
    if _system_prompt_cache is not None:
        return _system_prompt_cache
    prompt_path = PROMPTS_DIR / "explain_system.txt"
    with open(prompt_path, "r", encoding="utf-8") as f:
        _system_prompt_cache = f.read()
    return _system_prompt_cache


def _build_route_summary(route_data: dict) -> str:
    distance = route_data.get("distance_m", route_data.get("distance", "未知"))
    pois = route_data.get("pois", [])
    filter_status = route_data.get("filter_status", "no_filter")

    parts = [f"距离 {distance} 米"]
    if pois:
        poi_names = [p["name"] if isinstance(p, dict) else str(p) for p in pois]
        parts.append(f"途经 {', '.join(poi_names)}")
    if filter_status == "filtered":
        removed = route_data.get("filter_info", {}).get("removed_edges", "未知")
        parts.append(f"过滤了 {removed} 段不可通行路段")
    elif filter_status == "degraded_slope":
        parts.append("部分陡坡路段放宽限制")

    return "，".join(parts)


def _build_shortest_summary(route_data: dict) -> str:
    shortest_distance = route_data.get("shortest_distance_m", route_data.get("shortest_distance", "未知"))
    return f"距离 {shortest_distance} 米"


def _build_costs_summary(route_data: dict) -> str:
    costs = route_data.get("costs", {})
    if not costs:
        return "无"
    parts = []
    for k, v in costs.items():
        if isinstance(v, float):
            parts.append(f"{k}: {v:.3f}")
        else:
            parts.append(f"{k}: {v}")
    return ", ".join(parts)


def _build_weight_source_prefix(weight_source: Optional[str]) -> str:
    """根据 weight_source 返回解释开头的偏好来源标注（T-024 §2）。"""
    if weight_source == "explicit_nl":
        return "已按您的空间偏好推荐。"
    elif weight_source == "shortcut":
        return "已按快捷按钮预设偏好推荐。"
    elif weight_source == "default":
        return "已按默认路线推荐。"
    return ""


def _build_template_explanation(
    route_data: dict,
    user_constraints: Optional[dict],
    user_weights: Optional[dict],
    weight_source: Optional[str] = None,
) -> str:
    distance = route_data.get("distance_m", route_data.get("distance", 0))
    shortest_distance = route_data.get("shortest_distance_m", route_data.get("shortest_distance", 0))
    pois = route_data.get("pois", [])
    filter_status = route_data.get("filter_status", "no_filter")

    segments = []

    prefix = _build_weight_source_prefix(weight_source)
    if prefix:
        segments.append(prefix.rstrip("。"))

    if user_constraints:
        slope = user_constraints.get("slope", "normal")
        scenery = user_constraints.get("scenery", "normal")
        if slope == "avoid":
            if filter_status == "filtered":
                segments.append("已避开陡坡路段")
            elif filter_status == "degraded_slope":
                segments.append("部分陡坡路段已放宽限制")
        if scenery == "high":
            segments.append("优先选择了景观较好的路线")

    if pois:
        poi_names = [p["name"] if isinstance(p, dict) else str(p) for p in pois[:3]]
        segments.append(f"途经 {', '.join(poi_names)}")

    if distance and shortest_distance:
        diff = distance - shortest_distance
        if diff > 0:
            segments.append(f"比最短路径多 {diff} 米")
        elif diff < 0:
            segments.append(f"比最短路径少 {-diff} 米")
        else:
            segments.append("与最短路径一致")

    if not segments:
        return "已为您规划好路线，祝您游览愉快。"

    explanation = "，".join(segments) + "。"
    return explanation[:MAX_EXPLANATION_LENGTH]


def generate_explanation(
    route_data: dict,
    user_constraints: Optional[dict] = None,
    user_weights: Optional[dict] = None,
    weight_source: Optional[str] = None,
) -> str:
    if not DEEPSEEK_API_KEY:
        logger.error("DEEPSEEK_API_KEY 未配置，使用模板解释")
        return _build_template_explanation(route_data, user_constraints, user_weights, weight_source)

    try:
        from openai import OpenAI
    except ImportError:
        logger.error("openai SDK 未安装，使用模板解释")
        return _build_template_explanation(route_data, user_constraints, user_weights, weight_source)

    system_prompt = _load_system_prompt()

    route_summary = _build_route_summary(route_data)
    shortest_summary = _build_shortest_summary(route_data)
    costs_summary = _build_costs_summary(route_data)
    filter_status = route_data.get("filter_status", "no_filter")
    weight_source_label = _build_weight_source_prefix(weight_source).rstrip("。") or "无来源标注"

    user_content = (
        f"偏好来源：{weight_source_label}\n"
        f"约束：{json.dumps(user_constraints or {}, ensure_ascii=False)}\n"
        f"权重：{json.dumps(user_weights or {}, ensure_ascii=False)}\n"
        f"推荐路线：{route_summary}\n"
        f"最短路径：{shortest_summary}\n"
        f"分项成本：{costs_summary}\n"
        f"过滤状态：{filter_status}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=OPENAI_BASE_URL,
        timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0),
    )

    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                temperature=0.3,
                max_tokens=256,
            )
            explanation = response.choices[0].message.content.strip()
            if len(explanation) > MAX_EXPLANATION_LENGTH:
                explanation = explanation[:MAX_EXPLANATION_LENGTH - 1] + "…"
            return explanation
        except Exception as e:
            logger.warning(f"解释生成失败 (attempt {attempt + 1}): {type(e).__name__}: {e}")
            if attempt < 2:
                messages.append({
                    "role": "user",
                    "content": "请重新生成解释，注意控制在 150 字以内，并在开头说明偏好来源。",
                })

    logger.error("解释生成全部失败，使用模板兜底")
    return _build_template_explanation(route_data, user_constraints, user_weights, weight_source)