"""Exploratory C1 ablation: identical dialogue history with/without route state.

This uses the same /api/chat endpoint for both target arms. It isolates the
explicit saved route state from a transcript-only continuation for the 20
route-followup cases in the locked C1 pilot. Results remain development data.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Any

import requests

from experiments.c1_live_compare import (
    DEFAULT_DATASET, ROOT, compare_cases, project_run, score_target,
)


CASE_IDS = tuple(f"c1_{index:02d}_" for index in range(1, 21))


def contexts_for_target(history: list[dict], route_state: dict) -> tuple[dict, dict]:
    """Return independent copies so both arms see exactly the same transcript."""
    baseline = {"history": deepcopy(history)}
    structured = {"history": deepcopy(history),
                  "previous_route_state": deepcopy(route_state)}
    return structured, baseline


def _post_chat(session: requests.Session, base: str, query: str,
               *, context: dict | None = None, travel_mode: str = "walk") -> tuple[dict, float]:
    started = time.perf_counter()
    response = session.post(
        f"{base}/api/chat",
        json={"query": query, "travel_mode": travel_mode,
              **({"context": context} if context is not None else {})},
        timeout=70,
    )
    response.raise_for_status()
    payload = response.json()["data"]
    if not isinstance(payload, dict):
        raise ValueError("chat response data is not an object")
    return payload, round(time.perf_counter() - started, 3)


def _assistant_text(result: dict) -> str:
    return str(result.get("explanation") or result.get("message")
               or result.get("reply") or "已回答。")[:500]


def _prepare(session: requests.Session, base: str, case: dict) -> tuple[list[dict], dict, list[dict]]:
    history = []
    prior_route = None
    setup_observed = []
    for turn in case["setup"]:
        result, elapsed = _post_chat(
            session, base, turn["query"],
            travel_mode=turn.get("travel_mode", "walk"),
        )
        route = result.get("route_state")
        if isinstance(route, dict):
            prior_route = route
        observed = project_run({"status": "completed", "result": result})
        setup_observed.append({"query": turn["query"], "observed": observed,
                               "elapsed_s": elapsed})
        history.extend([{"role": "user", "content": turn["query"]},
                        {"role": "assistant", "content": _assistant_text(result)}])
    if prior_route is None:
        raise ValueError("setup did not produce a route state")
    return history, prior_route, setup_observed


def run_ablation(dataset: dict, base: str) -> list[dict]:
    cases = [case for case in dataset["cases"]
             if case["id"].startswith(CASE_IDS)]
    if len(cases) != 20:
        raise ValueError("expected exactly 20 frozen route-followup cases")
    rows = []
    with requests.Session() as session:
        for index, case in enumerate(cases):
            history, prior_route, setup_observed = _prepare(session, base, case)
            structured_context, history_context = contexts_for_target(
                history, prior_route,
            )
            query = case["target"]["query"]
            mode = case["target"].get("travel_mode", "walk")
            if index % 2:
                history_result, history_elapsed = _post_chat(
                    session, base, query, context=history_context, travel_mode=mode,
                )
                structured_result, structured_elapsed = _post_chat(
                    session, base, query, context=structured_context, travel_mode=mode,
                )
            else:
                structured_result, structured_elapsed = _post_chat(
                    session, base, query, context=structured_context, travel_mode=mode,
                )
                history_result, history_elapsed = _post_chat(
                    session, base, query, context=history_context, travel_mode=mode,
                )
            structured = project_run({"status": "completed", "result": structured_result})
            history_only = project_run({"status": "completed", "result": history_result})
            row = {
                "id": case["id"], "expected": case["expected"],
                "setup": setup_observed,
                "structured_observed": structured,
                "history_only_observed": history_only,
                "structured": score_target(case["expected"], structured),
                "history_only": score_target(case["expected"], history_only),
                "structured_elapsed_s": structured_elapsed,
                "history_only_elapsed_s": history_elapsed,
            }
            rows.append(row)
            print(json.dumps({"id": row["id"], "structured": row["structured"],
                              "history_only": row["history_only"]},
                             ensure_ascii=False), flush=True)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--base-url", default="https://whuspati.online")
    parser.add_argument("--server-commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.base_url.startswith("https://"):
        parser.error("live ablation requires an https URL")
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    rows = run_ablation(dataset, args.base_url.rstrip("/"))
    summary = compare_cases([
        {"full": row["structured"], "stateless": row["history_only"],
         "full_elapsed_s": row["structured_elapsed_s"]}
        for row in rows
    ])
    output = {
        "status": "exploratory_history_ablation",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "server_commit_declared": args.server_commit,
        "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "scope": "20 locked development route-followup tasks; same history in both arms",
        "summary": summary, "cases": rows,
        "limitations": [
            "Both arms use /api/chat rather than the durable conversation worker.",
            "The setup route is produced once and shared; target arms are independently evaluated.",
            "This is a development-set ablation, not independent paper evidence.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"report={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
