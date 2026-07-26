#!/usr/bin/env python3
"""Render repeating-geometry panel pattern candidates as a PNG contact sheet.

Shapes are described in millimetres on a 1U Intellijel 8HP panel and shared with
the KiCad generator, so the preview matches what gets fabricated.

Shape vocabulary (all coordinates in mm, y increases downward as in KiCad):
    ("poly", [(x, y), ...])                filled polygon
    ("polyline", [(x, y), ...], width)     open stroked polyline
    ("circle", (cx, cy), r, width)         stroked circle
    ("arc", (cx, cy), r, a1, a2, width)    stroked arc, degrees, y-down

Usage:
    python3 _preview_patterns.py [--mode emboss|expose] [--batch N]
                                 [--only Name,Name]
"""
from __future__ import annotations

import argparse
import math
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "_preview"

# 1U Intellijel 8HP
W, H = 40.3, 39.65
HOLES = [(7.5, 3.0), (32.9, 3.0), (7.5, 36.65), (32.9, 36.65)]
PAD = (5.5, 3.7)
DRILL = (5.0, 3.2)

BLEED = 9.0  # generate patterns past the outline so they bleed off the edge

SHEET_BG = "#0d0e10"
LABEL = "#c9ccd1"

# How the pattern is meant to be realised on the panel; names match the
# --style options of _gen_relief.py.
MODES = {
    # Copper under closed soldermask: one colour, pattern reads as relief.
    "emboss": {
        "panel": "#26272b",
        "faces": [
            (0.16, 0.16, "#131417"),
            (-0.16, -0.16, "#6a6e75"),
            (0.0, 0.0, "#33353a"),
        ],
        "hole_rim": "#6a6e75",
    },
    # Solid copper pour with the pattern opened in the soldermask.
    "expose": {
        "panel": "#17181a",
        "faces": [(0.0, 0.0, "#d8b45c")],
        "hole_rim": "#d8b45c",
    },
}


# --- geometry helpers ---------------------------------------------------------

def on_circle(c, r, deg):
    a = math.radians(deg)
    return (c[0] + r * math.cos(a), c[1] + r * math.sin(a))


def signed_area(pts):
    return 0.5 * sum(
        x0 * y1 - x1 * y0
        for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1])
    )


def shrink(pts, mm):
    """Miter-offset a simple polygon inward by `mm`, so gaps stay uniform."""
    if len(pts) < 3 or mm <= 0:
        return pts
    sign = 1.0 if signed_area(pts) > 0 else -1.0
    lines = []
    n = len(pts)
    for i in range(n):
        (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
        ex, ey = x1 - x0, y1 - y0
        length = math.hypot(ex, ey)
        if length < 1e-9:
            continue
        nx, ny = -ey / length * sign, ex / length * sign
        lines.append(((x0 + nx * mm, y0 + ny * mm), (ex, ey)))
    if len(lines) < 3:
        return []

    out = []
    for i in range(len(lines)):
        (p0, e0), (p1, e1) = lines[i], lines[(i + 1) % len(lines)]
        det = e0[0] * e1[1] - e0[1] * e1[0]
        if abs(det) < 1e-9:
            out.append(p1)
            continue
        t = ((p1[0] - p0[0]) * e1[1] - (p1[1] - p0[1]) * e1[0]) / det
        out.append((p0[0] + e0[0] * t, p0[1] + e0[1] * t))
    return out


def rect(x, y, w, h):
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def rotate(pts, deg, about):
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    ox, oy = about
    return [
        (ox + (x - ox) * ca - (y - oy) * sa, oy + (x - ox) * sa + (y - oy) * ca)
        for x, y in pts
    ]


def grid(step_x, step_y):
    """Cell indices whose origin covers the bleed area."""
    i0 = int(math.floor(-BLEED / step_x))
    i1 = int(math.ceil((W + BLEED) / step_x))
    j0 = int(math.floor(-BLEED / step_y))
    j1 = int(math.ceil((H + BLEED) / step_y))
    for j in range(j0, j1 + 1):
        for i in range(i0, i1 + 1):
            yield i, j


def bands(width, pitch, angle):
    """Parallel stripes of `width` at `pitch`, rotated about the panel centre."""
    about = (W / 2, H / 2)
    span = max(W, H) + 2 * BLEED
    out = []
    n = int(span / pitch) + 2
    for k in range(-n, n + 1):
        y = about[1] + k * pitch
        line = [(about[0] - span, y), (about[0] + span, y)]
        out.append(("polyline", rotate(line, angle, about), width))
    return out


# --- batch 1: tessellations ---------------------------------------------------

def _quarter_arcs(c, w, flip):
    """Quarter arcs at two opposite cell corners; `flip` picks the diagonal."""
    out = []
    r = c / 2
    for i, j in grid(c, c):
        x, y = i * c, j * c
        if flip(i, j):
            corners = [((x, y), 0, 90), ((x + c, y + c), 180, 270)]
        else:
            corners = [((x + c, y), 90, 180), ((x, y + c), 270, 360)]
        for ctr, a1, a2 in corners:
            out.append(("arc", ctr, r, a1, a2, w))
    return out


def p_rings():
    # Checkerboard orientation makes the four arcs at every vertex close a ring.
    return _quarter_arcs(7.2, 1.3, lambda i, j: (i + j) % 2 == 0)


def p_truchet():
    # Column-wise orientation leaves two arcs per vertex, so curves run through.
    return _quarter_arcs(7.2, 1.3, lambda i, j: i % 2 == 0)


def hex_centres(r):
    step_x = math.sqrt(3) * r
    step_y = 1.5 * r
    for i, j in grid(step_x, step_y):
        yield (i * step_x + (j % 2) * step_x / 2, j * step_y)


def hexagon(c, r):
    return [on_circle(c, r, k * 60 - 90) for k in range(6)]


def p_honeycomb():
    return [("poly", shrink(hexagon(c, 3.5), 0.3)) for c in hex_centres(3.5)]


def p_triangles():
    s = 6.5
    h = s * math.sqrt(3) / 2
    out = []
    for i, j in grid(s, h):
        x, y = i * s, j * h
        out.append(("poly", [(x + s / 2, y), (x, y + h), (x + s, y + h)]))
    return out


def p_basketweave():
    c, b, gap = 6.0, 3.0, 0.4
    out = []
    for i, j in grid(c, c):
        x, y = i * c, j * c
        if (i + j) % 2 == 0:
            bricks = [(x, y, c, b), (x, y + b, c, b)]
        else:
            bricks = [(x, y, b, c), (x + b, y, b, c)]
        for bx, by, bw, bh in bricks:
            out.append(
                ("poly", rect(bx + gap / 2, by + gap / 2, bw - gap, bh - gap))
            )
    return out


def p_octagon():
    c = 6.0
    t = c / (2 + math.sqrt(2))
    out = []
    for i, j in grid(c, c):
        x, y = i * c, j * c
        oct_pts = [
            (x + t, y), (x + c - t, y),
            (x + c, y + t), (x + c, y + c - t),
            (x + c - t, y + c), (x + t, y + c),
            (x, y + c - t), (x, y + t),
        ]
        out.append(("poly", shrink(oct_pts, 0.25)))
    return out


def p_harlequin():
    s = 4.8
    about = (W / 2, H / 2)
    out = []
    n = int((max(W, H) + 2 * BLEED) / s) + 4
    for j in range(-n, n + 1):
        for i in range(-n, n + 1):
            if (i + j) % 2:
                continue
            sq = rect(about[0] + i * s, about[1] + j * s, s, s)
            out.append(("poly", shrink(rotate(sq, 45, about), 0.2)))
    return out


def p_fishscale():
    r, step_x, step_y = 3.3, 7.4, 4.3
    out = []
    for i, j in grid(step_x, step_y):
        cx = i * step_x + (j % 2) * step_x / 2
        cy = j * step_y
        scale = [on_circle((cx, cy), r, k * 12) for k in range(16)]
        out.append(("poly", scale))
    return out


def p_pinwheel():
    c, b, gap = 7.6, 2.7, 0.5
    out = []
    for i, j in grid(c, c):
        x, y = i * c, j * c
        blades = [
            (x, y, c - b, b),
            (x + c - b, y, b, c - b),
            (x + b, y + c - b, c - b, b),
            (x, y + b, b, c - b),
        ]
        for bx, by, bw, bh in blades:
            out.append(
                ("poly", rect(bx + gap / 2, by + gap / 2, bw - gap, bh - gap))
            )
    return out


def p_stargrid():
    p, ro, ri = 6.6, 3.25, 2.3
    out = []
    for i, j in grid(p, p):
        c = (i * p + p / 2, j * p + p / 2)
        star = []
        for k in range(8):
            star.append(on_circle(c, ro, k * 45))
            star.append(on_circle(c, ri, k * 45 + 22.5))
        out.append(("poly", star))
    return out


def p_floweroflife():
    """Overlapping circles on a triangular lattice."""
    r, w = 5.0, 0.55
    step_y = r * math.sqrt(3) / 2
    return [
        ("circle", (i * r + (j % 2) * r / 2, j * step_y), r, w)
        for i, j in grid(r, step_y)
    ]


def p_asanoha():
    """Hemp-leaf lattice: honeycomb edges plus spokes to each hexagon centre."""
    r, w = 6.0, 1.2
    out = []
    for c in hex_centres(r):
        hexa = hexagon(c, r)
        out.append(("polyline", hexa + [hexa[0]], w))
        for v in hexa:
            out.append(("polyline", [c, v], w))
    return out


def p_greekkey():
    c, w = 6.0, 0.7
    key = [
        (1.4, 6.0), (1.4, 1.4), (4.6, 1.4), (4.6, 4.2), (2.8, 4.2),
    ]
    out = []
    for i, j in grid(c, c):
        x, y = i * c, j * c
        out.append(("polyline", [(x, y + c), (x + c, y + c)], w))
        pts = [(c - kx, ky) for kx, ky in key] if j % 2 else key
        out.append(("polyline", [(x + kx, y + ky) for kx, ky in pts], w))
    return out


def p_nested():
    c, w = 11.6, 1.0
    out = []
    for i, j in grid(c, c):
        cx, cy = i * c + c / 2, j * c + c / 2
        for side in (9.4, 5.0):
            sq = rect(cx - side / 2, cy - side / 2, side, side)
            out.append(("polyline", sq + [sq[0]], w))
    return out


def p_chevron():
    period, amp, thick, pitch = 9.0, 4.5, 2.6, 5.6
    n = int((W + 2 * BLEED) / (period / 2)) + 3
    tops = [
        (-BLEED + k * period / 2, amp if k % 2 else 0.0) for k in range(n)
    ]
    out = []
    j0 = int(math.floor((-BLEED - amp) / pitch))
    j1 = int(math.ceil((H + BLEED) / pitch))
    for j in range(j0, j1 + 1):
        base = j * pitch
        out.append(("polyline", [(x, base + dy) for x, dy in tops], thick))
    return out


def p_interlace():
    pitch, band, gap = 6.0, 2.4, 0.45
    idx = list(range(int(math.floor(-BLEED / pitch)),
                     int(math.ceil((W + BLEED) / pitch)) + 1))
    jdx = list(range(int(math.floor(-BLEED / pitch)),
                     int(math.ceil((H + BLEED) / pitch)) + 1))

    def pieces(lo, hi, breaks):
        out = []
        cursor = lo
        for c0, c1 in sorted(breaks):
            if c0 > cursor:
                out.append((cursor, c0))
            cursor = max(cursor, c1)
        if cursor < hi:
            out.append((cursor, hi))
        return out

    half = band / 2
    out = []
    for j in jdx:
        cy = j * pitch + pitch / 2
        breaks = [
            (i * pitch + pitch / 2 - half - gap, i * pitch + pitch / 2 + half + gap)
            for i in idx if (i + j) % 2
        ]
        for a, b in pieces(idx[0] * pitch, (idx[-1] + 1) * pitch, breaks):
            out.append(("poly", rect(a, cy - half, b - a, band)))
    for i in idx:
        cx = i * pitch + pitch / 2
        breaks = [
            (j * pitch + pitch / 2 - half - gap, j * pitch + pitch / 2 + half + gap)
            for j in jdx if (i + j) % 2 == 0
        ]
        for a, b in pieces(jdx[0] * pitch, (jdx[-1] + 1) * pitch, breaks):
            out.append(("poly", rect(cx - half, a, band, b - a)))
    return out


def p_trihex():
    """Hexagon outlines sharing edges, so every vertex sprouts three spurs."""
    r, w = 4.6, 1.3
    out = []
    for c in hex_centres(r):
        hexa = hexagon(c, r)
        out.append(("polyline", hexa + [hexa[0]], w))
    return out


# --- batch 2: relief-oriented textures ----------------------------------------

def p_diamondplate():
    px, py = 8.6, 8.6
    length, wide, offset = 6.0, 3.4, 4.0
    out = []
    for i, j in grid(px, py):
        cx, cy = i * px + px / 2, j * py + py / 2
        angle = 45 if (i + j) % 2 == 0 else -45
        for side in (-offset / 2, offset / 2):
            lozenge = [
                (cx - length / 2, cy + side), (cx, cy + side - wide / 2),
                (cx + length / 2, cy + side), (cx, cy + side + wide / 2),
            ]
            out.append(("poly", rotate(lozenge, angle, (cx, cy))))
    return out


def disc(c, r, steps=20):
    return [on_circle(c, r, k * 360.0 / steps) for k in range(steps)]


def p_dimples():
    return [("poly", disc(c, 2.7, 24)) for c in hex_centres(3.35)]


def p_dotgrid():
    pitch, r = 3.4, 1.1
    return [("poly", disc((i * pitch, j * pitch), r))
            for i, j in grid(pitch, pitch)]


def p_dotstagger():
    d, r = 3.7, 1.25
    step_y = d * math.sqrt(3) / 2
    return [
        ("poly", disc((i * d + (j % 2) * d / 2, j * step_y), r))
        for i, j in grid(d, step_y)
    ]


def p_dotduo():
    pitch = 3.6
    out = []
    for i, j in grid(pitch, pitch):
        r = 1.5 if (i + j) % 2 == 0 else 0.8
        out.append(("poly", disc((i * pitch, j * pitch), r)))
    return out


def p_waffle():
    c, gap = 6.4, 1.5
    return [
        ("poly", rect(i * c + gap / 2, j * c + gap / 2, c - gap, c - gap))
        for i, j in grid(c, c)
    ]


def p_ribs():
    return bands(2.8, 4.6, 0)


def p_diagonalribs():
    return bands(2.8, 4.6, 45)


def p_rhombille():
    r = 4.2
    out = []
    for c in hex_centres(r):
        v = hexagon(c, r)
        for k in (0, 2, 4):
            rhomb = [c, v[k], v[(k + 1) % 6], v[(k + 2) % 6]]
            out.append(("poly", shrink(rhomb, 0.3)))
    return out


def p_herringbone():
    # Two bricks per fundamental domain on the lattice (2,2) / (1,-1) in cells,
    # which is the exact herringbone tiling for 2:1 bricks.
    u, gap = 2.9, 0.4
    out = []
    for m in range(-10, 20):
        for n in range(-24, 24):
            ox, oy = 2 * m + n, 2 * m - n
            for (ax, ay), kind in (((0, 0), "h"), ((2, 0), "v")):
                cx, cy = (ox + ax) * u, (oy + ay) * u
                bw, bh = (2 * u, u) if kind == "h" else (u, 2 * u)
                if cx > W + BLEED or cy > H + BLEED:
                    continue
                if cx + bw < -BLEED or cy + bh < -BLEED:
                    continue
                out.append(
                    ("poly", rect(cx + gap / 2, cy + gap / 2, bw - gap, bh - gap))
                )
    return out


def p_rivetgrid():
    c, w = 9.0, 1.0
    out = []
    for i, j in grid(c, c):
        x, y = i * c, j * c
        sq = rect(x + 0.9, y + 0.9, c - 1.8, c - 1.8)
        out.append(("polyline", sq + [sq[0]], w))
        centre = (x + c / 2, y + c / 2)
        out.append(("poly", [on_circle(centre, 1.5, k * 20) for k in range(18)]))
    return out


def p_coffer():
    c = 9.4
    out = []
    for i, j in grid(c, c):
        x, y = i * c, j * c
        outer = rect(x + 1.5, y + 1.5, c - 3.0, c - 3.0)
        inner = rect(x + 3.2, y + 3.2, c - 6.4, c - 6.4)
        out.append(("polyline", outer + [outer[0]], 0.9))
        out.append(("poly", inner))
    return out


def p_scallopfan():
    c, w = 8.4, 0.9
    out = []
    for i, j in grid(c, c):
        x, y = i * c, j * c
        # The outermost arc spans the whole cell, so fans link up across cells.
        for k in (1, 2, 3):
            out.append(("arc", (x, y), c * k / 3, 0, 90, w))
    return out


def p_kagome():
    """Triangular lattice drawn as outlines."""
    s, w = 8.4, 1.2
    h = s * math.sqrt(3) / 2
    out = []
    for i, j in grid(s, h):
        x, y = i * s, j * h
        tri = [(x + s / 2, y), (x, y + h), (x + s, y + h)]
        out.append(("polyline", tri + [tri[0]], w))
    return out


def p_stepterrace():
    c, w = 12.0, 1.0
    out = []
    for i, j in grid(c, c):
        x, y = i * c, j * c
        for inset in (1.4, 3.9):
            sq = rect(x + inset, y + inset, c - 2 * inset, c - 2 * inset)
            out.append(("polyline", sq + [sq[0]], w))
    return out


def p_hexring():
    r, ring, w = 6.0, 4.6, 1.4
    out = []
    for c in hex_centres(r):
        hexa = hexagon(c, ring)
        out.append(("polyline", hexa + [hexa[0]], w))
    return out


def p_crossplus():
    # Greek crosses of arm width a tile the plane on the lattice (2a, a)/(-a, 2a).
    a = 2.4
    h, e = a / 2, 1.5 * a
    about = (W / 2, H / 2)
    out = []
    for m in range(-14, 15):
        for n in range(-14, 15):
            cx = about[0] + m * 2 * a - n * a
            cy = about[1] + m * a + n * 2 * a
            if not (-BLEED - e < cx < W + BLEED + e):
                continue
            if not (-BLEED - e < cy < H + BLEED + e):
                continue
            cross = [
                (cx - h, cy - e), (cx + h, cy - e), (cx + h, cy - h),
                (cx + e, cy - h), (cx + e, cy + h), (cx + h, cy + h),
                (cx + h, cy + e), (cx - h, cy + e), (cx - h, cy + h),
                (cx - e, cy + h), (cx - e, cy - h), (cx - h, cy - h),
            ]
            out.append(("poly", shrink(cross, 0.22)))
    return out


def p_parquet():
    c, gap = 8.4, 0.4
    strip = c / 3
    out = []
    for i, j in grid(c, c):
        x, y = i * c, j * c
        for k in range(3):
            if (i + j) % 2 == 0:
                bx, by, bw, bh = x, y + k * strip, c, strip
            else:
                bx, by, bw, bh = x + k * strip, y, strip, c
            out.append(
                ("poly", rect(bx + gap / 2, by + gap / 2, bw - gap, bh - gap))
            )
    return out


def p_trihexsolid():
    r = 4.0
    return [("poly", shrink(hexagon(c, r), 0.9)) for c in hex_centres(r)]


PATTERNS = [
    (1, "Honeycomb", "solid hex cells", p_honeycomb),
    (1, "Triangles", "alternating triangle tiling", p_triangles),
    (1, "Harlequin", "diamond checker", p_harlequin),
    (1, "Basketweave", "paired brick checker", p_basketweave),
    (1, "OctagonSquare", "truncated square tiling", p_octagon),
    (1, "StarGrid", "8-point star tessellation", p_stargrid),
    (1, "Pinwheel", "rotational windmill blades", p_pinwheel),
    (1, "Chevron", "nested zigzag bands", p_chevron),
    (1, "Truchet", "quarter-arc weave", p_truchet),
    (1, "RingLattice", "quarter arcs closing into rings", p_rings),
    (1, "FishScale", "overlapping solid scales", p_fishscale),
    (1, "Interlace", "woven over-under bands", p_interlace),
    (1, "FlowerOfLife", "overlapping circle lattice", p_floweroflife),
    (1, "Asanoha", "hemp-leaf hexagon lattice", p_asanoha),
    (1, "GreekKey", "meander band repeat", p_greekkey),
    (1, "NestedSquares", "concentric square grid", p_nested),
    (1, "TriHex", "hexagon lattice with spurs", p_trihex),
    (2, "DiamondPlate", "tread plate lozenges", p_diamondplate),
    (2, "Dimples", "hex-packed domes", p_dimples),
    (2, "Waffle", "square well grid", p_waffle),
    (2, "Ribs", "straight parallel ribs", p_ribs),
    (2, "DiagonalRibs", "45 degree ribs", p_diagonalribs),
    (2, "Rhombille", "isometric cube illusion", p_rhombille),
    (2, "Herringbone", "2:1 brick herringbone", p_herringbone),
    (2, "RivetGrid", "framed squares with centre holes", p_rivetgrid),
    (2, "Coffer", "recessed panel grid", p_coffer),
    (2, "ScallopFan", "quarter-circle fans", p_scallopfan),
    (2, "Kagome", "triangular lattice", p_kagome),
    (2, "StepTerrace", "stepped square terraces", p_stepterrace),
    (2, "HexRing", "thick hexagon rings", p_hexring),
    (2, "CrossPlus", "greek cross tessellation", p_crossplus),
    (2, "Parquet", "three-strip parquet blocks", p_parquet),
    (2, "TriHexSolid", "hexagons with triangle gaps", p_trihexsolid),
    (2, "DotGrid", "square grid of dots", p_dotgrid),
    (2, "DotStagger", "staggered perforation", p_dotstagger),
    (2, "DotDuo", "alternating large and small dots", p_dotduo),
]

PATTERN_BY_NAME = {name: fn for _, name, _, fn in PATTERNS}


# --- SVG ----------------------------------------------------------------------

def fmt(v):
    return f"{v:.4f}".rstrip("0").rstrip(".")


def shape_svg(shape, color):
    kind = shape[0]
    if kind == "poly":
        pts = " ".join(f"{fmt(x)},{fmt(y)}" for x, y in shape[1])
        return f'<polygon points="{pts}" fill="{color}"/>'
    if kind == "polyline":
        pts = " ".join(f"{fmt(x)},{fmt(y)}" for x, y in shape[1])
        return (
            f'<polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="{fmt(shape[2])}" stroke-linecap="round" '
            f'stroke-linejoin="round"/>'
        )
    if kind == "circle":
        (cx, cy), r, w = shape[1], shape[2], shape[3]
        return (
            f'<circle cx="{fmt(cx)}" cy="{fmt(cy)}" r="{fmt(r)}" fill="none" '
            f'stroke="{color}" stroke-width="{fmt(w)}"/>'
        )
    if kind == "arc":
        c, r, a1, a2, w = shape[1], shape[2], shape[3], shape[4], shape[5]
        sx, sy = on_circle(c, r, a1)
        ex, ey = on_circle(c, r, a2)
        large = 1 if abs(a2 - a1) > 180 else 0
        sweep = 1 if a2 > a1 else 0
        return (
            f'<path d="M {fmt(sx)} {fmt(sy)} A {fmt(r)} {fmt(r)} 0 {large} '
            f'{sweep} {fmt(ex)} {fmt(ey)}" fill="none" stroke="{color}" '
            f'stroke-width="{fmt(w)}" stroke-linecap="round"/>'
        )
    raise ValueError(kind)


def panel_svg(shapes, clip_id, mode):
    style = MODES[mode]
    parts = [f'<rect x="0" y="0" width="{W}" height="{H}" fill="{style["panel"]}"/>']
    parts.append(f'<g clip-path="url(#{clip_id})">')
    for dx, dy, color in style["faces"]:
        parts.append(f'<g transform="translate({fmt(dx)},{fmt(dy)})">')
        parts.extend(shape_svg(s, color) for s in shapes)
        parts.append("</g>")
    parts.append("</g>")
    for hx, hy in HOLES:
        pw, ph = PAD
        dw, dh = DRILL
        parts.append(
            f'<rect x="{fmt(hx - pw / 2)}" y="{fmt(hy - ph / 2)}" '
            f'width="{fmt(pw)}" height="{fmt(ph)}" rx="{fmt(ph / 2)}" '
            f'fill="{style["hole_rim"]}"/>'
        )
        parts.append(
            f'<rect x="{fmt(hx - dw / 2)}" y="{fmt(hy - dh / 2)}" '
            f'width="{fmt(dw)}" height="{fmt(dh)}" rx="{fmt(dh / 2)}" '
            f'fill="{SHEET_BG}"/>'
        )
    parts.append(
        f'<rect x="0" y="0" width="{W}" height="{H}" fill="none" '
        f'stroke="#5a5f66" stroke-width="0.15"/>'
    )
    return "".join(parts)


def contact_sheet(entries, mode, path, cols=4, px_per_mm=9.0):
    pw, ph = W * px_per_mm, H * px_per_mm
    gap, label_h, pad = 22, 30, 22
    rows = math.ceil(len(entries) / cols)
    total_w = pad * 2 + cols * pw + (cols - 1) * gap
    total_h = pad * 2 + rows * (ph + label_h) + (rows - 1) * gap

    defs, body = [], []
    for idx, (label, note, shapes) in enumerate(entries):
        col, row = idx % cols, idx // cols
        x = pad + col * (pw + gap)
        y = pad + row * (ph + label_h + gap)
        clip_id = f"clip{idx}"
        defs.append(
            f'<clipPath id="{clip_id}"><rect x="0" y="0" width="{W}" '
            f'height="{H}"/></clipPath>'
        )
        body.append(
            f'<g transform="translate({fmt(x)},{fmt(y)}) '
            f'scale({fmt(px_per_mm)})">{panel_svg(shapes, clip_id, mode)}</g>'
        )
        body.append(
            f'<text x="{fmt(x)}" y="{fmt(y + ph + 19)}" fill="{LABEL}" '
            f'font-family="Helvetica,Arial,sans-serif" font-size="15" '
            f'font-weight="bold">{label}</text>'
        )
        body.append(
            f'<text x="{fmt(x + pw)}" y="{fmt(y + ph + 19)}" fill="#7d838c" '
            f'font-family="Helvetica,Arial,sans-serif" font-size="12" '
            f'text-anchor="end">{note}</text>'
        )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{fmt(total_w)}" '
        f'height="{fmt(total_h)}" viewBox="0 0 {fmt(total_w)} {fmt(total_h)}">'
        f'<defs>{"".join(defs)}</defs>'
        f'<rect width="100%" height="100%" fill="{SHEET_BG}"/>'
        f'{"".join(body)}</svg>'
    )
    path.write_text(svg)
    subprocess.run(["rsvg-convert", "-o", str(path.with_suffix(".png")),
                    str(path)], check=True)
    return path.with_suffix(".png")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="emboss", choices=sorted(MODES))
    ap.add_argument("--batch", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    names = [n.strip() for n in args.only.split(",") if n.strip()]

    entries = []
    for offset, (batch, name, note, fn) in enumerate(PATTERNS, start=1):
        if args.batch and batch != args.batch:
            continue
        if names and name not in names:
            continue
        entries.append((f"{offset}. {name}", note, fn()))

    stem = args.out or f"patterns_{args.mode}" + (
        f"_b{args.batch}" if args.batch else ""
    )
    png = contact_sheet(entries, args.mode, OUT_DIR / f"{stem}.svg")
    print(f"{png}  ({len(entries)} patterns, mode={args.mode})")


if __name__ == "__main__":
    main()
