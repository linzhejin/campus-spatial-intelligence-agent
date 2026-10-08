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
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import httpx

import config
from agents import tools as agent_tools
from agents import knowledge, profile
from agents.preferences import route_preference_requested
from agents.timings import normalize_timings

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


_NAMED_VIA_RE = re.compile(
    r"^(?:请|带我|帮我)?\s*(?:从(?P<start>.+?))?"
    r"(?:途经|经过|经由|经)(?P<via>.+?)(?:去|到)"
    r"(?P<end>[^，,。]+)"
)


def _named_via_request(text: str) -> dict | None:
    """Recognize an explicit, single named stop before the destination."""
    match = _NAMED_VIA_RE.match((text or "").strip())
    if not match:
        return None
    via = match.group("via").strip()
    end = re.split(r"\s*(?:不走|避开|顺便|同时|查天气)", match.group("end"), maxsplit=1)[0].strip()
    if not via or not end or re.search(r"、|和|及|再经|途经|经过", via):
        return None
    return {"start": (match.group("start") or "").strip(), "via": via, "end": end}


def _explicit_start_name(query: str) -> str | None:
    """Return a clearly named origin so a map-selected start does not override it."""
    text = (query or "").strip()
    field = re.search(r"(?:起点|出发地)(?:设为|是|为|在|[:：])\s*([^，,。；;]+)", text)
    if field:
        name = field.group(1).strip()
        return name or None

    # Common conversational form: “我在教五这边，帮我导航到图书馆”.
    located = re.search(r"(?:^|[，,。；;\s])我(?:现在)?在\s*([^，,。；;]{1,40})", text)
    if located:
        name = re.split(r"(?:出发|前往|去|到|帮我|请你|规划|导航|带我)",
                        located.group(1), maxsplit=1)[0].strip()
        name = re.sub(r"(?:这边|附近|旁边|周围|一带)$", "", name).strip()
        if name and name not in {"我这", "我这里", "我这边", "这里", "当前位置", "当前"}:
            return name

    from_match = re.search(
        r"(?:从|由)\s*([^，,。；;]{1,30}?)\s*(?:出发|到|去|前往)", text
    )
    if from_match:
        name = from_match.group(1).strip()
        if name and name not in {"我这", "我这里", "我的位置", "当前位置", "地图起点"}:
            return name
        if name == "地图起点":
            return name
        return None

    direct_match = re.match(r"^([^，,。；;]{2,20}?)\s*(?:到|至|去|前往)", text)
    if not direct_match:
        return None
    name = direct_match.group(1).strip()
    # Bare imperative/request lead-ins are not place names. Without this guard,
    # phrases such as “带我去最近的食堂” look like an origin named “带我”.
    if re.match(
        r"^(?:我想|想要|我要|我希望|帮我|帮忙|请帮我|请你|请问|请|麻烦|带我|带我们|带您|送我|给我|推荐我|现在)",
        name,
    ):
        return None
    return name or None


def _manual_map_start_ref(coord_start: dict) -> dict | None:
    if not isinstance(coord_start, dict) or coord_start.get("name") != "地图起点":
        return None
    try:
        lng, lat = float(coord_start.get("lng")), float(coord_start.get("lat"))
    except (TypeError, ValueError):
        return None
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        return None
    return {"name": "地图起点", "type": "coord", "lng": lng, "lat": lat}


def _asks_for_origin(question: str) -> bool:
    """Identify a clarification that asks only for a route origin."""
    text = (question or "").strip()
    if not text or re.search(r"终点|目的地|要去哪里|去哪(?:里)?|想去的地点", text):
        return False
    return bool(re.search(
        r"起点|出发地|从哪(?:里)?(?:出发|开始)|你(?:现在)?在哪(?:里)?|当前位置|从哪里出发",
        text,
    ))


def _continuation_start(query: str) -> str:
    text = (query or "").strip().strip("，,。")
    match = re.fullmatch(r"(?:从|我在|起点(?:是|在)?)(.+?)(?:出发)?", text)
    if match:
        return match.group(1).strip()
    if text and len(text) <= 30 and not re.search(r"[？?]|路线|天气|介绍|查询|去|到", text):
        return text
    return ""


def _explicit_route_edit(query: str, prior_route: dict) -> dict | None:
    """Apply a bounded spoken edit to the saved route, retaining untouched slots."""
    from agents.route_state import apply_change, validate_route_state

    text = (query or "").strip().rstrip("。")
    prefix = r"(?:把|将)?(?:刚才的|这条|原)路线"
    strategy = re.fullmatch(
        prefix + r"(?:改为|改成|走)(平坦优先|风景优先|最短路径)", text
    )
    if strategy:
        name = {"平坦优先": "flat", "风景优先": "scenery",
                "最短路径": "shortest"}[strategy.group(1)]
        changed = apply_change(prior_route, {"strategy": name})
        changed["strategy"]["source"] = "explicit_nl"
        return changed

    endpoint = re.fullmatch(prefix + r"(起点|终点)改为([^，,。；;]+)", text)
    if endpoint:
        place = endpoint.group(2).strip()
        if not place or place in {"我这里", "我的位置", "当前位置"}:
            return None
        changed = deepcopy(validate_route_state(prior_route))
        changed["start" if endpoint.group(1) == "起点" else "end"] = {
            "name": place, "type": "poi"
        }
        changed["route_id"] = f"route-{uuid4().hex}"
        return validate_route_state(changed)

    constraint = re.fullmatch(
        prefix + r"(不走台阶|避开台阶|避开陡坡|不走楼梯|可以走台阶了)", text
    )
    if constraint:
        changed = deepcopy(validate_route_state(prior_route))
        command = constraint.group(1)
        if command == "可以走台阶了":
            changed["hard_constraints"].pop("avoid_steps", None)
        elif command == "避开陡坡":
            changed["hard_constraints"]["slope"] = "avoid"
        else:
            changed["hard_constraints"]["avoid_steps"] = True
        changed["route_id"] = f"route-{uuid4().hex}"
        return validate_route_state(changed)
    return None



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
    active_task_request = (context or {}).get("active_task_request")
    preference_query = active_task_request if isinstance(active_task_request, str) and active_task_request.strip() else query
    if active_task_request and re.search(r"风景优先|景观优先|平坦优先|少爬坡|少走坡|赏樱|赏花", query):
        preference_query = query
    allow_preferences = route_preference_requested(preference_query, context, history)
    if active_task_request and re.search(r"最短|赶时间|赶课|快一点|快点|尽快|直接到|不绕路|别绕路", query):
        allow_preferences = False
    profile_msg = profile.build_profile_message(uid) if allow_preferences else None
    if profile_msg:
        messages.append({"role": "system", "content": profile_msg})
    if not allow_preferences and re.search(r"从.{1,20}(?:到|去)|路线|怎么走|通勤|上课|宿舍", query):
        messages.append({"role": "system", "content":
                         "本次为普通通勤或距离优先请求。所有规划工具省略 weights，"
                         "由策略层执行纯最短路径 distance/slope/scenery=1.00/0.00/0.00。"
                         "目的地为景点、天气、任务卡和历史画像均不能改变这个策略。"})

    # 出行方式注入：前端切换了步行/骑行/驾车，LLM 应在 plan_route 等工具调用中设 mode
    if travel_mode and travel_mode in ("walk", "bike", "drive"):
        messages.append({
            "role": "system",
            "content": f"用户当前选择的出行方式为 {travel_mode}（步行/骑行/驾车）。"
                        f"调用 plan_route / plan_via_route 时 mode 参数请设为 '{travel_mode}'。"
                        f"除非用户在消息中明确说「骑车/开车/步行」覆盖，否则用此值。",
        })

    # GPS 定位或地图手选点注入：地图手选点是明确的路由端点，不只是当前位置提示。
    gps_lines = []
    for label, coord in (("起点（用户当前位置）", coord_start), ("终点", coord_end)):
        if isinstance(coord, dict) and coord.get("lng") is not None and coord.get("lat") is not None:
            if label.startswith("起点") and coord.get("name") == "地图起点":
                label = "起点（用户在地图上手动选定的起点）"
            elif label == "终点" and coord.get("name") == "地图终点":
                label = "终点（用户在地图上手动选定的终点）"
            gps_lines.append(
                f"- {label}: WGS-84 坐标 lng={coord['lng']}, lat={coord['lat']}"
                f"（必须将此点作为对应坐标端点传入）"
            )
    if gps_lines:
        messages.append({"role": "system", "content": "用户本次请求携带了可用于路线规划的精确坐标：\n"
            + "\n".join(gps_lines)})
    if _manual_map_start_ref(coord_start):
        messages.append({"role": "system", "content":
            "用户已在地图上手动选定路线起点。若本轮文本没有明确写出另一个具体起点，"
            "无论需求写成‘去食堂’、‘去最近的食堂’等简略说法，都必须使用地图起点坐标规划，"
            "不能再询问用户当前在哪里。用户若在本轮明确指定了另一个起点，则以本轮文字为准。"})

    # 地图途经点注入：用户在地图上选的途经点坐标
    if isinstance(coord_waypoints, list) and coord_waypoints:
        wp_lines = []
        for i, wp in enumerate(coord_waypoints):
            if isinstance(wp, dict) and wp.get("lng") is not None and wp.get("lat") is not None:
                wp_lines.append(
                    f"- 途经点{i+1}: WGS-84 坐标 lng={wp['lng']}, lat={wp['lat']}"
                    f"（调用 plan_via_route 时按原顺序放入 via_points，使用"
                    f"{{name:'地图途经点{i+1}', type:'coord', lng:{wp['lng']}, lat:{wp['lat']}}}）"
                )
        if wp_lines:
            messages.append({
                "role": "system",
                "content": "用户在地图上按顺序标记了途经点，规划必须依次经过所有点；"
                           "调用 plan_via_route 时必须一次传入完整、有序的 via_points 数组：\n"
                           + "\n".join(wp_lines),
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
        active_task_request = context.get("active_task_request")
        if isinstance(active_task_request, str) and active_task_request.strip():
            messages.append({
                "role": "system",
                "content": "当前正在继续同一个未完成任务。下一条用户消息是原始请求的引用，"
                           "末尾用户消息是对上次追问的回答或修正；保留原请求中的其他目标、"
                           "途经点、出行方式和硬约束，明确冲突时以本轮用户输入为准。"
                           "只有完成整项请求或继续追问尚缺的关键信息后才能结束。",
            })
            messages.append({"role": "user", "content": active_task_request.strip()[:500]})
        policy = context.get("context_policy")
        if policy:
            messages.append({"role": "system", "content": policy})
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
    # 地图端已经把起点、终点和每个途经点解析成精确坐标，不应再让 LLM
    # 决定是否调用工具或重排用户选点。直接执行同一个 plan_via_route 工具，
    # 使地图多点规划不受模型超时、漏调工具或无工具文本回答影响。
    if ("地图标记" in (query or "") and isinstance(coord_start, dict)
            and isinstance(coord_end, dict) and isinstance(coord_waypoints, list)
            and coord_waypoints and len(coord_waypoints) <= agent_tools._MAX_VIA_POINTS):
        refs = [coord_start, *coord_waypoints, coord_end]
        if all(isinstance(ref, dict) and ref.get("lng") is not None and ref.get("lat") is not None
               for ref in refs):
            args = {
                "start": {"name": coord_start.get("name") or "地图起点", "type": "coord",
                          "lng": coord_start["lng"], "lat": coord_start["lat"]},
                "end": {"name": coord_end.get("name") or "地图终点", "type": "coord",
                        "lng": coord_end["lng"], "lat": coord_end["lat"]},
                "via_points": [
                    {"name": point.get("name") or f"地图途经点{index}", "type": "coord",
                     "lng": point["lng"], "lat": point["lat"]}
                    for index, point in enumerate(coord_waypoints, start=1)
                ],
                "mode": travel_mode if travel_mode in {"walk", "bike", "drive"} else "walk",
            }
            result, artifact = agent_tools.execute_tool(
                "plan_via_route", args, {"query": query, "uid": uid}
            )
            route = (artifact or {}).get("route") if isinstance(artifact, dict) else None
            if route:
                route["timings_ms"] = normalize_timings(route.get("timings_ms"), agent=0.0)
                return {
                    "response_kind": "route",
                    "message": _build_route_message(route),
                    "route": route,
                    "route_kind": (artifact or {}).get("route_kind", "via"),
                    "candidates": None, "clarify": None, "suggestions": None, "turns": 0,
                }
            return {
                "response_kind": "chat",
                "message": (result or {}).get("message", "多途经点路线暂时无法生成，请调整地点后重试。"),
                "route": None, "route_kind": None, "candidates": None,
                "clarify": None, "suggestions": None, "turns": 0,
            }

    # A mode-only correction is an edit to the saved route, even if a weather
    # or POI turn appeared in between. Rebuild from the validated route state
    # so endpoints, ordered stops, hard constraints and strategy survive.
    from agents.context_builder import is_route_followup
    from agents.parser import detect_travel_mode
    mode_change = re.search(
        r"(?:切换到?|改成|改为|换成|改走).{0,5}(?:步行|走路|骑行|骑车|开车|驾车)",
        query or "",
    )
    prior_route = (context or {}).get("previous_route_state")
    if mode_change and isinstance(prior_route, dict) and is_route_followup(query):
        from agents.route_state import apply_change
        from api.routes import _replan_tool_request

        mode, explicit = detect_travel_mode(query)
        if explicit:
            requested = apply_change(prior_route, {"travel_mode": mode})
            tool_name, args = _replan_tool_request(requested)
            result, artifact = agent_tools.execute_tool(
                tool_name, args,
                {"query": requested["original_query"], "uid": uid, "replan": True},
            )
            route = (artifact or {}).get("route") if isinstance(artifact, dict) else None
            if route:
                route["timings_ms"] = normalize_timings(route.get("timings_ms"), agent=0.0)
                return {"response_kind": "route", "message": _build_route_message(route),
                        "route": route, "route_kind": requested["route_kind"],
                        "candidates": None, "clarify": None, "suggestions": None, "turns": 0}
            return {"response_kind": "chat",
                    "message": (result or {}).get("message") or "这条路线暂时无法按新出行方式规划。",
                    "route": None, "route_kind": None, "candidates": None,
                    "clarify": None, "suggestions": None, "turns": 0}

    # A spoken edit to an existing route must preserve its ordered stops and
    # untouched constraints. The LLM may otherwise issue a plausible direct
    # route after seeing only the endpoint and strategy in the short context.
    if isinstance(prior_route, dict) and is_route_followup(query):
        requested = _explicit_route_edit(query, prior_route)
        if requested is not None:
            from api.routes import _replan_tool_request

            tool_name, args = _replan_tool_request(requested)
            result, artifact = agent_tools.execute_tool(
                tool_name, args, {"query": query, "uid": uid, "replan": True},
            )
            route = (artifact or {}).get("route") if isinstance(artifact, dict) else None
            if route:
                route["timings_ms"] = normalize_timings(route.get("timings_ms"), agent=0.0)
                return {"response_kind": "route", "message": _build_route_message(route),
                        "route": route, "route_kind": requested["route_kind"],
                        "candidates": None, "clarify": None, "suggestions": None,
                        "turns": 0}
            return {"response_kind": "chat",
                    "message": (result or {}).get("message") or "这条路线暂时无法按新要求规划。",
                    "route": None, "route_kind": None, "candidates": None,
                    "clarify": None, "suggestions": None, "turns": 0}

    # Explicit "via X to Y" requests have a required stop. Resolve that
    # structure before the LLM can accidentally use X as the start and return
    # a plausible-looking direct route that silently skips the stop.
    original_request = (context or {}).get("active_task_request") or query
    named_via = _named_via_request(original_request)
    if named_via:
        revised_start = _continuation_start(query) if original_request != query else ""
        start_name = revised_start or named_via["start"]
        if start_name in {"我这", "我这里", "我的位置", "当前位置"}:
            start_name = ""
        if not start_name and not coord_start:
            question = f"从哪里出发？我会经过{named_via['via']}，再到{named_via['end']}。"
            return {"response_kind": "clarify", "message": question, "route": None,
                    "route_kind": None, "candidates": None,
                    "clarify": {"question": question, "options": []},
                    "suggestions": None, "turns": 0}
        start_ref = ({"name": coord_start.get("name") or "我的位置", "type": "coord",
                      "lng": coord_start["lng"], "lat": coord_start["lat"]}
                     if not start_name and coord_start else {"name": start_name, "type": "poi"})
        full_request = str(original_request) + " " + str(query)
        constraints = {}
        if "避坡" in full_request or "陡坡" in full_request:
            constraints["slope"] = "avoid"
        if re.search(r"(?:不走|避开|不要走|绕开)(?:楼梯|台阶)", full_request):
            constraints["avoid_steps"] = True
        if re.search(r"不(?:用|需要)(?:再)?避(?:开)?(?:陡坡|坡)|可以(?:爬|走)(?:坡|陡坡)", query):
            constraints.pop("slope", None)
        if re.search(r"(?:可以|允许)(?:走|经过)?(?:楼梯|台阶)|不(?:用|需要)避开(?:楼梯|台阶)", query):
            constraints.pop("avoid_steps", None)
        mode = travel_mode if travel_mode in {"walk", "bike", "drive"} else "walk"
        for text in (original_request, query):
            if re.search(r"开车|驾车", text): mode = "drive"
            elif re.search(r"骑车|骑行", text): mode = "bike"
            elif "步行" in text: mode = "walk"
        args = {"start": start_ref, "end": {"name": named_via["end"], "type": "poi"},
                "via_points": [{"name": named_via["via"], "type": "poi"}], "mode": mode}
        if constraints:
            args["constraints"] = constraints
        result, artifact = agent_tools.execute_tool(
            "plan_via_route", args, {"query": full_request, "uid": uid}
        )
        route = (artifact or {}).get("route") if isinstance(artifact, dict) else None
        if route:
            route["timings_ms"] = normalize_timings(route.get("timings_ms"), agent=0.0)
            return {"response_kind": "route", "message": _build_route_message(route),
                    "route": route, "route_kind": "via", "candidates": None,
                    "clarify": None, "suggestions": None, "turns": 0}
        question = (result or {}).get("message") or "途经点或终点暂时无法确认，请换个地点名称。"
        return {"response_kind": "clarify", "message": question, "route": None,
                "route_kind": None, "candidates": None,
                "clarify": {"question": question, "options": []},
                "suggestions": None, "turns": 0}

    # 模糊的类别需求走确定性的候选检索，避免模型按熟悉度挑固定 POI。
    # 起点只取本轮坐标、用户本轮明确说出的地点，或正在回答的起点追问；不继承旧路线终点。
    recommendation_query = original_request
    recommendation_categories = agent_tools.infer_recommendation_subcategories(recommendation_query)
    recommendation_is_catalog = agent_tools.is_poi_catalog_query(recommendation_query)
    recommendation_has_target = agent_tools._query_has_explicit_poi_destination(recommendation_query)
    route_to_nearest = bool(
        agent_tools.query_requests_route(recommendation_query)
        and re.search(r"最近|离我最(?:近|方便)|距离最短", recommendation_query)
    )
    if (recommendation_categories and not recommendation_is_catalog
            and not recommendation_has_target
            and (not agent_tools.query_requests_route(recommendation_query) or route_to_nearest)):
        start_ref = None
        explicit_origin = agent_tools.recommendation_origin_from_text(query)
        if explicit_origin:
            # 本轮文字明确给出的地点优先于页面附带的 GPS 或地图坐标。
            start_ref = {"type": "poi", "name": explicit_origin}
        elif original_request != query:
            # 起点追问的简短回答（如“工学部”）也是本轮明确位置，应优先采用。
            answered_origin, alternatives = agent_tools.find_poi_ambiguous(query.strip())
            if answered_origin and not alternatives:
                start_ref = {"type": "poi", "name": answered_origin["name"]}

        if start_ref is None and isinstance(coord_start, dict):
            try:
                lng, lat = float(coord_start.get("lng")), float(coord_start.get("lat"))
                if (-180 <= lng <= 180 and -90 <= lat <= 90):
                    start_ref = {"type": "coord", "name": coord_start.get("name") or "我的位置",
                                 "lng": lng, "lat": lat}
            except (TypeError, ValueError):
                start_ref = None

        if start_ref is None:
            question = "你从哪里出发？可以在地图上点起点，或告诉我所在的地点，我再按路网距离推荐。"
            options = ["地图上选起点", "告诉我所在地点"]
            return {"response_kind": "clarify", "message": question,
                    "route": None, "route_kind": None, "candidates": None,
                    "clarify": {"question": question, "options": options},
                    "suggestions": None, "turns": 0}

        recommendation_args = {
            "start": start_ref,
            "subcategories": recommendation_categories,
            "mode": travel_mode if travel_mode in {"walk", "bike", "drive"} else "walk",
            "max_distance_m": 10000,
            "limit": 4,
        }
        result, artifact = agent_tools.execute_tool(
            "search_poi_candidates", recommendation_args,
            {"query": recommendation_query, "uid": uid},
        )
        candidates = (artifact or {}).get("candidates") if isinstance(artifact, dict) else None
        if result.get("error"):
            return {"response_kind": "chat", "message": result.get("message") or "附近地点暂时无法核实。",
                    "route": None, "route_kind": None, "candidates": None,
                    "clarify": None, "suggestions": None, "turns": 0}
        if not candidates:
            return {"response_kind": "chat",
                    "message": result.get("message") or "当前路网范围内没有找到合适的地点。",
                    "route": None, "route_kind": None, "candidates": [],
                    "clarify": None, "suggestions": None, "turns": 0}

        if route_to_nearest:
            route_request = str(recommendation_query) + " " + str(query)
            route_args = {
                "start": start_ref,
                "end": {"type": "poi", "name": candidates[0].get("name", "")},
                "mode": recommendation_args["mode"],
            }
            if re.search(r"平坦优先|少爬坡|少走坡|避坡|不想爬坡", route_request):
                route_args["weights"] = {"distance": 0.2, "slope": 0.6, "scenery": 0.2}
                route_args["constraints"] = {"slope": "avoid"}
            elif re.search(r"风景优先|景观优先|赏樱|看风景|风景好", route_request):
                route_args["weights"] = {"distance": 0.15, "slope": 0.15, "scenery": 0.7}
            if re.search(r"(?:不走|避开|不要走|绕开)(?:楼梯|台阶)", route_request):
                route_args["constraints"] = {**route_args.get("constraints", {}), "avoid_steps": True}
            route_result, route_artifact = agent_tools.execute_tool(
                "plan_route", route_args, {"query": route_request, "uid": uid},
            )
            route = ((route_artifact or {}).get("route")
                     if isinstance(route_artifact, dict) else None)
            if route:
                route["timings_ms"] = normalize_timings(route.get("timings_ms"), agent=0.0)
                return {"response_kind": "route", "message": _build_route_message(route),
                        "route": route, "route_kind": "direct", "candidates": None,
                        "clarify": None, "suggestions": None, "turns": 0}
            if route_result.get("error"):
                return {"response_kind": "chat",
                        "message": route_result.get("message") or "找到附近地点，但当前无法生成可靠路线。",
                        "route": None, "route_kind": None, "candidates": None,
                        "clarify": None, "suggestions": None, "turns": 0}

        first = candidates[0]
        start_name = result.get("start_name") or start_ref.get("name") or "你选定的起点"
        if start_ref.get("type") == "poi":
            for candidate in candidates:
                candidate["recommendation_start_name"] = start_name
        category = first.get("subcategory_label") or first.get("subcategory")
        distance = first.get("distance_m")
        if distance is not None:
            summary = (f"从{start_name}出发，我按当前路网估算距离排了 {len(candidates)} 个选项。"
                       f"最近的是{first.get('name', '候选地点')}（{category}，约 {round(float(distance))} 米）。")
        else:
            summary = f"我按从{start_name}出发的路网距离排了 {len(candidates)} 个选项。"
        summary += "起点和地点到道路的接驳按直线估算，未核实实际通行；点选地点可继续规划路线。"
        return {"response_kind": "candidates", "message": summary,
                "route": None, "route_kind": None, "candidates": candidates,
                "clarify": None, "suggestions": None, "turns": 0}

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
    agent_elapsed_ms = 0.0
    active_task_request = (context or {}).get("active_task_request")
    if not isinstance(active_task_request, str):
        active_task_request = ""
    # A clarification answer is still part of the original route request.
    preference_query = active_task_request or query
    if active_task_request and re.search(r"风景优先|景观优先|平坦优先|少爬坡|少走坡|赏樱|赏花", query):
        preference_query = query
    allow_preferences = route_preference_requested(preference_query, context, history)
    if active_task_request and re.search(r"最短|赶时间|赶课|快一点|快点|尽快|直接到|不绕路|别绕路", query):
        allow_preferences = False
    previous = (context or {}).get("previous_intent") or {}
    prev_constraints = previous.get("constraints") or (context or {}).get("constraints") or {}
    constraint_query = (active_task_request or "") + " " + query
    avoid_slope = prev_constraints.get("slope") == "avoid" or "避坡" in constraint_query or "陡坡" in constraint_query
    if re.search(r"(?:不(?:用|需要)(?:再)?避(?:开)?(?:陡坡|坡)|可以(?:爬|走)(?:坡|陡坡))", query):
        avoid_slope = False
    avoid_steps = bool(re.search(r"(?:不走|避开|不要走|绕开)(?:楼梯|台阶)", constraint_query))
    if re.search(r"(?:可以|允许)(?:走|经过)?(?:楼梯|台阶)|不(?:用|需要)避开(?:楼梯|台阶)", query):
        avoid_steps = False
    active_weights = None
    if allow_preferences:
        active_weights = previous.get("weights")
    route_tools = {"plan_route", "plan_via_route", "plan_tour", "plan_multimodal_route"}

    for _ in range(MAX_TURNS):
        if time.monotonic() - started > TIME_BUDGET_S:
            logger.warning("Agent 循环超时（%.1fs），基于已有结果收尾", time.monotonic() - started)
            break
        turns += 1

        try:
            llm_started = time.perf_counter()
            response = client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=messages,
                tools=agent_tools.TOOL_SCHEMAS,
                temperature=0.0,
                max_tokens=LLM_MAX_TOKENS,
            )
            agent_elapsed_ms += (time.perf_counter() - llm_started) * 1000
        except Exception as e:
            agent_elapsed_ms += (time.perf_counter() - llm_started) * 1000
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
            manual_start = _manual_map_start_ref(coord_start)
            # A new explicit origin in this turn supersedes the original task;
            # otherwise keep the original named origin through clarifications.
            explicit_start = (_explicit_start_name(query)
                              or _explicit_start_name(active_task_request))
            manual_start_applies = bool(
                manual_start and (not explicit_start or explicit_start == manual_start["name"])
            )
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                result = {"error": "invalid_args", "message": "参数不是合法 JSON，请修正后重试"}
                artifact = None
            else:
                if (name == "ask_user" and manual_start_applies and isinstance(args, dict)
                        and _asks_for_origin(args.get("question", ""))):
                    # The selected map origin already answers this clarification. Return it
                    # to the model as tool context and let the bounded agent loop continue.
                    result = {
                        "message": "用户已经在地图上手动选定起点，精确坐标已随请求提供。"
                                   "请直接以该坐标作为起点继续处理，不要再次询问起点。"
                    }
                    artifact = None
                else:
                    if name in route_tools and isinstance(args, dict):
                        if manual_start_applies:
                            # Do not let the model omit the selected origin or replace it with
                            # a guessed POI; an explicitly named different origin remains intact.
                            args["start"] = manual_start
                        if not allow_preferences:
                            args.pop("weights", None)
                        elif active_weights and "weights" not in args:
                            args["weights"] = active_weights
                        elif args.get("weights"):
                            active_weights = args["weights"]
                        constraints = args.get("constraints")
                        if isinstance(constraints, dict):
                            avoid_slope = avoid_slope or constraints.get("slope") == "avoid"
                        if avoid_slope:
                            args["constraints"] = {**(constraints if isinstance(constraints, dict) else {}),
                                                   "slope": "avoid"}
                        if avoid_steps:
                            args["constraints"] = {**(args.get("constraints") or {}),
                                                   "avoid_steps": True}
                    result, artifact = agent_tools.execute_tool(
                        name, args, {"query": constraint_query if name in route_tools else query,
                                     "uid": uid}
                    )

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
    if artifact_route:
        artifact_route["timings_ms"] = normalize_timings(
            artifact_route.get("timings_ms"), agent=agent_elapsed_ms
        )
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
