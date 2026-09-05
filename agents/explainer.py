import json
import logging
import re
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


# ====== 校园闲聊回复生成 ======

_chat_prompt_cache = None


def _load_chat_prompt() -> str:
    global _chat_prompt_cache
    if _chat_prompt_cache is not None:
        return _chat_prompt_cache
    prompt_path = PROMPTS_DIR / "chat_system.txt"
    with open(prompt_path, "r", encoding="utf-8") as f:
        _chat_prompt_cache = f.read()
    return _chat_prompt_cache


def generate_chat_response(query: str) -> str:
    """生成校园闲聊回复（chat task_type）。

    调用 LLM 以学生向导口吻回答校园问题。
    如果 LLM 不可用，返回模板兜底回复。
    """
    if not DEEPSEEK_API_KEY:
        return "我是珞珈漫步向导，关于校园的问题都可以问我～比如樱花开了没、哪个食堂好吃、图书馆几点开门等等。"

    try:
        from openai import OpenAI
    except ImportError:
        return "我是珞珈漫步向导，你问的关于武大的事情我尽量回答～"

    system_prompt = _load_chat_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query},
    ]

    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=OPENAI_BASE_URL,
        timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0),
    )

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            temperature=0.6,
            max_tokens=200,
        )
        reply = response.choices[0].message.content.strip()
        return reply if reply else "嗯…这个问题我得想想，换个问法试试？"
    except Exception as e:
        logger.warning(f"闲聊回复生成失败: {type(e).__name__}: {e}")
        return "哎呀，网络不太好，稍等一下再问我吧～"


# ====== 跟进建议生成（"可能想问"） ======

_suggest_prompt_cache = None


def _load_suggest_prompt() -> str:
    global _suggest_prompt_cache
    if _suggest_prompt_cache is not None:
        return _suggest_prompt_cache
    prompt_path = PROMPTS_DIR / "suggest_system.txt"
    with open(prompt_path, "r", encoding="utf-8") as f:
        _suggest_prompt_cache = f.read()
    return _suggest_prompt_cache


def _extract_json_array(text: str):
    """从 LLM 输出中提取 JSON 数组，失败返回 None。"""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\[[\s\S]*\]", text)
    if m:
        try:
            data = json.loads(m.group())
            return data if isinstance(data, list) else None
        except json.JSONDecodeError:
            pass
    return None


def generate_suggestions(
    query: str,
    route_data: dict,
    constraints: Optional[dict] = None,
    weights: Optional[dict] = None,
) -> list:
    """根据对话上下文用 LLM 生成 2~3 条跟进建议（"可能想问"）。

    返回 [{label, query}, ...]；LLM 不可用或失败时返回空列表（前端兜底到规则建议）。
    """
    if not DEEPSEEK_API_KEY:
        return []

    try:
        from openai import OpenAI
    except ImportError:
        return []

    system_prompt = _load_suggest_prompt()
    route_summary = _build_route_summary(route_data)

    user_content = (
        f"用户原始需求：{query}\n"
        f"推荐路线：{route_summary}\n"
        f"约束：{json.dumps(constraints or {}, ensure_ascii=False)}\n"
        f"权重：{json.dumps(weights or {}, ensure_ascii=False)}"
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

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            temperature=0.5,
            max_tokens=256,
        )
        raw = response.choices[0].message.content
        data = _extract_json_array(raw)
        if not data:
            return []
        result = []
        for item in data:
            if isinstance(item, dict) and item.get("label") and item.get("query"):
                result.append({"label": str(item["label"]), "query": str(item["query"])})
        return result[:3]
    except Exception as e:
        logger.warning(f"跟进建议生成失败: {type(e).__name__}: {e}")
        return []


# ====== 地点引导生成（POI 找不到时友好引导到已知地点） ======

_poi_guide_prompt_cache = None


def _load_poi_guide_prompt() -> str:
    global _poi_guide_prompt_cache
    if _poi_guide_prompt_cache is not None:
        return _poi_guide_prompt_cache
    prompt_path = PROMPTS_DIR / "poi_guide_system.txt"
    with open(prompt_path, "r", encoding="utf-8") as f:
        _poi_guide_prompt_cache = f.read()
    return _poi_guide_prompt_cache


def _format_ambiguity_message(unknown_name: str, alternatives: list) -> str:
    """同分歧义：直接请用户确认是哪一个（不走 LLM，确定性文案）。"""
    lines = [f"「{unknown_name}」在武大好几个学部都有哦，你指的是哪一个呀？"]
    for i, p in enumerate(alternatives[:5], 1):
        campus = p.get("campus") or "武大"
        lines.append(f"{i}. {p['name']}（{campus}）")
    lines.append("回复序号或完整名称就行～")
    return "\n".join(lines)


def generate_poi_guidance(query: str, unknown_name: str, alternatives: list = None) -> str:
    """当 POI 找不到时，生成友好引导，把用户引到已知地点。

    - alternatives 非空：同分歧义 → 直接给出候选清单请用户确认（不调用 LLM）
    - 校外单位查询（华师/华科等）→ 明确说明服务范围（不调用 LLM）
    - 其余：LLM 基于预筛候选 POI 生成引导；LLM 不可用或失败时返回通用兜底
    """
    if alternatives:
        return _format_ambiguity_message(unknown_name, alternatives)

    # 校外单位：明确边界，避免 LLM 胡乱建议
    try:
        from spatial.poi import _is_external_query
        if unknown_name and _is_external_query(unknown_name):
            return (
                f"「{unknown_name}」不在武汉大学校园内哦～我只熟悉武大文理学部、"
                f"工学部、信息学部三个学部的道路和地点，换个校内目的地试试吧？"
                f"比如「从牌坊到樱花大道」😊"
            )
    except Exception:
        pass

    if not DEEPSEEK_API_KEY:
        return f"「{unknown_name}」我暂时没找到😅 试试换个说法，比如它的正式名称？"

    try:
        from openai import OpenAI
    except ImportError:
        return f"「{unknown_name}」我暂时没找到😅 试试换个说法？"

    # prompt 瘦身：只发与输入最相关的候选 + 少量招牌地标（name+type+短描述），
    # 避免把全量 300 个 POI 的长描述塞进上下文（≈1.2 万 token）。
    relevant = []
    try:
        from spatial.poi import find_poi_candidates, load_pois
        scored = find_poi_candidates(unknown_name or query or "", limit=10, min_score=0.2)
        relevant = [p for p, _ in scored]
        iconic = [
            p for p in load_pois()
            if p.get("scenery_score", 3) >= 5
            and p.get("type") == "scenery"
            and p not in relevant
        ][:6]
        guide_pois = relevant + iconic
    except Exception:
        from config import WHU_POIS
        guide_pois = [
            {"name": k, "type": v.get("type", ""), "description": v.get("desc", "")}
            for k, v in list(WHU_POIS.items())[:16]
        ]

    poi_list = json.dumps(
        [
            {
                "name": p.get("name", ""),
                "type": p.get("type", ""),
                "desc": (p.get("description") or "")[:40],
            }
            for p in guide_pois[:16]
        ],
        ensure_ascii=False,
    )

    system_prompt = _load_poi_guide_prompt()
    user_content = (
        f"用户提到：{unknown_name}\n"
        f"用户原话：{query}\n\n"
        f"已知地点列表：{poi_list}"
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

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            temperature=0.5,
            max_tokens=160,
        )
        reply = response.choices[0].message.content.strip()
        return reply if reply else f"「{unknown_name}」我暂时没找到，换个说法试试？"
    except Exception as e:
        logger.warning(f"地点引导生成失败: {type(e).__name__}: {e}")
        return f"「{unknown_name}」我暂时没找到😅 试试换个说法，比如它的正式名称？"