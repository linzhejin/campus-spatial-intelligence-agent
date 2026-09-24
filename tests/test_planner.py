"""Planner（Agent 循环）单元测试：全部 mock LLM 客户端与工具执行，不依赖网络和真实路网。"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import agents.planner as planner


def _fake_msg(content=None, tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    msg.model_dump = lambda exclude_none=False: {
        "role": "assistant",
        **({"content": content} if content else {}),
        **({"tool_calls": [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in (tool_calls or [])
        ]} if tool_calls else {}),
    }
    return msg


def _fake_response(content=None, tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=_fake_msg(content, tool_calls))])


def _fake_tool_call(name, args):
    return SimpleNamespace(
        id=f"call_{name}",
        function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)),
    )


class FakeClient:
    """按脚本依次返回响应的伪 LLM 客户端。"""

    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.calls = []

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setattr(planner.config, "DEEPSEEK_API_KEY", "test-key")


def _run(client, **kwargs):
    with patch.object(planner, "_make_client", return_value=client):
        return planner.run_agent("测试 query", **kwargs)


class TestAgentLoop:
    def test_direct_answer_no_tool(self):
        client = FakeClient([_fake_response(content="今天适合散步，要去樱顶吗？")])
        resp = _run(client)
        assert resp["response_kind"] == "chat"
        assert resp["message"] == "今天适合散步，要去樱顶吗？"
        assert resp["turns"] == 1

    def test_tool_call_then_answer(self):
        route_payload = {
            "recommended": [{"lng": 1, "lat": 2}], "distance_m": 800,
            "start_name": "牌坊", "end_name": "樱顶", "mode": "walk",
        }
        client = FakeClient([
            _fake_response(tool_calls=[_fake_tool_call("plan_route", {"start": {"name": "牌坊"}, "end": {"name": "樱顶"}})]),
        ])
        with patch.object(planner.agent_tools, "execute_tool",
                          return_value=(route_payload, {"route": route_payload})) as mock_exec:
            resp = _run(client)
        assert mock_exec.call_count == 1
        assert resp["response_kind"] == "route"
        assert resp["route"] == route_payload
        assert resp["route_kind"] == "direct"
        # 路线产出后立即收尾：不再多调一轮 LLM（防 DSML 泄漏），消息由结构化数据拼出
        assert resp["message"] == "已为你规划好从牌坊到樱顶的步行路线，约 800 米。"
        assert len(client.calls) == 1

    def test_candidates_artifact(self):
        cands = [{"name": "桂园食堂"}, {"name": "梅园食堂"}]
        client = FakeClient([
            _fake_response(tool_calls=[_fake_tool_call("search_poi_candidates", {"subcategory": "canteen"})]),
            _fake_response(content="找到两个食堂，你选哪个？"),
        ])
        with patch.object(planner.agent_tools, "execute_tool",
                          return_value=({"candidates": cands}, {"candidates": cands})):
            resp = _run(client)
        assert resp["response_kind"] == "candidates"
        assert resp["candidates"] == cands

    def test_ask_user_terminates_loop(self):
        client = FakeClient([
            _fake_response(tool_calls=[_fake_tool_call("ask_user", {"question": "从哪里出发？", "options": ["牌坊", "我的位置"]})]),
        ])
        with patch.object(planner.agent_tools, "execute_tool",
                          return_value=({"question": "从哪里出发？"}, {"clarify": {"question": "从哪里出发？", "options": ["牌坊"]}})):
            resp = _run(client)
        assert resp["response_kind"] == "clarify"
        assert resp["clarify"]["question"] == "从哪里出发？"
        assert len(client.calls) == 1  # 终止型工具：不再继续循环

    def test_invalid_tool_args_reinjected(self):
        """工具参数非法 JSON → 错误回注，循环继续直至 LLM 给出答复。"""
        bad_call = SimpleNamespace(
            id="call_bad",
            function=SimpleNamespace(name="plan_route", arguments="{not valid json"),
        )
        client = FakeClient([
            _fake_response(tool_calls=[bad_call]),
            _fake_response(content="参数有误，请再说一次起点终点。"),
        ])
        with patch.object(planner.agent_tools, "execute_tool") as mock_exec:
            resp = _run(client)
        mock_exec.assert_not_called()  # 参数非法时根本不执行工具
        assert resp["response_kind"] == "chat"
        tool_msgs = [m for m in client.calls[1]["messages"] if m.get("role") == "tool"]
        assert tool_msgs and "invalid_args" in tool_msgs[0]["content"]

    def test_llm_failure_raises_planner_error(self):
        client = FakeClient([ConnectionError("api down")])
        with pytest.raises(planner.PlannerError):
            _run(client)

    def test_no_api_key_raises(self, monkeypatch):
        monkeypatch.setattr(planner.config, "DEEPSEEK_API_KEY", "")
        with pytest.raises(planner.PlannerError):
            planner.run_agent("query")

    def test_loop_exhaustion_with_artifact_fallback_message(self):
        """循环耗尽但有路径 artifact → 兜底文案收尾而非报错。"""
        route_payload = {"recommended": [], "distance_m": 500}
        scripted = [
            _fake_response(tool_calls=[_fake_tool_call("plan_route", {"start": {"name": "A"}, "end": {"name": "B"}})]),
        ] * planner.MAX_TURNS  # LLM 一直调工具不给结论
        client = FakeClient(scripted)
        with patch.object(planner.agent_tools, "execute_tool",
                          return_value=(route_payload, {"route": route_payload})):
            resp = _run(client)
        assert resp["response_kind"] == "route"
        assert resp["message"]  # 有兜底文案

    def test_loop_exhaustion_empty_raises(self):
        """循环耗尽且无任何产出 → PlannerError 触发路由层兜底。"""
        client = FakeClient([_fake_response(tool_calls=[_fake_tool_call("get_weather", {})])] * planner.MAX_TURNS)
        with patch.object(planner.agent_tools, "execute_tool", return_value=({"weather": "晴"}, None)):
            with pytest.raises(planner.PlannerError):
                _run(client)


class TestChatEndpointAgentFirst:
    """/api/chat 接线：planner 成功走 Agent；planner 异常落回旧管道。"""

    @pytest.fixture
    def client(self):
        import app as app_module
        app_module.app.config["TESTING"] = True
        return app_module.app.test_client()

    def test_chat_uses_agent_response(self, client):
        fake_resp = {"response_kind": "chat", "message": "你好呀", "route": None,
                     "route_kind": None, "candidates": None, "clarify": None, "turns": 1}
        with patch("agents.planner.run_agent", return_value=fake_resp):
            r = client.post("/api/chat", json={"query": "你好"})
        assert r.status_code == 200
        data = r.get_json()["data"]
        assert data["task_type"] == "chat"
        assert data["reply"] == "你好呀"

    def test_chat_falls_back_when_planner_raises(self, client):
        with patch("agents.planner.run_agent", side_effect=planner.PlannerError("down")), \
             patch("api.routes.parse_query") as mock_parse, \
             patch("api.routes.generate_chat_response", return_value="兜底回复"):
            intent = SimpleNamespace(
                model_dump=lambda: {"task_type": "chat", "start": None, "end": None},
            )
            mock_parse.return_value = intent
            r = client.post("/api/chat", json={"query": "你好"})
        assert r.status_code == 200
        assert r.get_json()["data"]["task_type"] == "chat"

    def test_internal_bug_does_not_trigger_legacy_parser(self, client):
        with patch("agents.planner.run_agent", side_effect=RuntimeError("implementation bug")), \
             patch("api.routes.parse_query") as mock_parse:
            r = client.post("/api/chat", json={"query": "从教五到图书馆"})
        mock_parse.assert_not_called()
        assert r.status_code == 500
        assert r.get_json()["error"] == "internal_error"
        assert "implementation bug" not in r.get_data(as_text=True)


class TestPreferenceReliability:
    def test_retry_keeps_explicit_weights(self):
        weights = {"distance": 0.2, "slope": 0.1, "scenery": 0.7}
        client = FakeClient([
            _fake_response(tool_calls=[_fake_tool_call("plan_route", {"weights": weights})]),
            _fake_response(tool_calls=[_fake_tool_call("plan_route", {})]),
            _fake_response(content="暂未找到可行路线。"),
        ])
        with patch.object(planner, "_make_client", return_value=client), \
             patch.object(planner.agent_tools, "execute_tool", return_value=({"error": "route_not_found"}, None)) as execute:
            planner.run_agent("从教五到图书馆，走风景好的路")
        assert execute.call_args_list[1].args[1]["weights"] == weights

    def test_slope_avoid_is_enforced_when_model_omits_it(self):
        client = FakeClient([_fake_response(tool_calls=[_fake_tool_call("plan_route", {})]),
                             _fake_response(content="没有可行路线。")])
        with patch.object(planner, "_make_client", return_value=client), \
             patch.object(planner.agent_tools, "execute_tool", return_value=({"error": "route_not_found"}, None)) as execute:
            planner.run_agent("从教五到图书馆，避开陡坡")
        assert execute.call_args.args[1]["constraints"]["slope"] == "avoid"

    def test_tour_exposes_all_route_preferences(self):
        schemas = {t["function"]["name"]: t["function"]["parameters"]
                   for t in planner.agent_tools.TOOL_SCHEMAS}
        assert {"mode", "weights", "constraints"} <= schemas["plan_tour"]["properties"].keys()

    @pytest.mark.parametrize("query", ["从教五到图书馆", "从珞珈门到樱顶，赶时间", "回宿舍上课别绕路"])
    def test_commute_ignores_model_invented_scenery_weights(self, query):
        client = FakeClient([_fake_response(tool_calls=[_fake_tool_call("plan_route", {
            "start": {"name": "教五"}, "end": {"name": "图书馆"},
            "weights": {"distance": 0.2, "slope": 0.1, "scenery": 0.7},
        })])])
        route = {"distance_m": 500, "mode": "walk"}
        with patch.object(planner, "_make_client", return_value=client), \
             patch.object(planner.agent_tools, "execute_tool", return_value=(route, {"route": route})) as execute:
            planner.run_agent(query)
        args = execute.call_args.args[1]
        assert args.get("weights") is None or args["weights"] == {
            "distance": 0.90, "slope": 0.05, "scenery": 0.05}

    def test_explicit_preference_is_preserved(self):
        weights = {"distance": 0.2, "slope": 0.6, "scenery": 0.2}
        client = FakeClient([_fake_response(tool_calls=[_fake_tool_call("plan_route", {
            "start": {"name": "教五"}, "end": {"name": "图书馆"},
            "weights": weights, "constraints": {"slope": "avoid"},
        })])])
        route = {"distance_m": 500, "mode": "walk"}
        with patch.object(planner, "_make_client", return_value=client), \
             patch.object(planner.agent_tools, "execute_tool", return_value=(route, {"route": route})) as execute:
            planner.run_agent("从教五到图书馆，避开陡坡")
        assert execute.call_args.args[1]["weights"] == weights

    def test_retry_cannot_drop_slope_constraint(self):
        client = FakeClient([
            _fake_response(tool_calls=[_fake_tool_call("plan_route", {
                "start": {"name": "A"}, "end": {"name": "B"}, "constraints": {"slope": "avoid"},
            })]),
            _fake_response(tool_calls=[_fake_tool_call("plan_route", {
                "start": {"name": "A"}, "end": {"name": "B"}, "constraints": {"slope": "normal"},
            })]),
            _fake_response(content="暂无满足避坡要求的路线。"),
        ])
        with patch.object(planner, "_make_client", return_value=client), \
             patch.object(planner.agent_tools, "execute_tool", return_value=({"error": "route_not_found"}, None)) as execute:
            planner.run_agent("从 A 到 B，避开陡坡")
        assert execute.call_args_list[1].args[1]["constraints"]["slope"] == "avoid"
