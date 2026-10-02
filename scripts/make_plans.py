"""Draws the demo site's site-plan illustrations (SVG): contour lines, plot boundary, house, paths, trees.
Run: python scripts/make_plans.py  →  demo/assets/*.svg"""
from __future__ import annotations

import math
import random
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "demo" / "assets"
MOSS, SPRUCE, LICHEN, STONE, GRANITE, BIRCH, WATER = "#2f6b4f", "#1f4a3a", "#c9d3a0", "#b9bdb5", "#2b2f2c", "#eef1ec", "#bfcfcc"


def blob(cx, cy, r, phases, amps, n=140):
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        k = 1 + sum(a * math.sin(m * t + p) for (m, a), p in zip(amps, phases))
        pts.append((cx + r * k * math.cos(t), cy + r * 0.82 * k * math.sin(t)))
    return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + " Z"


def contours(rng, w, h, hills, rings=9, step=34):
    out = []
    for hx, hy, base in hills:
        phases = [rng.uniform(0, 6.28) for _ in range(3)]
        amps = [(2, rng.uniform(.05, .1)), (3, rng.uniform(.03, .07)), (5, rng.uniform(.01, .03))]
        for k in range(rings):
            r = base + k * step
            op = 0.55 - k * 0.04
            out.append(f'<path d="{blob(hx, hy, r, phases, amps)}" fill="none" stroke="{MOSS}" '
                       f'stroke-width="{1.4 if k % 4 == 0 else 0.8}" opacity="{max(op, .16):.2f}"/>')
            if k % 4 == 0 and k:
                lx, ly = hx + r * 0.95, hy - 4
                if 0 < lx < w - 30 and 0 < ly < h:
                    out.append(f'<text x="{lx:.0f}" y="{ly:.0f}" font-size="9" fill="{MOSS}" opacity=".7" '
                               f'font-family="Golos Text, sans-serif">+{12 - k * .5:.1f}</text>')
    return "\n".join(out)


def tree(x, y, r, kind="leaf"):
    if kind == "pine":
        spikes = " ".join(f"M{x:.1f},{y:.1f} L{x + r * math.cos(a):.1f},{y + r * math.sin(a):.1f}"
                          for a in [i * math.pi / 6 for i in range(12)])
        return (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{MOSS}" fill-opacity=".22" stroke="{SPRUCE}" stroke-width="1"/>'
                f'<path d="{spikes}" stroke="{SPRUCE}" stroke-width=".7" opacity=".7"/>')
    return (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{MOSS}" fill-opacity=".3" stroke="{SPRUCE}" stroke-width="1.1"/>'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.6" fill="{SPRUCE}"/>')


def frame(w, h, body, label=""):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" role="img" aria-label="{label}">'
            f'<defs><clipPath id="c"><rect width="{w}" height="{h}"/></clipPath></defs>'
            f'<g clip-path="url(#c)"><rect width="{w}" height="{h}" fill="{BIRCH}"/>{body}</g></svg>\n')


def north_and_scale(x, y):
    return (f'<g transform="translate({x},{y})" font-family="Golos Text, sans-serif" fill="{GRANITE}">'
            f'<path d="M0,-22 L7,0 L0,-5 L-7,0 Z" fill="{GRANITE}"/><text x="-4" y="14" font-size="11">С</text>'
            f'<g transform="translate(26,4)"><rect width="30" height="5" fill="{GRANITE}"/>'
            f'<rect x="30" width="30" height="5" fill="none" stroke="{GRANITE}"/>'
            f'<text y="18" font-size="9">0</text><text x="27" y="18" font-size="9">5</text>'
            f'<text x="54" y="18" font-size="9">10 м</text></g></g>')


def hero(rng):
    w, h = 640, 600
    body = [contours(rng, w, h, [(160, 140, 30), (520, 470, 40)], rings=10)]
    px, py, pw, ph = 70, 70, 500, 460                  # plot boundary
    body.append(f'<path d="M{px + 30},{py + 120} C{px + 200},{py + 60} {px + 330},{py + 160} {px + 470},{py + 110} '
                f'L{px + 470},{py + 420} C{px + 320},{py + 380} {px + 160},{py + 440} {px + 30},{py + 400} Z" '
                f'fill="{LICHEN}" fill-opacity=".55"/>')  # lawn
    body.append(f'<rect x="{px + 290}" y="{py + 190}" width="140" height="110" fill="{BIRCH}" stroke="{GRANITE}" stroke-width="2"/>'
                f'<path d="M{px + 290},{py + 190} l140,110 M{px + 430},{py + 190} l-140,110" stroke="{GRANITE}" stroke-width=".6" opacity=".5"/>'
                f'<rect x="{px + 250}" y="{py + 220}" width="40" height="60" fill="{STONE}" fill-opacity=".6" stroke="{GRANITE}" stroke-width="1"/>')
    body.append(f'<path d="M{px},{py + 330} C{px + 90},{py + 330} {px + 150},{py + 260} {px + 250},{py + 255}" '
                f'fill="none" stroke="{STONE}" stroke-width="14" stroke-linecap="round"/>'
                f'<path d="M{px + 360},{py + 300} C{px + 365},{py + 360} {px + 420},{py + 380} {px + 470},{py + 390}" '
                f'fill="none" stroke="{STONE}" stroke-width="10" stroke-linecap="round"/>')
    body.append(f'<ellipse cx="{px + 140}" cy="{py + 170}" rx="44" ry="28" fill="{WATER}" stroke="{MOSS}" stroke-width="1"/>')
    for i in range(14):                                    # hedge along the north edge
        body.append(tree(px + 18 + i * 34, py + 14, 13))
    for x, y, r, k in [(px + 60, py + 250, 26, "pine"), (px + 120, py + 400, 30, "pine"), (px + 420, py + 80, 24, "leaf"),
                       (px + 210, py + 120, 18, "leaf"), (px + 470, py + 300, 20, "leaf"), (px + 30, py + 60, 22, "pine")]:
        body.append(tree(x, y, r, k))
    for x, y in [(px + 230, py + 330), (px + 265, py + 350), (px + 300, py + 335), (px + 200, py + 210)]:
        body.append(f'<circle cx="{x}" cy="{y}" r="7" fill="{MOSS}" fill-opacity=".45" stroke="{SPRUCE}" stroke-width=".8"/>')
    body.append(f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" fill="none" stroke="{GRANITE}" stroke-width="1.4" stroke-dasharray="10 5 2 5"/>')
    body.append(f'<g font-family="Golos Text, sans-serif" font-size="11" fill="{GRANITE}">'
                f'<text x="{px + pw / 2 - 12}" y="{py + ph + 22}">40 м</text>'
                f'<text x="{px + pw + 10}" y="{py + ph / 2}" >25 м</text>'
                f'<text x="{px + 318}" y="{py + 250}" font-size="12">дом</text>'
                f'<text x="{px + 112}" y="{py + 174}" font-size="10" fill="{SPRUCE}">пруд</text>'
                f'<text x="{px + 300}" y="{py + 150}" font-size="10" fill="{SPRUCE}">газон 6 сот.</text></g>')
    body.append(north_and_scale(w - 110, h - 30))
    return frame(w, h, "\n".join(body), "Генплан участка 10 соток с газоном, прудом и посадками")


def project(rng, kind):
    w, h = 360, 260
    body = [contours(rng, w, h, [(rng.uniform(60, 300), rng.uniform(40, 220), 20)], rings=8, step=26)]
    body.append(f'<rect x="20" y="20" width="320" height="220" fill="none" stroke="{GRANITE}" stroke-width="1.2" stroke-dasharray="8 4 2 4"/>')
    if kind == "forest":
        body.append(f'<path d="M20,190 C120,150 200,200 340,120" fill="none" stroke="{STONE}" stroke-width="9" stroke-linecap="round"/>')
        body.append(f'<rect x="140" y="60" width="90" height="70" fill="{BIRCH}" stroke="{GRANITE}" stroke-width="1.6"/>')
        for _ in range(16):
            x, y = rng.uniform(35, 325), rng.uniform(35, 225)
            if not (130 < x < 240 and 50 < y < 140):
                body.append(tree(x, y, rng.uniform(9, 16), "pine"))
    elif kind == "lawn":
        body.append(f'<rect x="40" y="90" width="280" height="130" fill="{LICHEN}" fill-opacity=".6"/>')
        body.append(f'<rect x="120" y="32" width="120" height="58" fill="{BIRCH}" stroke="{GRANITE}" stroke-width="1.6"/>')
        for i in range(10):
            body.append(tree(30 + i * 33, 228, 8))
        for i in range(4):
            for j in range(3):
                body.append(f'<circle cx="{80 + i * 70}" cy="{115 + j * 40}" r="2.2" fill="{SPRUCE}"/>'
                            f'<circle cx="{80 + i * 70}" cy="{115 + j * 40}" r="16" fill="none" stroke="{MOSS}" stroke-dasharray="2 3" opacity=".6"/>')
    else:  # townhouse yard
        body.append(f'<rect x="20" y="20" width="320" height="70" fill="{BIRCH}" stroke="{GRANITE}" stroke-width="1.6"/>')
        for i in range(10):
            for j in range(3):
                body.append(f'<rect x="{30 + i * 31}" y="{100 + j * 22}" width="29" height="20" fill="{STONE}" fill-opacity=".55"/>')
        body.append(f'<rect x="30" y="170" width="300" height="60" fill="{LICHEN}" fill-opacity=".6"/>')
        for x in (60, 170, 280):
            body.append(f'<circle cx="{x}" cy="160" r="4" fill="#e8c766" stroke="{GRANITE}" stroke-width=".8"/>')
        body.append(tree(300, 200, 18))
    return frame(w, h, "\n".join(body), "План участка")


if __name__ == "__main__":
    rng = random.Random(7)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "plan-hero.svg").write_text(hero(rng), encoding="utf-8")
    for name, kind in (("plan-komarovo.svg", "forest"), ("plan-vsevolozhsk.svg", "lawn"), ("plan-murino.svg", "yard")):
        (OUT / name).write_text(project(rng, kind), encoding="utf-8")
    print("ok")
