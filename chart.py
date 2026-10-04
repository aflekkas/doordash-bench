"""Reproducible hand-drawn chart, rendered from actual run.json numbers."""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
INK = "#222222"
PAPER = "#fffdf7"


def render_chart(report, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    scale = 2
    width, height = 1400, 1020
    im = Image.new("RGB", (width * scale, height * scale), PAPER)
    draw = ImageDraw.Draw(im)
    rng = random.Random(7)
    fontpath = ROOT / "assets" / "Caveat.ttf"

    def font(size):
        return ImageFont.truetype(str(fontpath), int(size * scale))

    def text(x, y, value, size=30, fill=INK):
        draw.text((x * scale, y * scale), str(value), font=font(size), fill=fill)

    def line(points, fill=INK, weight=2, sketch=True):
        for _ in range(2 if sketch else 1):
            jitter = 1.2 if sketch else 0
            pts = [(int((x + rng.uniform(-jitter, jitter)) * scale),
                    int((y + rng.uniform(-jitter, jitter)) * scale)) for x, y in points]
            draw.line(pts, fill=fill, width=weight * scale, joint="curve")

    text(56, 28, "DoorDash Bench", 70)
    text(58, 113, "Which frontier model knows what I want for dinner?", 34)
    target = report["target"]
    target_label = "; ".join(target.get("items", [])) or target["restaurant"]
    text(58, 164, "Ground truth: " + target_label[:92], 30, "#55534c")
    text(1035, 60, "n = 1", 44)
    text(1035, 112, "p = vibes", 35)
    line([(1010, 55), (1260, 39), (1290, 159), (1000, 171), (1010, 55)], fill="#f56545")
    x0, x1 = 425, 1125
    top, bottom = 251, 739
    text(1215, top-39, "CLI time", 24, "#676056")
    for tick in (0, 25, 50, 75, 100):
        x = x0 + (x1-x0) * tick / 100
        line([(x, top-15), (x, bottom)], "#ddd8cb", 1)
        text(x-13, bottom+12, tick, 25, "#69645b")
    text(673, bottom+48, "craving closeness / 100", 29)
    rows = report["results"] + report.get("baselines", [])
    palette = ["#ffb899", "#b9d8cf", "#c6cff0", "#e5c7eb", "#f7dc93"]
    row_step = min(83, 475 / max(len(rows), 1))
    for i, row in enumerate(rows):
        y = top + i * row_step
        is_baseline = row.get("baseline", False)
        text(57, y-3, row["label"], 34 if not is_baseline else 29)
        prediction = row.get("prediction", {})
        detail = prediction.get("restaurant", row.get("status", "not run"))
        text(59, y+35, detail[:58], 23, "#676056")
        score = row.get("score")
        if score is None:
            reason = row.get("status", "error")
            if reason == "ok":
                reason = "evaluator failed"
            text(x0+12, y+2, "NO SCORE - " + reason, 30, "#a54d44")
        else:
            end = x0 + (x1-x0) * score / 100
            if score > 0:
                rect = [(x0, y+5), (end, y+3), (end+1, y+42), (x0-1, y+44), (x0, y+5)]
                draw.polygon([(x*scale, yy*scale) for x, yy in rect], fill="#e5e1d7" if is_baseline else palette[i % len(palette)])
                line(rect)
                for hatch in range(int(x0+12), int(end)-5, 23):
                    line([(hatch, y+38), (min(hatch+13, end-2), y+10)], fill="#ffffff", weight=1, sketch=False)
            text(end+12, y-3, str(score), 35)
        if not is_baseline and row.get("latency_seconds") is not None:
            text(1235, y+2, f"{row['latency_seconds']:.1f}s", 26, "#676056")
    line([(x0, top-13), (x0, bottom)], INK, 2)
    scored = [r for r in report["results"] if r.get("score") is not None]
    if scored:
        best = max(r["score"] for r in scored)
        winners = [r["label"] for r in scored if r["score"] == best]
        line([(60, 841), (1340, 836)], "#d9d2c5", 1)
        if len(winners) > 1:
            baseline_best = max((r.get("score") or 0 for r in report.get("baselines", [])), default=-1)
            suffix = " (also tied with counting orders)" if best == baseline_best else ""
            winner_text = f"SOTA* : {len(winners)}-way tie at {best}/100" + suffix
        else:
            winner_text = "SOTA* : " + winners[0] + f" - {best}/100"
        text(58, 855, winner_text, 38)
    else:
        text(58, 855, "SOTA* : pending a valid scored run", 38)
    text(59, 908, "*state of the art at guessing this one guy's dinner. One shot per model.", 27, "#676056")
    text(59, 947, "github.com/aflekkas/doordash-bench  /  peer review: my stomach", 26, "#676056")
    im.save(out / "chart.png")
    # PNG is portable and fonts are baked in; SVG keeps a scalable wrapper for sharing.
    import base64
    png = base64.b64encode((out / "chart.png").read_bytes()).decode()
    (out / "chart.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="1020" viewBox="0 0 1400 1020">'
        f'<image width="1400" height="1020" href="data:image/png;base64,{png}"/></svg>\n'
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    render_chart(json.loads(args.run.read_text()), args.out or args.run.parent)
