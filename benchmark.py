#!/usr/bin/env python3
"""A small CLI-only benchmark for an extremely important dinner decision."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import random
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from cli_runners import DEFAULT_MODELS, find_cli, run_model

RUBRIC = {
    "name": "craving-closeness-v1",
    "restaurant_only": {"exact": 100, "hot_chicken": 60, "chicken": 40, "rice_bowl": 20, "other": 0},
    "with_item": {"exact_restaurant": 70, "exact_item": 30, "partial_item": 15,
                  "wrong_restaurant_hot_chicken": 45, "wrong_restaurant_chicken": 30,
                  "wrong_restaurant_rice_bowl": 15, "wrong_restaurant_other": 0},
    "note": "One top-1 prediction per model. Closeness points, not accuracy percentages. No reward for rationale length.",
}


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def parse_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Model response was not a single JSON object") from exc
    if not isinstance(result, dict):
        raise ValueError("Model response must be an object")
    return result


def normalize(name):
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def exact_restaurant(prediction, target):
    allowed = [target["restaurant"], *target.get("restaurant_aliases", [])]
    return normalize(prediction["restaurant"]) in {normalize(x) for x in allowed}


def score(prediction, target, assessment):
    category = assessment["category"]
    if category not in ("hot_chicken", "chicken", "rice_bowl", "other"):
        raise ValueError("Unknown evaluator category")
    exact = exact_restaurant(prediction, target)
    if not target.get("items"):
        return RUBRIC["restaurant_only"]["exact" if exact else category]
    if not exact:
        return RUBRIC["with_item"]["wrong_restaurant_" + category]
    match = assessment["item_match"]
    if match not in ("exact", "partial", "none"):
        raise ValueError("Item target requires a valid item match")
    return 70 + {"exact": 30, "partial": 15, "none": 0}[match]


def predictor_prompt(brief):
    return (
        "Predict what this person is craving right now from their past food orders. "
        "This is a silly dinner benchmark. Use only the supplied evidence. No tools, "
        "files, browsing, questions or ordering. Pick ONE restaurant and meal. "
        'Return only JSON: {"restaurant":"...","items":["..."],"reason":"20 words max"}.\n'
        + brief
    )


def validate_prediction(data):
    if not isinstance(data.get("restaurant"), str) or not data["restaurant"].strip():
        raise ValueError("Missing restaurant")
    if not isinstance(data.get("items"), list) or not all(isinstance(x, str) for x in data["items"]):
        raise ValueError("Items must be a list of names")
    if not isinstance(data.get("reason"), str):
        raise ValueError("Missing reason")
    return {k: data[k] for k in ("restaurant", "items", "reason")}


def sanitize_target(data):
    if not isinstance(data, dict) or not isinstance(data.get("restaurant"), str) or not data["restaurant"].strip():
        raise ValueError("Target requires a restaurant name")
    clean = {"restaurant": data["restaurant"]}
    for field in ("items", "restaurant_aliases"):
        value = data.get(field, [])
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise ValueError(f"Target {field} must be a list of names")
        if field in data:
            clean[field] = value
    return clean


def require_result(result, stage):
    if result["status"] != "ok":
        raise RuntimeError(f"{stage} failed: {result.get('error', result['status'])}")
    return parse_json(result["text"])


def run(args):
    out = Path(args.out)
    if (out / "run.json").exists() or (out / "manifest.json").exists():
        raise ValueError("Use a fresh output directory; existing runs are never overwritten")
    out.mkdir(parents=True, exist_ok=True)
    history = json.loads(Path(args.history).read_text())
    # Re-allowlist even supplied fixtures. Personal IDs and timestamps never reach models.
    orders = []
    for index, order in enumerate(history["orders"]):
        if not isinstance(order.get("restaurant"), str) or not isinstance(order.get("items"), list):
            raise ValueError("Invalid order history")
        if not all(isinstance(x, str) for x in order["items"]):
            raise ValueError("Invalid history item")
        orders.append({"sequence": index + 1, "restaurant": order["restaurant"], "items": order["items"]})
    if not orders:
        raise ValueError("History is empty")
    target = sanitize_target(json.loads(Path(args.target).read_text()))
    selected = [m for m in DEFAULT_MODELS if m.id in args.models.split(",")]
    if len(selected) != len(set(args.models.split(","))):
        raise ValueError("Unknown or repeated model ID")
    by_id = {m.id: m for m in DEFAULT_MODELS}
    write_json(out / "rubric.json", RUBRIC)
    versions = {}
    for cli in sorted({m.cli for m in selected}):
        executable = find_cli(cli)
        if executable:
            try:
                version = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10)
                versions[cli] = version.stdout.strip()[:160] if version.returncode == 0 else "unknown"
            except (OSError, subprocess.TimeoutExpired):
                versions[cli] = "unknown"
        else:
            versions[cli] = "not installed"
    manifest = {"started_at": datetime.now(timezone.utc).isoformat(), "target_sha256": digest(target),
                "rubric_sha256": digest(RUBRIC), "history_sha256": digest(orders),
                "models": [vars(m) for m in selected], "setup_model": args.setup_model,
                "judge_model": args.judge_model, "trials_per_model": 1,
                "transport": "installed CLIs; no direct API calls", "timeout_seconds": args.timeout,
                "cli_versions": versions}
    write_json(out / "manifest.json", manifest)
    print("SETUP: building brief without the hidden target", flush=True)
    setup_prompt = (
        "You are the setup model for a playful food craving benchmark. No tools. "
        "Summarize ONLY evidence in this history, without claiming to know today's craving. "
        "Mention repeat restaurants and meals; don't recommend a winner. Do not invent times, "
        "preferences, restrictions or occasions. Return ONLY JSON "
        '{"pattern_summary":"under 100 words","rubric_note":"under 30 words explaining the fixed scoring"}.\n'
        + json.dumps({"history_newest_first": orders, "fixed_rubric": RUBRIC}, ensure_ascii=False)
    )
    setup_result = run_model(by_id[args.setup_model], setup_prompt, timeout=args.timeout)
    write_json(out / "setup.json", setup_result)
    setup = require_result(setup_result, "Setup")
    if not isinstance(setup.get("pattern_summary"), str):
        raise ValueError("Setup did not produce a summary")
    # Include the original allowlisted evidence so setup omissions cannot rewrite history.
    brief = "# What am I craving right now?\n\n" + setup["pattern_summary"] + "\n\n"
    brief += "Orders, newest first (relative sequence only):\n"
    for o in orders:
        brief += f"{o['sequence']}. {o['restaurant']}: {'; '.join(o['items'])}\n"
    (out / "brief.md").write_text(brief)
    prompt = predictor_prompt(brief)
    (out / "contestant-prompt.txt").write_text(prompt)
    manifest["brief_sha256"] = hashlib.sha256(brief.encode()).hexdigest()
    manifest["prompt_sha256"] = hashlib.sha256(prompt.encode()).hexdigest()
    write_json(out / "manifest.json", manifest)
    print(f"PREDICT: {len(selected)} models, one short answer each", flush=True)
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(run_model, m, prompt, timeout=args.timeout): m for m in selected}
        for future in concurrent.futures.as_completed(futures):
            m = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"id": m.id, "status": "error", "error": type(exc).__name__, "model": m.model, "cli": m.cli}
            result["label"] = m.label
            if result["status"] == "ok":
                try:
                    result["prediction"] = validate_prediction(parse_json(result["text"]))
                except ValueError as exc:
                    result["status"] = "invalid_output"
                    result["error"] = str(exc)
            result["score"] = None
            results.append(result)
            write_json(out / "predictions" / (m.id + ".json"), result)
            print(f"  {m.label}: {result['status']}", flush=True)
    good = [r for r in results if r["status"] == "ok"]
    # Frequency tie-break: choose the restaurant seen most recently.
    counts = Counter(o["restaurant"] for o in orders)
    frequent = max(orders, key=lambda o: (counts[o["restaurant"]], -o["sequence"]))
    baselines = []
    for name, order in (("Most recent order", orders[0]), ("Order-frequency baseline", frequent)):
        baselines.append({"id": "baseline_" + str(len(baselines)), "label": name,
                          "baseline": True, "status": "ok", "score": None,
                          "prediction": {"restaurant": order["restaurant"], "items": order["items"],
                                         "reason": "Count orders; tie-break by recency."}})
    random.Random(20261003).shuffle(good)
    anonymous = good + baselines
    random.Random(103).shuffle(anonymous)
    blind = {f"C{i+1}": r for i, r in enumerate(anonymous)}
    judge_result = None
    if anonymous:
        judge_prompt = (
            "You evaluate anonymous dinner predictions in a joke benchmark. No tools. "
            "Use the fixed rubric; do not infer model identities. Category describes the guessed meal: "
            "Classify a rice/protein bowl as rice_bowl first, even if it contains chicken. "
            "Otherwise hot_chicken=Nashville/spicy fried chicken; chicken=other chicken; "
            "other=anything else. Classify item_match exact/partial/none when target items exist, "
            "otherwise not_scored. Don't invent a target item. A partial item is the same main "
            "protein/form but different sides/quantity. Return ONLY JSON "
            '{"assessments":[{"id":"C1","category":"hot_chicken|chicken|rice_bowl|other",'
            '"item_match":"exact|partial|none|not_scored","roast":"under 15 words"}]}\n'
            + json.dumps({"target": target, "rubric": RUBRIC,
                          "predictions": [{"id": k, "prediction": v["prediction"]} for k, v in blind.items()]}, ensure_ascii=False)
        )
        (out / "evaluator-prompt.txt").write_text(judge_prompt)
        print("EVALUATE: one blinded batch", flush=True)
        judge_result = run_model(by_id[args.judge_model], judge_prompt, timeout=args.timeout)
        write_json(out / "evaluator.json", judge_result)
        try:
            judge = require_result(judge_result, "Evaluator")
            assessments = judge["assessments"]
            if not isinstance(assessments, list) or len(assessments) != len(blind):
                raise ValueError("Evaluator omitted predictions")
            ids = [a["id"] for a in assessments]
            if set(ids) != set(blind) or len(set(ids)) != len(ids):
                raise ValueError("Evaluator returned unknown/duplicate IDs")
            computed = [(a, score(blind[a["id"]]["prediction"], target, a)) for a in assessments]
            for a, points in computed:
                r = blind[a["id"]]
                r.update({"score": points, "assessment": a,
                          "exact_restaurant": exact_restaurant(r["prediction"], target)})
        except (ValueError, RuntimeError, KeyError, TypeError) as exc:
            print(f"Evaluator error: {exc}", flush=True)
            judge_result["evaluation_error"] = str(exc)
    results.sort(key=lambda r: (-r["score"] if r["score"] is not None else 1, r["label"]))
    report = {"manifest": manifest, "target": target, "rubric": RUBRIC, "results": results, "baselines": baselines,
              "setup": setup_result, "evaluator": judge_result,
              "caption": "DoorDash Bench. n=1, p=vibes, peer review=my stomach."}
    write_json(out / "target-revealed.json", target)
    write_json(out / "run.json", report)
    from chart import render_chart
    render_chart(report, out)
    print(f"Saved {out}/run.json and chart.png", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", required=True, help="Allowlisted DoorDash CLI history JSON")
    parser.add_argument("--target", required=True, help="JSON with restaurant, optional aliases and items; hidden from contestants")
    parser.add_argument("--out", default="local/run")
    parser.add_argument("--models", default="muse,opus,sonnet,sol,astra")
    parser.add_argument("--setup-model", choices=[m.id for m in DEFAULT_MODELS], default="sonnet")
    parser.add_argument("--judge-model", choices=[m.id for m in DEFAULT_MODELS], default="opus")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--concurrency", type=int, choices=range(1, 6), default=2)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
