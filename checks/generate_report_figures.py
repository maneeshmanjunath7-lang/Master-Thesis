#!/usr/bin/env python3
"""Regenerate the three compact figures used by Chapters 3 and 6.

Uses only Pillow, pandas, and NumPy so it can run in the bundled workspace
runtime without a plotting-framework dependency.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
FIG = ROOT / "figures"
BLUE = "#0065BD"
ORANGE = "#E37222"
GREEN = "#2E8B57"
RED = "#C9302C"
GREY = "#5A6673"
GRID = "#D9E1E8"
INK = "#17365D"
PRIORITY = {"Critical": RED, "High": ORANGE, "Medium": BLUE, "Low": GREEN}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "arialbd.ttf" if bold else "arial.ttf"
    path = Path("C:/Windows/Fonts") / name
    return ImageFont.truetype(str(path), size=size)


def save(img: Image.Image, name: str) -> None:
    img.save(FIG / name, dpi=(180, 180), optimize=True)


def map_figure(tasks: pd.DataFrame) -> None:
    img = Image.new("RGB", (1800, 860), "white")
    draw = ImageDraw.Draw(img)
    draw.text((80, 30), "Final wildfire task centroids", font=font(42, True), fill=INK)
    panels = [("California", (80, 120, 860, 720)), ("India", (940, 120, 1720, 720))]
    for region, (x0, y0, x1, y1) in panels:
        sub = tasks[tasks.region == region].copy()
        lon = sub.centroid_lon.astype(float).to_numpy()
        lat = sub.centroid_lat.astype(float).to_numpy()
        padx = max((lon.max() - lon.min()) * 0.08, 0.5)
        pady = max((lat.max() - lat.min()) * 0.08, 0.5)
        xmin, xmax = lon.min() - padx, lon.max() + padx
        ymin, ymax = lat.min() - pady, lat.max() + pady
        draw.rectangle((x0, y0, x1, y1), outline=INK, width=3)
        for k in range(1, 5):
            xx = x0 + (x1 - x0) * k / 5
            yy = y0 + (y1 - y0) * k / 5
            draw.line((xx, y0, xx, y1), fill=GRID, width=2)
            draw.line((x0, yy, x1, yy), fill=GRID, width=2)
        for _, row in sub.iterrows():
            x = x0 + (float(row.centroid_lon) - xmin) / (xmax - xmin) * (x1 - x0)
            y = y1 - (float(row.centroid_lat) - ymin) / (ymax - ymin) * (y1 - y0)
            radius = 7 if region == "India" else 11
            color = PRIORITY[str(row.priority_class)]
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color, outline="white", width=1)
        draw.text((x0, 78), f"{region} (n={len(sub)})", font=font(30, True), fill=INK)
        draw.text((x0, y1 + 16), f"Longitude {xmin:.1f}° to {xmax:.1f}°", font=font(22), fill=GREY)
        draw.text((x0 + 400, y1 + 16), f"Latitude {ymin:.1f}° to {ymax:.1f}°", font=font(22), fill=GREY)
    lx = 420
    for priority in ("Critical", "High", "Medium", "Low"):
        draw.ellipse((lx, 790, lx + 20, 810), fill=PRIORITY[priority])
        draw.text((lx + 28, 785), priority, font=font(22), fill=INK)
        lx += 220
    save(img, "wildfire_task_map.png")


def priority_figure(tasks: pd.DataFrame) -> None:
    order = ["Critical", "High", "Medium", "Low"]
    regions = ["California", "India"]
    counts = {r: [int(((tasks.region == r) & (tasks.priority_class == p)).sum()) for p in order] for r in regions}
    img = Image.new("RGB", (1800, 900), "white")
    draw = ImageDraw.Draw(img)
    draw.text((70, 28), "Wildfire task workload and priority composition", font=font(40, True), fill=INK)

    def axes(box: tuple[int, int, int, int], ymax: float, ylabel: str) -> None:
        x0, y0, x1, y1 = box
        draw.line((x0, y1, x1, y1), fill=INK, width=3)
        draw.line((x0, y0, x0, y1), fill=INK, width=3)
        for k in range(6):
            y = y1 - (y1 - y0) * k / 5
            draw.line((x0, y, x1, y), fill=GRID, width=2)
            val = ymax * k / 5
            draw.text((x0 - 60, y - 12), f"{val:.0f}", font=font(18), fill=GREY)
        draw.text((x0, y0 - 44), ylabel, font=font(24, True), fill=INK)

    left = (100, 160, 850, 760)
    axes(left, 450, "Task count")
    group_w = 150
    bar_w = 52
    for i, p in enumerate(order):
        cx = left[0] + 90 + i * group_w
        for j, r in enumerate(regions):
            value = counts[r][i]
            h = value / 450 * (left[3] - left[1])
            color = BLUE if r == "California" else ORANGE
            x = cx + j * (bar_w + 10)
            draw.rectangle((x, left[3] - h, x + bar_w, left[3]), fill=color)
            draw.text((x, left[3] - h - 26), str(value), font=font(18, True), fill=INK)
        draw.text((cx - 8, left[3] + 16), p, font=font(18), fill=INK)

    right = (990, 160, 1710, 760)
    axes(right, 100, "Within-region share (%)")
    for j, r in enumerate(regions):
        x0 = right[0] + 150 + j * 280
        y = right[3]
        total = sum(counts[r])
        for p, value in zip(order[::-1], counts[r][::-1]):
            h = value / total * (right[3] - right[1])
            draw.rectangle((x0, y - h, x0 + 130, y), fill=PRIORITY[p], outline="white", width=2)
            if h > 30:
                draw.text((x0 + 36, y - h / 2 - 11), f"{100*value/total:.1f}", font=font(18, True), fill="white")
            y -= h
        draw.text((x0 + 8, right[3] + 16), r, font=font(22, True), fill=INK)

    draw.rectangle((580, 92, 610, 117), fill=BLUE)
    draw.text((620, 90), "California", font=font(22), fill=INK)
    draw.rectangle((770, 92, 800, 117), fill=ORANGE)
    draw.text((810, 90), "India", font=font(22), fill=INK)
    lx = 1070
    for p in order:
        draw.rectangle((lx, 92, lx + 24, 116), fill=PRIORITY[p])
        draw.text((lx + 32, 90), p, font=font(20), fill=INK)
        lx += 150
    save(img, "priority_distribution.png")


def exp_offset_fit(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    best = None
    for k in np.linspace(0.001, 0.20, 600):
        a = np.column_stack((np.exp(-k * x), np.ones_like(x)))
        coef, *_ = np.linalg.lstsq(a, y, rcond=None)
        pred = a @ coef
        err = float(np.mean((pred - y) ** 2))
        if best is None or err < best[0]:
            best = (err, k, coef)
    _, k, coef = best
    xx = np.linspace(0, 100, 201)
    return xx, coef[0] * np.exp(-k * xx) + coef[1]


def regression_figure(summary: pd.DataFrame) -> None:
    img = Image.new("RGB", (1800, 1160), "white")
    draw = ImageDraw.Draw(img)
    draw.text((65, 26), "Exploratory fits to central-node marginal means", font=font(40, True), fill=INK)
    specs = [
        ("California", "observation_mean", "Observation (%)", "exponential + offset"),
        ("India", "observation_mean", "Observation (%)", "quadratic"),
        ("California", "receiver_fraction_mean", "Useful coverage (%)", "quadratic"),
        ("India", "receiver_fraction_mean", "Useful coverage (%)", "quadratic"),
        ("California", "p90_first_reception_mean", "P90 first reception (min)", "exponential + offset"),
        ("India", "p90_first_reception_mean", "P90 first reception (min)", "exponential + offset"),
    ]
    for index, (region, field, ylabel, model) in enumerate(specs):
        row, col = divmod(index, 2)
        x0, y0 = 90 + col * 860, 125 + row * 335
        x1, y1 = x0 + 760, y0 + 245
        sub = summary[summary.region == region]
        x = sub.cn_fraction_percent.astype(float).to_numpy()
        y = sub[field].astype(float).to_numpy()
        ymin = 0.0
        ymax = float(y.max() * 1.12)
        draw.line((x0, y1, x1, y1), fill=INK, width=3)
        draw.line((x0, y0, x0, y1), fill=INK, width=3)
        for k in range(1, 5):
            yy = y1 - (y1 - y0) * k / 5
            draw.line((x0, yy, x1, yy), fill=GRID, width=2)
        if model == "quadratic":
            coef = np.polyfit(x, y, 2)
            xx = np.linspace(0, 100, 201)
            yy = np.polyval(coef, xx)
        else:
            xx, yy = exp_offset_fit(x, y)
        points = []
        for xv, yv in zip(xx, yy):
            px = x0 + xv / 100 * (x1 - x0)
            py = y1 - (yv - ymin) / (ymax - ymin) * (y1 - y0)
            points.append((px, py))
        draw.line(points, fill=ORANGE, width=5)
        for xv, yv in zip(x, y):
            px = x0 + xv / 100 * (x1 - x0)
            py = y1 - (yv - ymin) / (ymax - ymin) * (y1 - y0)
            draw.ellipse((px - 7, py - 7, px + 7, py + 7), fill=BLUE, outline="white", width=2)
        draw.text((x0, y0 - 34), f"{region}: {ylabel}", font=font(24, True), fill=INK)
        draw.text((x1 - 225, y0 + 8), model, font=font(19), fill=ORANGE)
        draw.text((x0 - 5, y1 + 10), "0", font=font(17), fill=GREY)
        draw.text((x1 - 28, y1 + 10), "100", font=font(17), fill=GREY)
        draw.text((x0 - 55, y1 - 12), "0", font=font(17), fill=GREY)
        draw.text((x0 - 70, y0 - 10), f"{ymax:.0f}", font=font(17), fill=GREY)
    draw.text((760, 1120), "Central-node fraction (%)", font=font(23, True), fill=INK)
    save(img, "regression_model_comparison.png")


def main() -> None:
    tasks = pd.read_csv(ROOT / "data/tasks/final_tasks_compact.csv")
    summary = pd.read_csv(ROOT / "data/nominal/01_cn_sweep_regional_summary.data.csv")
    map_figure(tasks)
    priority_figure(tasks)
    regression_figure(summary)
    print("Generated wildfire_task_map.png, priority_distribution.png, regression_model_comparison.png")


if __name__ == "__main__":
    main()
