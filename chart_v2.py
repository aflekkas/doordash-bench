"""Render the multi-task benchmark as a clean score and efficiency comparison."""
from __future__ import annotations

import argparse
import html
import json
import math
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
INK = "#182230"
MUTED = "#667085"
GRID = "#EAECF0"
COLORS = ["#2563EB", "#7C3AED", "#E07816", "#0D9488", "#D7476A", "#4F46E5", "#475467"]


def number(value):
    """Ignore missing, nonfinite, or negative measurements instead of plotting zero."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def score_text(value):
    value = number(value)
    return "n/a" if value is None else str(Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def cost_text(value):
    value = number(value)
    if value is None:
        return "n/a"
    if value == 0:
        return "$0"
    return f"${value:.4f}" if value < 0.1 else f"${value:.3f}"


class Canvas:
    """Write PNG and real vector SVG from the same drawing commands."""

    def __init__(self, width, height):
        self.image = Image.new("RGB", (width * 2, height * 2), "white")
        self.draw = ImageDraw.Draw(self.image)
        self.svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
                    '<rect width="100%" height="100%" fill="white"/>']
        self.fonts = {}

    def font(self, size, bold=False):
        key = (size, bold)
        if key not in self.fonts:
            font = ImageFont.truetype(str(ROOT / "assets" / "Inter.ttf"), size * 2)
            # Inter's variable axes are optical size and weight.
            font.set_variation_by_axes([14, 650 if bold else 400])
            self.fonts[key] = font
        return self.fonts[key]

    def text(self, x, y, text, size=20, fill=INK, bold=False, anchor="left"):
        text = str(text)
        font = self.font(size, bold)
        text_width = self.draw.textlength(text, font=font) / 2
        left = x - text_width if anchor == "right" else x - text_width / 2 if anchor == "middle" else x
        self.draw.text((left * 2, y * 2), text, font=font, fill=fill, anchor="lt")
        self.svg.append(f'<text x="{left:.2f}" y="{y + size * .82:.2f}" fill="{fill}" font-family="Inter,Arial,sans-serif" font-size="{size}" font-weight="{650 if bold else 400}">{html.escape(text)}</text>')

    def line(self, x1, y1, x2, y2, fill=GRID, width=1, dashed=False):
        if dashed:
            length = math.hypot(x2 - x1, y2 - y1)
            if length:
                for start in range(0, math.ceil(length), 12):
                    end = min(start + 6, length)
                    self.draw.line([(2 * (x1 + (x2 - x1) * start / length), 2 * (y1 + (y2 - y1) * start / length)),
                                    (2 * (x1 + (x2 - x1) * end / length), 2 * (y1 + (y2 - y1) * end / length))], fill=fill, width=width * 2)
        else:
            self.draw.line((x1 * 2, y1 * 2, x2 * 2, y2 * 2), fill=fill, width=width * 2)
        dash = ' stroke-dasharray="6 6"' if dashed else ""
        self.svg.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{fill}" stroke-width="{width}"{dash}/>')

    def rect(self, x, y, width, height, fill, radius=0):
        self.draw.rounded_rectangle((x * 2, y * 2, (x + width) * 2, (y + height) * 2), radius=radius * 2, fill=fill)
        self.svg.append(f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{radius}" fill="{fill}"/>')

    def dot(self, x, y, color, radius=7):
        self.draw.ellipse(((x - radius) * 2, (y - radius) * 2, (x + radius) * 2, (y + radius) * 2), fill=color, outline="white", width=3)
        self.svg.append(f'<circle cx="{x}" cy="{y}" r="{radius}" fill="{color}" stroke="white" stroke-width="1.5"/>')

    def save(self, out):
        self.image.resize((1600, 1000), Image.Resampling.LANCZOS).save(out / "chart.png")
        (out / "chart.svg").write_text("\n".join(self.svg + ["</svg>\n"]))


def render_chart(report, out):
    """One readable scatter: true cost coordinates, probability score, no bar chart."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    canvas = Canvas(1600, 1000)
    rows = [dict(row) for row in report.get("results", [])]
    rows.sort(key=lambda row: -(number(row.get("score")) if number(row.get("score")) is not None else -1))
    for index, row in enumerate(rows):
        row["color"] = COLORS[index % len(COLORS)]
    baselines = report.get("baselines", [])
    baseline = next((row for row in baselines if row.get("id") == "uniform" and number(row.get("score")) is not None), None)
    if baseline is None:
        baseline = max((row for row in baselines if number(row.get("score")) is not None),
                       key=lambda row: number(row.get("score")), default=None)
    all_costs = bool(rows) and all(number(row.get("cost_per_task_usd")) is not None for row in rows)
    x_key = "cost_per_task_usd" if all_costs else "tokens_per_task"
    x_label = "Estimated cost per prediction (USD)" if all_costs else "Tokens per prediction (cost proxy)"
    canvas.text(80, 43, report.get("title", "DoorDash Bench"), 28, MUTED, bold=True)
    canvas.text(80, 97, "Can AI guess my next DoorDash order?", 45, bold=True)
    task_count = report.get("task_count", "?")
    historical_count = sum(task.get("kind") == "historical_next_order" for task in report.get("tasks_revealed", []))
    current_count = sum(task.get("kind") == "current_craving" for task in report.get("tasks_revealed", []))
    if historical_count and current_count == 1:
        subtitle = f"{historical_count} historical orders + today’s craving · {task_count} independent tasks/model"
    else:
        subtitle = f"{task_count} held-out order/craving tasks · independent predictions per model"
    canvas.text(80, 164, subtitle, 21, MUTED)
    canvas.text(80, 235, "Up = better forecast · left = cheaper" if all_costs else "Up = better forecast · left = fewer tokens", 24)

    x0, x1, y0, y1 = 145, 1490, 333, 768
    points = [row for row in rows if number(row.get("score")) is not None and number(row.get(x_key)) is not None]
    visible_scores = [number(row["score"]) for row in points]
    if baseline:
        visible_scores.append(number(baseline["score"]))
    if visible_scores:
        smallest, largest = min(visible_scores), max(visible_scores)
        padding = max(5, (largest - smallest) * .3)
        y_min = max(0, math.floor((smallest - padding) / 5) * 5)
        y_max = min(100, math.ceil((largest + padding) / 5) * 5)
        if y_max <= y_min:
            y_max = min(100, y_min + 10)
            y_min = max(0, y_max - 10)
    else:
        y_min, y_max = 0, 100
    canvas.text(80, 294, "Probability score / 100", 20, MUTED)
    zoom = f"Y-axis zoomed: {y_min:g}–{y_max:g} (full scale: 0–100)" if y_min > 0 or y_max < 100 else "Full score scale: 0–100"
    canvas.text(1490, 294, zoom, 17, MUTED, anchor="right")
    tick_step = 5 if y_max - y_min <= 30 else 10 if y_max - y_min <= 60 else 20
    for tick in range(int(y_min), int(y_max) + 1, tick_step):
        y = y1 - (y1 - y0) * (tick - y_min) / (y_max - y_min)
        canvas.line(x0, y, x1, y)
        canvas.text(x0 - 20, y - 10, tick, 20, MUTED, anchor="right")
    maximum = max((number(row[x_key]) for row in points), default=0)
    if maximum:
        exponent = 10 ** math.floor(math.log10(maximum))
        x_max = math.ceil(maximum * 1.1 / exponent) * exponent
    else:
        x_max = .01 if all_costs else 1000
    for fraction in (0, .25, .5, .75, 1):
        x = x0 + (x1 - x0) * fraction
        value = x_max * fraction
        label = "$0" if value == 0 and all_costs else f"${value:.3f}" if all_costs else f"{value:,.0f}"
        canvas.text(x, y1 + 20, label, 20, MUTED, anchor="middle")
    canvas.text((x0 + x1) / 2, 832, x_label, 25, anchor="middle")
    occupied = []
    if baseline:
        score = number(baseline["score"])
        y = y1 - (y1 - y0) * (score - y_min) / (y_max - y_min)
        canvas.line(x0, y, x1, y, "#98A2B3", width=2, dashed=True)
        label = "Uniform probabilities" if baseline.get("id") == "uniform" else baseline.get("label", "Baseline")
        caption = f"{label}: {score_text(score)}"
        caption_width = canvas.draw.textlength(caption, font=canvas.font(23)) / 2
        canvas.text(x1, y - 35, caption, 23, MUTED, anchor="right")
        occupied.append((x1 - caption_width, y - 37, x1, y - 8))
        occupied.append((x0, y - 2, x1, y + 2))
    positioned = []
    for row in points:
        x = x0 + (x1 - x0) * number(row[x_key]) / x_max
        y = y1 - (y1 - y0) * (number(row["score"]) - y_min) / (y_max - y_min)
        label = row.get("label", row.get("id", "Unknown"))
        # Drop redundant provider prefixes while retaining versioned model names.
        label = label.removeprefix("Claude ")
        label = f"{label} · {score_text(row['score'])}"
        positioned.append(dict(row, x=x, y=y, caption=label))

    def overlaps(a, b, padding=9):
        return a[0] < b[2] + padding and a[2] + padding > b[0] and a[1] < b[3] + padding and a[3] + padding > b[1]

    for row in positioned:
        text_width = canvas.draw.textlength(row["caption"], font=canvas.font(25, True)) / 2
        candidates = []
        for dy in (-48, 20, -86, 58, -124, 96, -162, 134):
            for dx in (18, -text_width - 18):
                left = min(max(row["x"] + dx, x0 + 10), x1 - text_width - 10)
                top = min(max(row["y"] + dy, y0 + 8), y1 - 35)
                box = (left, top, left + text_width, top + 28)
                conflicts = sum(overlaps(box, other) for other in occupied)
                for point in positioned:
                    conflicts += overlaps(box, (point["x"] - 10, point["y"] - 10, point["x"] + 10, point["y"] + 10))
                distance = math.hypot(left - row["x"], top - row["y"])
                candidates.append((conflicts, distance, box))
        _, _, box = min(candidates, key=lambda candidate: (candidate[0], candidate[1]))
        occupied.append(box)
        left, top, right, bottom = box
        near_x = max(left, min(row["x"], right))
        near_y = max(top, min(row["y"], bottom))
        canvas.line(row["x"], row["y"], near_x, near_y, row["color"], width=1)
        canvas.text(left, top, row["caption"], 25, row["color"], bold=True)
    for row in positioned:
        canvas.dot(row["x"], row["y"], row["color"], radius=9)
    missing = len(rows) - len(points)
    if missing:
        canvas.text(80, 882, f"{missing} model(s) lack score or budget and are omitted.", 18, MUTED)
    exact_rows = [row for row in rows if row.get("exact_hits") is not None]
    hit_values = {row["exact_hits"] for row in exact_rows}
    if rows and len(exact_rows) == len(rows) and len(hit_values) == 1:
        hits = next(iter(hit_values))
        exact_note = f"All {len(rows)} models: {hits}/{task_count} exact picks."
    else:
        exact_note = "Exact picks and detailed measurements are in the README."
    canvas.text(80, 925, "Probability scores, not accuracy. " + exact_note, 20, MUTED)
    cost_note = "API-equivalent estimates, not subscription charges. n = 1 person." if all_costs else "Tokens are a cost proxy; unavailable dollar estimates are not $0. n = 1 person."
    canvas.text(80, 963, cost_note, 18, MUTED)
    canvas.text(1520, 963, "github.com/aflekkas/doordash-bench", 17, MUTED, anchor="right")
    canvas.save(out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    render_chart(json.loads(args.run.read_text()), args.out or args.run.parent)
