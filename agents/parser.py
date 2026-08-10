import json
import logging
import re
from pathlib import Path
from typing import Literal, Optional

import httpx
from pydantic import BaseModel, ValidationError

from config import DEEPSEEK_API_KEY, OPENAI_BASE_URL, LLM_MODEL, WHU_POIS

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

FEW_SHOT_EXAMPLES = [
    (
        "从牌坊到樱顶",
        '{"task_type":"path_planning","start":{"name":"牌坊","type":"poi"},"end":{"name":"樱顶","type":"poi"},"constraints":{"distance":"medium","slope":"normal","scenery":"normal"},"weights":null,"input_method":"nl","ambiguity":null}',
    ),
    (
        "从牌坊到樱顶，避开陡坡",
        '{"task_type":"path_planning","start":{"name":"牌坊","type":"poi"},"end":{"name":"樱顶","type":"poi"},"constraints":{"distance":"medium","slope":"avoid","scenery":"normal"},"weights":{"distance":0.2,"slope":0.6,"scenery":0.2},"input_method":"nl","ambiguity":null}',
    ),
    (
        "我第一次来武大，想看樱花和老图书馆，但膝盖不好",
        '{"task_type":"path_planning","start":null,"end":null,"constraints":{"distance":"medium","slope":"avoid","scenery":"high"},"weights":{"distance":0.1,"slope":0.5,"scenery":0.4},"input_method":"nl","ambiguity":"请指定起点"}',
    ),
    (
        "从梅园到桂园，最短路径",
        '{"task_type":"path_planning","start":{"name":"梅园","type":"poi"},"end":{"name":"桂园","type":"poi"},"constraints":{"distance":"short","slope":"normal","scenery":"normal"},"weights":{"distance":0.8,"slope":0.1,"scenery":0.1},"input_method":"nl","ambiguity":null}',
    ),
    (
        "带朋友逛，从教五去图书馆，走风景好的路",
        '{"task_type":"path_planning","start":{"name":"教五","type":"poi"},"end":{"name":"图书馆","type":"poi"},"constraints":{"distance":"medium","slope":"normal","scenery":"high"},"weights":{"distance":0.2,"slope":0.1,"scenery":0.7},"input_method":"nl","ambiguity":null}',
    ),
]


class PoiRef(BaseModel):
    name: str
    type: Literal["poi", "coord"]
    coordinates: Optional[dict] = None


class Constraints(BaseModel):
    distance: Literal["short", "medium", "relaxed"]
    slope: Literal["avoid", "normal", "any"]
    scenery: Literal["high", "normal", "any"]


class TaskIntent(BaseModel):
    task_type: Literal["path_planning", "poi_query"]
    start: Optional[PoiRef] = None
    end: Optional[PoiRef] = None
    constraints: Constraints
    weights: Optional[dict] = None
    input_method: Literal["nl", "shortcut", "map_click"] = "nl"
    ambiguity: Optional[str] = None


def _load_system_prompt() -> str:
    prompt_path = PROMPTS_DIR / "parse_system.txt"
    with open(prompt_path, "r", encoding="utf-8") as f:
        template = f.read()
    poi_list = json.dumps(
        [{"name": k, "type": v["type"], "desc": v.get("desc", "")} for k, v in WHU_POIS.items()],
        ensure_ascii=False,
    )
    return template.replace("{poi_list_json}", poi_list)


def _build_messages(query: str, context: Optional[dict] = None) -> list[dict]:
    system_prompt = _load_system_prompt()
    messages = [{"role": "system", "content": system_prompt}]

    for shot_query, shot_output in FEW_SHOT_EXAMPLES:
        messages.append({"role": "user", "content": shot_query})
        messages.append({"role": "assistant", "content": shot_output})

    if context:
        messages.append({"role": "user", "content": f"上下文: {json.dumps(context, ensure_ascii=False)}\n当前查询: {query}"})
    else:
        messages.append({"role": "user", "content": query})

    return messages


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("无法从响应中提取 JSON", text, 0)


def _fallback_task_intent(query: str) -> TaskIntent:
    return TaskIntent(
        task_type="path_planning",
        start=None,
        end=None,
        constraints=Constraints(distance="medium", slope="normal", scenery="normal"),
        weights=None,
        input_method="nl",
        ambiguity="解析失败，请尝试更明确的描述",
    )


def parse_query(query: str, context: Optional[dict] = None) -> TaskIntent:
    if not DEEPSEEK_API_KEY:
        logger.error("DEEPSEEK_API_KEY 未配置，使用兜底解析")
        return _fallback_task_intent(query)

    try:
        from openai import OpenAI
    except ImportError:
        logger.error("openai SDK 未安装")
        return _fallback_task_intent(query)

    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=OPENAI_BASE_URL,
        timeout=httpx.Timeout(connect=5.0, read=10.0),
    )

    messages = _build_messages(query, context)

    last_error = None
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                temperature=0.1,
                max_tokens=512,
            )
            raw_text = response.choices[0].message.content
            data = _extract_json(raw_text)
            return TaskIntent(**data)
        except ValidationError as e:
            last_error = e
            logger.warning(f"Pydantic 校验失败 (attempt {attempt + 1}): {e}")
        except json.JSONDecodeError as e:
            last_error = e
            logger.warning(f"JSON 解析失败 (attempt {attempt + 1}): {e}")
        except Exception as e:
            last_error = e
            logger.warning(f"LLM 调用失败 (attempt {attempt + 1}): {type(e).__name__}: {e}")

        if attempt < 2:
            messages.append({
                "role": "user",
                "content": "上一次输出格式有误，请重新输出正确的 JSON 格式。",
            })

    logger.error(f"解析全部失败，使用兜底方案。最后错误: {last_error}")
    return _fallback_task_intent(query)