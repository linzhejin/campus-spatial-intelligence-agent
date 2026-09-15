"""珞珈智行 — Agent 规划器（Plan-Act-Observe 循环）。

v2 全 Agent 架构的唯一入口：所有用户输入（含明确 A→B、闲聊、天气、路况）
都进入本循环，由 LLM 决策调用工具或直接回答。

护栏：
- 轮次上限 MAX_TURNS、总时长 TIME_BUDGET_S（gunicorn timeout 60s 内留余量）
- 工具参数/执行失败 → 错误信息回注，LLM 自我修正
- LLM API 故障 → 抛 PlannerError，由 routes.py 落回旧管道（停电保险）
"""

import json
import logging
import re
import time
from pathlib import Path

import httpx

import config
from agents import tools as agent_tools
from agents import knowledge, profile

logger = logging.getLogger(__name__)

MAX_TURNS = 6
TIME_BUDGET_S = 40.0
LLM_MAX_TOKENS = 800

_PROMPT_PATH = Path(__file__).parent / "prompts" / "agent_system.txt"
_system_prompt_cache = None  # NOTE: 修改 agent_system.txt 后需清此缓存或重启进程


class PlannerError(Exception):
    """LLM 层面的故障（网络/鉴权/限流），触发路由层兜底。"""


def _load_system_prompt() -> str:
    global _system_prompt_cache
    if _system_prompt_cache is None:
        _system_prompt_cache = _PROMPT_PATH.read_text(encoding="utf-8")
    return _system_prompt_cache


def _make_client():
    from openai import OpenAI
    return OpenAI(
        api_key=config.DEEPSEEK_API_KEY,
        base_url=config.OPENAI_BASE_URL,
        # 循环内每次调用给 25s read；总时长由 TIME_BUDGET_S 兜底
        timeout=httpx.Timeout(connect=5.0, read=25.0, write=10.0, pool=5.0),
    )


# DeepSeek 偶发把内部 DSML 工具调用标记当纯文本吐出，必须剥掉，绝不能进前端
_DSML_RE = re.compile(r"<\s*[｜|]{2,}\s*DSML[\s\S]*", re.IGNORECASE)


def _sanitize_message(text: str) -> str:
    if not text:
        return ""
    return _DSML_RE.split(text)[0].strip()


_MODE_VERB = {"walk": "步行", "bike": "骑行", "drive": "开车"}


def _fmt_dist(m):
    if m is None:
        return None
    return f"{m / 1000:.1f} 公里" if m >= 1000 else f"{round(m)} 米"


def _build_route_message(route: dict) -> str:
    """路线工具成功后直接用结构化数据拼一句话，不再调 LLM（快且不会泄漏 DSML）。"""
    mode = route.get("mode", "walk")
    verb = _MODE_VERB.get(mode, "步行")
    s_name = route.get("start_name") or "起点"
    e_name = route.get("end_name") or "终点"
    length = route.get("recommended_length_m") or route.get("distance_m")
    dur = route.get("duration_min")
    shortest = route.get("shortest_length_m")
    shortest_dur = route.get("shortest_duration_min")

    head = f"已为你规划好从{s_name}到{e_name}的{verb}路线"
    ld = _fmt_dist(length)
    if ld:
        head += f"，约 {ld}"
        if dur is not None:
            head += f"、{dur:g} 分钟"
    # 最短路线明显更短（>7%）才提示，地图上灰虚线可直接对比
    tail = ""
    if shortest and length and shortest < length * 0.93:
        sd = _fmt_dist(shortest)
        if sd:
            tail = f"最短路线约 {sd}"
            if shortest_dur is not None:
                tail += f"、{shortest_dur:g} 分钟"
            tail += "，地图灰虚线可对比"
    return head + "。" + (tail + "。" if tail else "")



def _build_messages(query: str, context: dict = None, history: list = None,
                    coord_start: dict = None, coord_end: dict = None,
                    uid: str = None, travel_mode: str = None,
                    coord_waypoints: list = None) -> list:
    messages = [{"role": "system", "content": _load_system_prompt()}]

    # 任务卡知识注入（静态校园经验）
    knowledge_msg = knowledge.build_knowledge_message(query)
    if knowledge_msg:
        messages.append({"role": "system", "content": knowledge_msg})

    # 用户画像注入（动态个体偏好）
    profile_msg = profile.build_profile_message(uid)
    if profile_msg:
        messages.append({"role": "system", "content": profile_msg})

    # 出行方式注入：前端切换了步行/骑行/驾车，LLM 应在 plan_route 等工具调用中设 mode
    if travel_mode and travel_mode in ("walk", "bike", "drive"):
        messages.append({
            "role": "system",
            "content": f"用户当前选择的出行方式为 {travel_mode}（步行/骑行/驾车）。"
                        f"调用 plan_route / plan_via_route 时 mode 参数请设为 '{travel_mode}'。"
                        f"除非用户在消息中明确说「骑车/开车/步行」覆盖，否则用此值。",
        })

    # GPS 定位注入：让 LLM 能把"我这/这里"解析为 coord 类型端点
    gps_lines = []
    for label, coord in (("起点（用户当前位置）", coord_start), ("终点", coord_end)):
        if isinstance(coord, dict) and coord.get("lng") is not None and coord.get("lat") is not None:
            gps_lines.append(
                f"- {label}: WGS-84 坐标 lng={coord['lng']}, lat={coord['lat']}"
                f"（用户说「我这/这里/我的位置」时用 type=coord 传入）"
            )
    if gps_lines:
        messages.append({"role": "system", "content": "用户本次请求携带了 GPS 定位：\n" + "\n".join(gps_lines)})

    # 地图途经点注入：用户在地图上选的途经点坐标
    if isinstance(coord_waypoints, list) and coord_waypoints:
        wp_lines = []
        for i, wp in enumerate(coord_waypoints):
            if isinstance(wp, dict) and wp.get("lng") is not None and wp.get("lat") is not None:
                wp_lines.append(
                    f"- 途经点{i+1}: WGS-84 坐标 lng={wp['lng']}, lat={wp['lat']}"
                    f"（调用 plan_via_route 时用 via_coord={{lng:{wp['lng']}, lat:{wp['lat']}}} 传入）"
                )
        if wp_lines:
            messages.append({
                "role": "system",
                "content": "用户在地图上标记了途经点，规划时必须经过这些点：\n" + "\n".join(wp_lines),
            })

    # 多轮历史（前端在 P4 接入；兼容旧 context.history 形态）
    history = history or (context or {}).get("history") or []
    for turn in history[-8:]:
        if not isinstance(turn, dict):
            continue
        role, content = turn.get("role"), turn.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": str(content)[:500]})
        elif turn.get("query"):  # 旧形态兼容
            messages.append({"role": "user", "content": str(turn["query"])})

    # 旧 context 的规划槽位（上一轮路线状态），供承接"换一条/从那里出发"
    if context:
        slot = {k: context[k] for k in ("start", "end") if context.get(k)}
        if slot:
            messages.append({
                "role": "system",
                "content": "上一轮规划状态（供承接，不要复述）: " + json.dumps(slot, ensure_ascii=False),
            })

    messages.append({"role": "user", "content": query})
    return messages


def run_agent(query: str, context: dict = None, history: list = None,
              coord_start: dict = None, coord_end: dict = None,
              uid: str = None, travel_mode: str = None,
              coord_waypoints: list = None) -> dict:
    """Agent 主循环。

    Returns:
        {
            "response_kind": "route" | "candidates" | "clarify" | "chat",
            "message": str,            # 给用户的最终自然语言答复
            "route": dict | None,      # plan_route/plan_via_route/plan_tour 的完整路径包
            "route_kind": "direct" | "via" | "tour" | None,
            "candidates": list | None, # search_poi_candidates 结果
            "clarify": {"question", "options"} | None,
            "turns": int,              # 实际 LLM 轮次（观测用）
        }

    Raises:
        PlannerError: LLM API 不可用（调用方落回旧管道）
    """
    if not config.DEEPSEEK_API_KEY:
        raise PlannerError("DEEPSEEK_API_KEY 未配置")

    started = time.monotonic()
    client = _make_client()
    messages = _build_messages(query, context, history, coord_start, coord_end,
                               uid=uid, travel_mode=travel_mode,
                               coord_waypoints=coord_waypoints)

    artifact_route, route_kind = None, None
    artifact_candidates, artifact_clarify = None, None
    artifact_suggestions = None
    final_message = ""
    turns = 0

    for _ in range(MAX_TURNS):
        if time.monotonic() - started > TIME_BUDGET_S:
            logger.warning("Agent 循环超时（%.1fs），基于已有结果收尾", time.monotonic() - started)
            break
        turns += 1

        try:
            response = client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=messages,
                tools=agent_tools.TOOL_SCHEMAS,
                temperature=0.0,
                max_tokens=LLM_MAX_TOKENS,
            )
        except Exception as e:
            raise PlannerError(f"LLM 调用失败: {type(e).__name__}: {e}") from e

        msg = response.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)

        # 无工具调用：LLM 直接给出最终答复，循环结束
        if not tool_calls:
            final_message = (msg.content or "").strip()
            break

        messages.append(msg.model_dump(exclude_none=True))

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                result = {"error": "invalid_args", "message": "参数不是合法 JSON，请修正后重试"}
                artifact = None
            else:
                result, artifact = agent_tools.execute_tool(name, args)

            if artifact:
                if "route" in artifact:
                    artifact_route = artifact["route"]
                    route_kind = artifact.get("route_kind", "direct")
                if "candidates" in artifact:
                    artifact_candidates = artifact["candidates"]
                if "clarify" in artifact:
                    artifact_clarify = artifact["clarify"]
                if "suggestions" in artifact:
                    artifact_suggestions = artifact["suggestions"]

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": agent_tools.result_to_json(result),
            })

        # 终止型工具：澄清提问立即结束
        if artifact_clarify:
            break

        # 路线已产出 → 立即收尾，不再让 LLM 多跑一轮
        # （DeepSeek 在历史含 tool_call 时会把内部 DSML 工具调用格式当纯文本吐出）
        if artifact_route:
            break

    # ---- 收尾：组装响应 ----
    if artifact_clarify:
        return {
            "response_kind": "clarify",
            "message": _sanitize_message(final_message) or artifact_clarify["question"],
            "route": artifact_route, "route_kind": route_kind,
            "candidates": artifact_candidates,
            "clarify": artifact_clarify,
            "suggestions": artifact_suggestions,
            "turns": turns,
        }

    # 防御：LLM 直出文本里若混入 DSML 标记，剥掉
    final_message = _sanitize_message(final_message)

    if not final_message:
        # 循环耗尽/超时但 LLM 没给结论：基于已有 artifact 兜底生成
        if artifact_route:
            final_message = _build_route_message(artifact_route)
        elif artifact_candidates:
            final_message = "帮你找到这些候选地点，选一个我帮你规划路线～"
        else:
            raise PlannerError("Agent 循环结束但没有产出任何结果")

    response_kind = "chat"
    if artifact_route:
        response_kind = "route"
    elif artifact_candidates:
        response_kind = "candidates"

    return {
        "response_kind": response_kind,
        "message": final_message,
        "route": artifact_route,
        "route_kind": route_kind,
        "candidates": artifact_candidates,
        "clarify": None,
        "suggestions": artifact_suggestions,
        "turns": turns,
    }
