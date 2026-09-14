"""真实 LLM 六场景验收冒烟脚本。

只验证 Agent 循环能正常走完并返回合理结构，不断言具体文案（LLM 输出有随机性）。
"""

import sys
import time

from agents.planner import run_agent, PlannerError


SCENARIOS = [
    {
        "name": "1. 明确 A→B 路径规划",
        "query": "从牌坊到樱顶",
        "expect_kind": "route",
        "expect_field": "route",
    },
    {
        "name": "2. 模糊目标型需求",
        "query": "饿了想吃饭",
        # LLM 可能先 ask_user 问位置（clarify），也可能直接列候选（candidates），都合理
        "expect_kind": None,
        "expect_field": None,
    },
    {
        "name": "3. 途经点规划",
        "query": "从梅园去教五上课，路上顺便买个笔记本",
        # 途经点找不到时仍应返回 route（基础路线），或 chat 但有替代建议
        "expect_kind": None,
        "expect_field": None,
    },
    {
        "name": "4. 游览推荐",
        "query": "第一次来武大，帮我逛遍全校",
        "expect_kind": "route",
        "expect_field": "route",
    },
    {
        "name": "5. 闲聊",
        "query": "今天天气怎么样",
        "expect_kind": "chat",
        "expect_field": "message",
    },
    {
        "name": "6. 追问澄清",
        "query": "去那个有花的地方",
        "expect_kind": None,  # 可能是 clarify、candidates 或 route，都算合理
        "expect_field": None,
    },
]


def main():
    passed = 0
    failed = 0
    for s in SCENARIOS:
        print(f"\n{'='*60}")
        print(f"场景 {s['name']}")
        print(f"Query: {s['query']}")
        try:
            t0 = time.monotonic()
            r = run_agent(s["query"])
            elapsed = time.monotonic() - t0
            print(f"  response_kind: {r['response_kind']}")
            print(f"  turns: {r['turns']}")
            print(f"  time: {elapsed:.1f}s")
            msg = r.get("message") or ""
            print(f"  message: {msg[:120]}{'...' if len(msg) > 120 else ''}")
            if r.get("route"):
                rt = r["route"]
                print(f"  route_kind: {r.get('route_kind')}")
                print(f"  distance: {rt.get('distance_m') or rt.get('recommended_length_m')}m")
                print(f"  path points: {len(rt.get('recommended', []))}")
            if r.get("candidates"):
                cands = r["candidates"]
                print(f"  candidates: {len(cands)} 个")
                for c in cands[:3]:
                    print(f"    - {c.get('name')} ({c.get('subcategory', '?')})")
            if r.get("clarify"):
                print(f"  clarify: {r['clarify']}")

            # 验证
            if s["expect_kind"] and r["response_kind"] != s["expect_kind"]:
                # 场景 2 模糊目标可能 LLM 直接帮选并规划路线，也算合理
                if s["name"].startswith("2.") and r["response_kind"] == "route":
                    print("  [INFO] LLM 直接选了一个并规划路线，也算合理")
                    passed += 1
                    continue
                print(f"  [FAIL] 期望 {s['expect_kind']}，实际 {r['response_kind']}")
                failed += 1
            else:
                print("  [PASS]")
                passed += 1
        except PlannerError as e:
            print(f"  [FAIL] PlannerError: {e}")
            failed += 1
        except Exception as e:
            print(f"  [FAIL] {type(e).__name__}: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"结果: {passed} passed, {failed} failed / {len(SCENARIOS)} total")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
