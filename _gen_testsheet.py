#!/usr/bin/env python3
"""Build one 3U 28HP panel carrying 30 relief patterns side by side.

This is a sample board, meant to be ordered once so the textures can be judged
by hand before committing to a pattern. Every patch uses the emboss recipe from
_gen_relief.py: copper on F.Cu with the soldermask left closed, so the whole
panel is one colour and the patterns read purely as relief.

The front carries nothing but the textures and a copper index number under each
patch; the legend that maps numbers to names lives on the back silkscreen so it
cannot interfere with judging the front.

    python3 _gen_testsheet.py [--check-only]
"""
from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import uuid
from pathlib import Path

import _gen_relief as g
import _preview_patterns as pp

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "3U_28HP_Blank"
OUT = ROOT / "testsheet" / "3U_28HP_ReliefTest"

BW, BH = 141.9, 128.5
HOLES = [(7.5, 3.0), (134.5, 3.0), (7.5, 125.5), (134.5, 125.5)]

# Grid sits clear of the mounting pads, which reach X 4.75-10.25 / 131.75-137.25
# and Y 1.15-4.85 / 123.65-127.35.
GRID = (11.5, 6.0, 130.4, 122.5)
COLS, ROWS = 6, 5
GUTTER = 1.4     # copper-free lane between neighbouring patches
LABEL_H = 3.4    # strip under each patch reserved for its index number

# Textures that duplicate another entry closely enough to skip on a sample board.
SKIP = {"DiagonalRibs", "DotGrid", "TriHexSolid", "StepTerrace", "Parquet",
        "Waffle"}


def cells():
    gx0, gy0, gx1, gy1 = GRID
    pitch_x = (gx1 - gx0) / COLS
    pitch_y = (gy1 - gy0) / ROWS
    patch_w = pitch_x - GUTTER
    patch_h = pitch_y - GUTTER - LABEL_H
    for n in range(COLS * ROWS):
        col, row = n % COLS, n // COLS
        x = gx0 + col * pitch_x + GUTTER / 2
        y = gy0 + row * pitch_y + GUTTER / 2
        yield n, (x, y, patch_w, patch_h), (x + patch_w / 2,
                                           y + patch_h + LABEL_H / 2)


def patch_geometry(fn, patch_w, patch_h):
    """Cut a patch-sized window out of the middle of a pattern.

    The pattern functions already draw well past the 1U panel they were written
    for, so a window smaller than that panel needs no changes to the library.
    Coordinates come back relative to the patch corner.
    """
    cx, cy = pp.W / 2, pp.H / 2
    win = (cx - patch_w / 2, cy - patch_h / 2,
           cx + patch_w / 2, cy + patch_h / 2)
    return [[(x - win[0], y - win[1]) for x, y in p]
            for p in g.flatten(fn(), win)]


def overflow(polys, patch_w, patch_h):
    """Copper leaving its own patch would eat into the gutter or the pads."""
    pts = [p for poly in polys for p in poly]
    if not pts:
        return []
    slack = max(
        max(-min(x for x, _ in pts), max(x for x, _ in pts) - patch_w),
        max(-min(y for _, y in pts), max(y for _, y in pts) - patch_h),
    )
    if slack <= 1e-6:
        return []
    return [f"copper reaches {slack:.2f} mm outside its patch"]


def build():
    """Return the placed geometry plus a per-patch verification report."""
    chosen = [(name, fn) for _, name, _, fn in pp.PATTERNS if name not in SKIP]
    slots = list(cells())
    if len(chosen) > len(slots):
        raise SystemExit(
            f"{len(chosen)} patterns will not fit in {len(slots)} cells; "
            f"add names to SKIP"
        )

    placed, labels, report = [], [], []
    for (name, fn), (n, (x, y, pw, ph), label_at) in zip(chosen, slots):
        polys = patch_geometry(fn, pw, ph)
        problems = g.verify(polys, size=(pw, ph))
        problems += overflow(polys, pw, ph)
        report.append((n + 1, name, problems))
        placed += [[(px + x, py + y) for px, py in p] for p in polys]
        labels.append((n + 1, name, label_at))
    return placed, labels, report


# --- KiCad output -------------------------------------------------------------

INFO = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0 Transitional//EN">
<HTML>
<HEAD>
<META HTTP-EQUIV="CONTENT-TYPE" CONTENT="text/html; charset=utf-8">
<TITLE>3U 28HP relief texture test panel</TITLE>
</HEAD>
<BODY>
<P>3U 28HP blank, 141.9 x 128.5 mm, carrying {count} relief textures in a
{cols} x {rows} grid so they can be compared by hand on one board.</P>
<P>Each patch is copper on F.Cu with the soldermask left closed over it, so the
panel stays one colour and the patterns read as a 35 um raised relief. Order
2 oz outer copper to double the step to 70 um. The index number under each
patch is also copper; the legend is on the back silkscreen.</P>
<P>Nothing is milled through the board and all four mounting pads are intact.</P>
</BODY>
</HTML>
"""

NOTE_TOP = "RELIEF TEXTURE TEST - 3U 28HP"
NOTE_BOTTOM = ("Copper under closed black soldermask, ENIG. "
               "2 oz outer copper gives 70 um relief.")


# Mirrored left-justified text grows towards -X, so these are the right-hand
# ends of each column. Listed so the first column reads leftmost from the back.
LEGEND_COLS = (124.0, 84.0, 44.0)


def legend(labels):
    """Back-silkscreen legend, three columns of ten entries."""
    out = [g.gr_text(NOTE_TOP, (BW / 2, 11.0), "B.SilkS",
                     size=2.6, thickness=0.4, justify="mirror")]
    per_col = math.ceil(len(labels) / len(LEGEND_COLS))
    for idx, (number, name, _) in enumerate(labels):
        col, row = idx // per_col, idx % per_col
        y = 24.0 + row * 8.6
        out.append(g.gr_text(f"{number:02d} {name}", (LEGEND_COLS[col], y),
                             "B.SilkS", size=1.7, thickness=0.28,
                             justify="left mirror"))
    out.append(g.gr_text(NOTE_BOTTOM, (BW / 2, 120.0), "B.SilkS",
                         size=1.5, thickness=0.25, justify="mirror"))
    return out


def write_panel(polys, labels):
    (OUT / "meta").mkdir(parents=True, exist_ok=True)

    base_pcb = (BASE / f"{BASE.name}.kicad_pcb").read_text()
    stackup = g.STACKUP.format(copper=g.COPPER_MM,
                               core=1.6 - 2 * g.COPPER_MM - 0.02)
    base_pcb = base_pcb.replace("  (setup\n", f"  (setup\n{stackup}", 1)

    body = [base_pcb.rsplit("  (gr_rect", 1)[0]]
    body.append(
        f'  (gr_rect (start 0 0) (end {BW} {BH}) (layer "Edge.Cuts") '
        f'(width 0.1) (fill none) (tstamp {uuid.uuid4()}))\n\n'
    )
    for x0, y0, x1, y1 in g.pour_bands(BW, BH, HOLES):
        body.append(g.gr_poly(pp.rect(x0, y0, x1 - x0, y1 - y0), "B.Cu"))
    body.append("\n")
    for poly in polys:
        body.append(g.gr_poly(poly, "F.Cu"))
    for number, _, at in labels:
        body.append(g.gr_text(f"{number:02d}", at, "F.Cu",
                              size=1.9, thickness=0.3))
    body.extend(legend(labels))
    body.append("\n)\n")

    (OUT / f"{OUT.name}.kicad_pcb").write_text("".join(body))
    shutil.copy(BASE / f"{BASE.name}.kicad_pro", OUT / f"{OUT.name}.kicad_pro")
    shutil.copy(BASE / "meta" / "icon.png", OUT / "meta" / "icon.png")
    (OUT / "meta" / "info.html").write_text(
        INFO.format(count=len(labels), cols=COLS, rows=ROWS)
    )


# --- preview ------------------------------------------------------------------

def write_preview(polys, labels, path, px_per_mm=7.0):
    """Draw the whole sheet the way the emboss contact sheets are drawn."""
    style = pp.MODES["emboss"]
    shapes = [("poly", p) for p in polys]

    parts = [f'<rect x="0" y="0" width="{BW}" height="{BH}" '
             f'fill="{style["panel"]}"/>']
    for dx, dy, color in style["faces"]:
        parts.append(f'<g transform="translate({dx},{dy})">')
        parts.extend(pp.shape_svg(s, color) for s in shapes)
        for number, _, (lx, ly) in labels:
            parts.append(
                f'<text x="{pp.fmt(lx)}" y="{pp.fmt(ly + 0.7)}" fill="{color}" '
                f'font-family="Helvetica,Arial,sans-serif" font-size="2" '
                f'text-anchor="middle">{number:02d}</text>'
            )
        parts.append("</g>")
    for hx, hy in HOLES:
        parts.append(
            f'<rect x="{pp.fmt(hx - 2.75)}" y="{pp.fmt(hy - 1.85)}" '
            f'width="5.5" height="3.7" rx="1.85" fill="{style["hole_rim"]}"/>'
        )
        parts.append(
            f'<rect x="{pp.fmt(hx - 2.5)}" y="{pp.fmt(hy - 1.6)}" '
            f'width="5" height="3.2" rx="1.6" fill="{pp.SHEET_BG}"/>'
        )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{pp.fmt(BW * px_per_mm)}" height="{pp.fmt(BH * px_per_mm)}" '
        f'viewBox="0 0 {BW} {BH}">'
        f'<rect width="100%" height="100%" fill="{pp.SHEET_BG}"/>'
        f'{"".join(parts)}</svg>'
    )
    path.write_text(svg)
    png = path.with_suffix(".png")
    subprocess.run(["rsvg-convert", "-o", str(png), str(path)], check=True)
    return png


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()

    polys, labels, report = build()

    failed = [r for r in report if r[2]]
    print(f"{len(report) - len(failed)}/{len(report)} patches pass\n")
    for number, name, problems in report:
        flag = "ok  " if not problems else "FAIL"
        detail = "" if not problems else "  <- " + "; ".join(problems)
        print(f"  {flag} {number:02d} {name:<16}{detail}")

    if failed:
        raise SystemExit("\nfix the failing patches before writing the panel")

    if not args.check_only:
        write_panel(polys, labels)
        print(f"\n{OUT}")

    pp.OUT_DIR.mkdir(exist_ok=True)
    png = write_preview(polys, labels, pp.OUT_DIR / "testsheet_28HP.svg")
    print(f"{png}")


if __name__ == "__main__":
    main()
