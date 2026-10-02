"""Durable LangGraph orchestration around the existing campus route agent."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from storage.task_repository import append_run_event, get_run_input


class AgentRunState(TypedDict, total=False):
    run_id: str
    worker_id: str
    input: dict[str, Any]
    agent_response: dict[str, Any]
    result: dict[str, Any]


class AgentRunContext(TypedDict):
    """Lease-scoped values that must never be restored from a graph checkpoint."""

    worker_id: str


def _emit(
    state: AgentRunState, runtime: Runtime[AgentRunContext], event: str, node: str, label: str,
    database_url: str | None,
) -> None:
    stored = append_run_event(
        database_url, state["run_id"], runtime.context["worker_id"], event,
        {"label": label}, node_id=node,
    )
    if stored is None:
        raise RuntimeError("run lease lost or task cancelled")


def _coordinate(location: dict | None) -> dict | None:
    if not isinstance(location, dict):
        return None
    point = location.get("coordinates")
    if not isinstance(point, dict):
        return None
    return {
        "lng": point.get("longitude"),
        "lat": point.get("latitude"),
        "crs": point.get("crs"),
        "name": location.get("name"),
    }


def _weather_message(weather: dict) -> str:
    parts = [f"当前天气：{weather.get('weather') or '暂缺'}"]
    if weather.get("temperature") is not None:
        parts.append(f"气温 {weather['temperature']}℃")
    if weather.get("windpower"):
        parts.append(f"风力 {weather['windpower']}")
    if weather.get("advice"):
        parts.append(str(weather["advice"]).strip())
    message = "，".join(parts).rstrip("，")
    return message if message.endswith(("。", "！", "？", "!", "?")) else message + "。"


def _run_weather_tool():
    from agents.tools import execute_tool

    result, _ = execute_tool("get_weather", {}, {})
    if not isinstance(result, dict) or result.get("error"):
        detail = result.get("message") if isinstance(result, dict) else None
        raise RuntimeError(detail or "天气暂时获取不到")
    return result


def build_agent_workflow(
    checkpointer, agent: Callable[..., dict] | None = None,
    database_url: str | None = None,
    interrupt_after: list[str] | None = None,
):
    """Compile a bounded task graph; the injected agent is for deterministic replay."""
    if agent is None:
        from agents.planner import run_agent
        agent = run_agent

    def load_task(state: AgentRunState, runtime: Runtime[AgentRunContext]):
        _emit(state, runtime, "node_started", "load_task", "正在恢复本轮对话和任务状态", database_url)
        run_input = get_run_input(database_url, state["run_id"])
        if not run_input:
            raise RuntimeError("run input is missing")
        if run_input.get("cancel_requested"):
            raise RuntimeError("task cancelled")
        _emit(state, runtime, "node_completed", "load_task", "已载入可信会话上下文", database_url)
        return {"input": run_input}

    def execute_agent(state: AgentRunState, runtime: Runtime[AgentRunContext]):
        _emit(state, runtime, "node_started", "execute_agent", "正在识别需求并调用校园空间工具", database_url)
        data = state["input"]
        task = data.get("task") or {}
        route_spec = task.get("route_spec") or {}
        start = _coordinate(route_spec.get("start"))
        end = _coordinate(route_spec.get("end"))
        stops = route_spec.get("stops") or []
        waypoints = [point for point in (_coordinate(item) for item in stops) if point]
        history = data.get("history") or []
        from agents.context_builder import build_agent_context, is_route_followup
        continuation = data.get("task_revision", 0) > 0
        origin_query = data.get("task_origin_query")
        previous_route_state = data.get("previous_route_state")
        previous_task_id = data.get("previous_route_state_task_id")
        # A clarification of a fresh task must not import a completed route
        # from another task, even though the route is still in the conversation.
        if (continuation and previous_task_id and data.get("task_id")
                and previous_task_id != data["task_id"]
                and not is_route_followup(origin_query or data["query"])):
            previous_route_state = None
        context = build_agent_context(
            data["query"], history, previous_route_state,
            same_task=continuation,
        )
        if continuation and isinstance(origin_query, str) and origin_query.strip():
            context["active_task_request"] = origin_query.strip()
        from api.routes import _normalize_chat_context
        context = _normalize_chat_context(context)
        route_state = task.get("route_spec") or {}
        from agents.task_planner import build_task_plan
        plan = build_task_plan(
            context.get("active_task_request") or data["query"],
            has_route_context=bool(start or end or waypoints),
        )
        response = None
        weather = None
        requirement_results: dict[str, str] = {}
        errors: dict[str, str] = {}

        def run_route_agent():
            return agent(
                data["query"], context=context, history=history,
                coord_start=start, coord_end=end, coord_waypoints=waypoints,
                travel_mode=route_state.get("travel_mode", "walk"),
                uid=data.get("owner_id"),
            )

        if plan.kind == "composite":
            # Route interpretation and live weather are independent, so neither
            # is allowed to hold the other requirement hostage.
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="agent-subtask") as pool:
                route_future = pool.submit(run_route_agent)
                weather_future = pool.submit(_run_weather_tool)
                try:
                    response = route_future.result()
                    if not isinstance(response, dict):
                        raise TypeError("agent returned a non-object response")
                    agent_requirement = plan.nodes[0].requirement_ids[0]
                    requirement_results[agent_requirement] = (
                        "needs_input" if response.get("response_kind") == "clarify" else "satisfied"
                    )
                except Exception as error:
                    agent_requirement = plan.nodes[0].requirement_ids[0]
                    errors[agent_requirement] = type(error).__name__
                    requirement_results[agent_requirement] = "failed"
                try:
                    weather = weather_future.result()
                    requirement_results["weather"] = "satisfied"
                except Exception as error:
                    errors["weather"] = type(error).__name__
                    requirement_results["weather"] = "failed"
        elif plan.kind == "weather":
            try:
                weather = _run_weather_tool()
                requirement_results["weather"] = "satisfied"
            except Exception as error:
                errors["weather"] = type(error).__name__
                requirement_results["weather"] = "failed"
        else:
            try:
                response = run_route_agent()
                if not isinstance(response, dict):
                    raise TypeError("agent returned a non-object response")
                requirement_results["route" if plan.kind == "route" else "answer"] = (
                    "needs_input" if response.get("response_kind") == "clarify" else "satisfied"
                )
            except Exception as error:
                errors["route" if plan.kind == "route" else "answer"] = type(error).__name__
                requirement_results["route" if plan.kind == "route" else "answer"] = "failed"

        clarify = bool(response and response.get("response_kind") == "clarify")
        succeeded = sum(value in {"satisfied", "needs_input"} for value in requirement_results.values())
        total = len(plan.requirements)
        execution_status = "completed" if succeeded == total else ("partial" if succeeded else "failed")
        if response is None:
            response = {
                "response_kind": "chat",
                "message": "这次没有完成请求，请稍后重试。",
                "turns": 0,
            }
        if weather:
            weather_text = _weather_message(weather)
            if plan.kind == "weather":
                response["message"] = weather_text
            else:
                if requirement_results.get("route") == "failed":
                    route_failure = "路线暂时无法规划，请检查起终点后重试。"
                    response["message"] = route_failure + "\n" + weather_text
                else:
                    response["message"] = (response.get("message", "").strip() + "\n" + weather_text).strip()
        elif "weather" in errors:
            weather_text = "天气服务暂时不可用。"
            if plan.kind == "composite":
                response["message"] = (response.get("message", "").strip() + "\n" + weather_text).strip()
            else:
                response["message"] = weather_text
        response.update({
            "weather": weather,
            "execution_status": execution_status,
            "requirement_results": requirement_results,
            "requirement_errors": errors,
            "needs_input": clarify,
        })
        _emit(state, runtime, "node_completed", "execute_agent", "需求分析与空间工具执行完成", database_url)
        return {"agent_response": response}

    def compose_result(state: AgentRunState, runtime: Runtime[AgentRunContext]):
        _emit(state, runtime, "node_started", "compose_result", "正在整理路线与说明", database_url)
        from api.routes import _agent_response_to_legacy

        data = state["input"]
        route_spec = (data.get("task") or {}).get("route_spec") or {}
        start = _coordinate(route_spec.get("start"))
        end = _coordinate(route_spec.get("end"))
        result = _agent_response_to_legacy(state["agent_response"], start, end)
        response = state["agent_response"]
        for key in ("weather", "execution_status", "requirement_results", "requirement_errors"):
            result[key] = response.get(key)
        if response.get("needs_input"):
            result["needs_input"] = True
            result["clarify"] = response.get("clarify")
            # The composite answer may include a completed independent requirement.
            result["message"] = response.get("message", result.get("message", ""))
            if result.get("task_type") == "unknown":
                result["clarify"] = response.get("clarify")
        _emit(state, runtime, "node_completed", "compose_result", "结果已整理，可以查看", database_url)
        return {"result": result}

    graph = StateGraph(AgentRunState, context_schema=AgentRunContext)
    graph.add_node("load_task", load_task)
    graph.add_node("execute_agent", execute_agent)
    graph.add_node("compose_result", compose_result)
    graph.add_edge(START, "load_task")
    graph.add_edge("load_task", "execute_agent")
    graph.add_edge("execute_agent", "compose_result")
    graph.add_edge("compose_result", END)
    compile_options = {"checkpointer": checkpointer}
    if interrupt_after:
        compile_options["interrupt_after"] = interrupt_after
    return graph.compile(**compile_options)
