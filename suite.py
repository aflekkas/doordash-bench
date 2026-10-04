#!/usr/bin/env python3
"""DoorDash Bench v2: independent historical forecasts, probability scores and cost."""
from __future__ import annotations

import argparse
from collections import Counter
import concurrent.futures
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random

from benchmark import digest, normalize, parse_json, require_result, sanitize_target, write_json
from cli_runners import DEFAULT_MODELS, run_model
from costing import DEFAULT_PRICES, apply_estimate, load_prices

SPEC = {
    "version": "doordash-bench-v2",
    "historical_tasks": 3,
    "current_craving_tasks": 1,
    "score": "100 * (1 - multiclass Brier loss / 2), averaged across all four tasks",
    "brier_loss": "sum((probability - one_hot_truth) ** 2) across the fixed meal choices",
    "higher_is_better": True,
    "top1_tie_break": "lexicographically smallest meal ID",
    "task_isolation": "one fresh CLI session per task; no shared history/context or session resume",
    "menu_scope": "static meal choices from the history; known-menu prediction, not discovery of unseen restaurants",
    "normalization": "reject probability sums more than 0.005 from 1; otherwise normalize rounding error",
    "cost_basis": "Estimated API-equivalent prediction cost: CLI estimates where reported, dated reference rates otherwise; not invoices. Shared setup/evaluation overhead excluded.",
}


def order_key(order):
    return (normalize(order["restaurant"]), tuple(sorted(normalize(x) for x in order["items"])))


def make_tasks(history, target):
    """Newest-first input. Each historical case sees strictly older evidence."""
    orders = []
    for i, raw in enumerate(history["orders"]):
        clean = sanitize_target(raw)
        if not clean.get("items"):
            raise ValueError("Each historical order requires item names")
        orders.append({"sequence": i + 1, "restaurant": clean["restaurant"], "items": clean["items"]})
    if len(orders) < 4:
        raise ValueError("Need at least four orders for three historical holdouts")
    # IDs must not encode recency or holdout answers. Canonical menu order is independent of chronology.
    unique = {order_key(order): order for order in orders}
    candidates = []
    lookup = {}
    for key in sorted(unique):
        order = unique[key]
        meal_id = f"M{len(candidates)+1:02d}"
        lookup[key] = meal_id
        candidates.append({"id": meal_id, "restaurant": order["restaurant"], "items": order["items"]})
    if len(candidates) < 2:
        raise ValueError("Need at least two distinct meals")
    target = sanitize_target(target)
    if order_key(target) not in lookup:
        raise ValueError("Current craving must match a historical meal choice; keep the menu independent of today's answer")
    tasks = []
    # Earliest of the three holdouts first; each gets only orders older than itself.
    for index in (2, 1, 0):
        visible = [{**o, "sequence": j + 1} for j, o in enumerate(orders[index+1:])]
        tasks.append({"id": f"H{len(tasks)+1}", "kind": "historical_next_order", "history": visible,
                      "answer": lookup[order_key(orders[index])]})
    tasks.append({"id": "NOW", "kind": "current_craving", "history": orders,
                  "answer": lookup[order_key(target)]})
    return orders, candidates, tasks, target


def validate_probabilities(data, candidates):
    probs = data.get("probabilities")
    ids = {c["id"] for c in candidates}
    if not isinstance(probs, dict) or set(probs) != ids:
        raise ValueError("Provide exactly one probability for every meal ID")
    if any(isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 1 for p in probs.values()):
        raise ValueError("Probabilities must be finite numbers from 0 to 1")
    total = sum(probs.values())
    if abs(total - 1) > 0.005:
        raise ValueError("Probabilities must sum to 1 (rounding tolerance 0.005)")
    return {k: probs[k] / total for k in sorted(probs)}


def brier_score(probs, answer):
    if answer not in probs:
        raise ValueError("Answer missing from probability distribution")
    return 100 * (1 - sum((p - (1 if k == answer else 0)) ** 2 for k, p in probs.items()) / 2)


def top_choice(probs):
    return min(probs, key=lambda k: (-probs[k], k))


def make_prompt(task, candidates, guide):
    menu = list(candidates)
    random.Random(task["id"]).shuffle(menu)
    payload = {"history_newest_first": task["history"], "meal_choices": menu}
    # Neither task ID/type nor ground truth is sent. No future histories in this session.
    return (
        "Forecast this person's next food order from ONLY their past orders and the known meal menu. "
        "No current craving clues are available. No tools, files, memory, questions or ordering. "
        "Estimate probabilities for ALL meal IDs; numbers 0..1 summing to1. "
        'Return ONLY JSON {"probabilities":{"M01":0.2,"M02":0.3,...}}; no rationale.\n'
        + guide + "\n" + json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    )


def baselines(tasks, candidates):
    ids = [c["id"] for c in candidates]
    lookup = {order_key(c): c["id"] for c in candidates}
    rows = []
    for kind, label in (("uniform", "Uniform chance"), ("frequency", "Order frequency"), ("recent", "Repeat latest")):
        outcomes = []
        for task in tasks:
            counts = Counter(lookup[order_key(o)] for o in task["history"])
            if kind == "uniform":
                probs = {k: 1/len(ids) for k in ids}
            elif kind == "frequency":
                # Fixed Jeffreys-style smoothing gives unobserved menu choices a chance.
                denom = sum(counts.values()) + 0.5 * len(ids)
                probs = {k: (counts[k] + 0.5) / denom for k in ids}
            else:
                recent = lookup[order_key(task["history"][0])]
                probs = {k: float(k == recent) for k in ids}
            outcomes.append({"task_id": task["id"], "probabilities": probs,
                             "score": brier_score(probs, task["answer"]),
                             "top_choice": top_choice(probs), "correct": top_choice(probs) == task["answer"]})
        rows.append({"id": kind, "label": label, "baseline": True, "status": "ok",
                     "score": sum(x["score"] for x in outcomes)/len(tasks),
                     "exact_hits": sum(x["correct"] for x in outcomes), "task_count": len(tasks),
                     "cost_per_task_usd": 0.0, "tokens_per_task": 0.0, "tasks": outcomes})
    return rows


def aggregate(spec, rows, task_count):
    complete = len(rows) == task_count and all(r["status"] == "ok" for r in rows)
    def mean_field(name):
        values = [r.get(name) for r in rows]
        return sum(values)/task_count if len(values) == task_count and all(v is not None for v in values) else None
    return {"id": spec.id, "label": spec.label, "model": spec.model, "cli": spec.cli,
            "status": "ok" if complete else "incomplete", "task_count": task_count,
            "score": sum(r["score"] for r in rows)/task_count if complete else None,
            "exact_hits": sum(r.get("correct", False) for r in rows),
            "cost_per_task_usd": mean_field("estimated_cost_usd"), "cost_sources": sorted({r.get("estimated_cost_source", "unreported") for r in rows}),
            "tokens_per_task": mean_field("tokens_total"),
            "latency_per_task_seconds": mean_field("latency_seconds"), "tasks": sorted(rows, key=lambda r: r["task_id"])}


def run(args):
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        raise ValueError("Use a fresh output directory; published/previous runs are never overwritten")
    out.mkdir(parents=True, exist_ok=True)
    history = json.loads(Path(args.history).read_text())
    prices = load_prices(args.prices)
    orders, candidates, tasks, target = make_tasks(history, json.loads(Path(args.target).read_text()))
    selected_ids = args.models.split(",")
    selected = [m for m in DEFAULT_MODELS if m.id in selected_ids]
    if len(selected) != len(selected_ids) or len(set(selected_ids)) != len(selected_ids):
        raise ValueError("Unknown or repeated model ID")
    by_id = {m.id: m for m in DEFAULT_MODELS}
    manifest = {"started_at": datetime.now(timezone.utc).isoformat(), "spec": SPEC,
                "spec_sha256": digest(SPEC), "history_sha256": digest(orders),
                "answer_commitment_sha256": digest([{k: t[k] for k in ("id", "answer")} for t in tasks]),
                "candidate_sha256": digest(candidates), "models": [vars(m) for m in selected],
                "task_count": len(tasks), "setup_model": args.setup_model, "judge_model": args.judge_model,
                "trials_per_task": 1, "prediction_calls_planned": len(selected)*len(tasks),
                "reference_prices": prices, "reference_prices_sha256": digest(prices)}
    write_json(out / "manifest.json", manifest)
    write_json(out / "candidates.json", candidates)
    print("SETUP: benchmark guide (no history or hidden answers)", flush=True)
    setup_prompt = (
        "You are the setup model for DoorDash Bench. No tools. Write a short task guide based ONLY on "
        "this fixed specification. Explain estimating next-order probabilities from history, "
        "without picking a restaurant or making claims about the user's preferences. "
        'Return ONLY JSON {"guide":"under 70 words","score_explanation":"under 40 words"}.\n'
        + json.dumps(SPEC)
    )
    setup_result = run_model(by_id[args.setup_model], setup_prompt, timeout=args.timeout)
    write_json(out / "setup.json", setup_result)
    setup = require_result(setup_result, "Setup")
    guide = setup.get("guide")
    if not isinstance(guide, str) or not isinstance(setup.get("score_explanation"), str):
        raise ValueError("Setup guide invalid")
    (out / "brief.md").write_text("# DoorDash Bench v2\n\n" + guide + "\n\n" + setup["score_explanation"] + "\n")
    prompts = {t["id"]: make_prompt(t, candidates, guide) for t in tasks}
    manifest["prompt_sha256"] = {k: hashlib.sha256(v.encode()).hexdigest() for k, v in prompts.items()}
    for key, prompt in prompts.items():
        (out / "prompts").mkdir(exist_ok=True)
        (out / "prompts" / f"{key}.txt").write_text(prompt)
    write_json(out / "manifest.json", manifest)
    rows = {m.id: [] for m in selected}
    print(f"PREDICT: {len(selected)} models x {len(tasks)} independent tasks", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(run_model, m, prompts[t["id"]], timeout=args.timeout): (m, t)
                   for m in selected for t in tasks}
        for future in concurrent.futures.as_completed(futures):
            m, task = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                row = {"id": m.id, "status": "error", "error": type(exc).__name__}
            row["task_id"] = task["id"]
            apply_estimate(row, prices)
            row["score"] = None
            if row["status"] == "ok":
                try:
                    probs = validate_probabilities(parse_json(row["text"]), candidates)
                    row.update(probabilities=probs, score=brier_score(probs, task["answer"]),
                               top_choice=top_choice(probs), correct=top_choice(probs) == task["answer"])
                except (ValueError, TypeError) as exc:
                    row.update(status="invalid_output", error=str(exc))
            rows[m.id].append(row)
            write_json(out / "predictions" / m.id / f"{task['id']}.json", row)
            print(f"  {m.label} / {task['id']}: {row['status']}", flush=True)
    results = [aggregate(m, rows[m.id], len(tasks)) for m in selected]
    results.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0), r["label"]))
    baseline_rows = baselines(tasks, candidates)
    # Deterministic scoring is already complete. Judge audits exact hits without model names.
    anonymous = [r for r in results if r["status"] == "ok"]
    random.Random(731).shuffle(anonymous)
    mapping = {f"A{i+1}": r for i, r in enumerate(anonymous)}
    judge = None
    if mapping:
        judge_prompt = (
            "You are the anonymous evaluator for DoorDash Bench. No tools. The code computes probability "
            "scores; independently count exact top-1 hits using these revealed correct meal IDs. "
            "Do not infer model identity. Return ONLY JSON "
            '{"audits":[{"id":"A1","exact_hits":0,"roast":"under 12 words"}]}.\n'
            + json.dumps({"answers": {t["id"]: t["answer"] for t in tasks},
                          "guesses": [{"id": k, "top_choices": {x["task_id"]: x["top_choice"] for x in r["tasks"]}}
                                      for k, r in mapping.items()]}, separators=(",", ":"))
        )
        (out / "evaluator-prompt.txt").write_text(judge_prompt)
        print("EVALUATE: blinded audit; deterministic probability scores", flush=True)
        judge = run_model(by_id[args.judge_model], judge_prompt, timeout=args.timeout)
        write_json(out / "evaluator.json", judge)
        try:
            audits = require_result(judge, "Evaluator")["audits"]
            if not isinstance(audits, list) or len(audits) != len(mapping) or {a["id"] for a in audits} != set(mapping):
                raise ValueError("Evaluator IDs do not match predictions")
            for audit in audits:
                r = mapping[audit["id"]]
                if audit["exact_hits"] != r["exact_hits"]:
                    raise ValueError("Evaluator's hit count disagrees with deterministic score")
                r["audit"] = audit
            judge["audit_verified"] = True
        except (ValueError, RuntimeError, KeyError, TypeError) as exc:
            judge["audit_verified"] = False
            judge["audit_error"] = str(exc)
        write_json(out / "evaluator.json", judge)
    report = {"title": "DoorDash Bench", "version": SPEC["version"], "task_count": len(tasks),
              "manifest": manifest, "candidates": candidates, "tasks_revealed": tasks,
              "target": target, "results": results, "baselines": baseline_rows,
              "setup": setup_result, "evaluator": judge, "cost_basis": SPEC["cost_basis"],
              "score_basis": SPEC["score"]}
    report["shared_overhead"] = {
        "setup_tokens": setup_result.get("tokens_total"), "evaluator_tokens": judge.get("tokens_total") if judge else None,
        "setup_cost_usd": setup_result.get("cost_usd"), "evaluator_cost_usd": judge.get("cost_usd") if judge else None,
    }
    write_json(out / "run.json", report)
    from chart_v2 import render_chart
    render_chart(report, out)
    print(f"Saved {out}/run.json and chart.png", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--out", default="local/suite")
    parser.add_argument("--models", default="muse,opus,sonnet,sol,astra")
    parser.add_argument("--setup-model", choices=[m.id for m in DEFAULT_MODELS], default="sonnet")
    parser.add_argument("--judge-model", choices=[m.id for m in DEFAULT_MODELS], default="opus")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--concurrency", type=int, choices=range(1, 6), default=2)
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES, help="Dated offline reference rates for CLIs without reported dollar estimates")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
