#!/usr/bin/env python3
"""Turn the repeating-geometry pattern library into relief-textured KiCad panels.

Nothing is milled through the board. The texture comes from the two layers a
plain PCB process already gives you for free:

    emboss (default)  pattern on F.Cu, soldermask left closed over everything.
                      The copper stands 35 um proud (70 um if you order 2 oz),
                      so the pattern is a single-colour raised relief that you
                      can feel and that catches the light.

    expose            solid copper pour with the pattern opened in F.Mask.
                      The pattern becomes bare plated metal against matte
                      mask: strong gloss and colour contrast, almost no step.

Both styles keep the board a normal 1.6 mm blank with all four mounting pads
intact, so the panel costs exactly what an unpatterned blank costs.

    python3 _gen_relief.py [--style emboss|expose] [--check-only] [--only Name]
"""
from __future__ import annotations

import argparse
import math
import shutil
import uuid
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

import _preview_patterns as pp

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "1U_Intellijel_08HP_Blank"
OUT = ROOT / "relief"

W, H = pp.W, pp.H

EDGE_CLEAR = 0.3   # keep copper this far from the board outline
MASK_CLEAR = 0.5   # keep mask openings inside the copper pour
ARC_SAG = 0.02     # chord flattening tolerance for arcs

COPPER_MIN = 0.15  # narrowest copper feature any fab will hold
GAP_MIN = 0.15     # narrowest gap between neighbouring copper features
PPM = 30           # raster resolution for verification (px per mm)

PAD = (5.5, 3.7)   # mounting pad, matching the blank templates
PAD_CLEAR = 0.5

STYLES = ("emboss", "expose")


# --- clipping -----------------------------------------------------------------

def clip_polygon(pts, rect):
    """Sutherland-Hodgman clip of a polygon against an axis-aligned rect."""
    x0, y0, x1, y1 = rect
    edges = (
        lambda p: p[0] >= x0, lambda p: p[0] <= x1,
        lambda p: p[1] >= y0, lambda p: p[1] <= y1,
    )
    axes = ((0, x0), (0, x1), (1, y0), (1, y1))
    out = list(pts)
    for inside, (axis, bound) in zip(edges, axes):
        if not out:
            return []
        clipped = []
        for a, b in zip(out, out[1:] + out[:1]):
            ain, bin_ = inside(a), inside(b)
            if ain:
                clipped.append(a)
            if ain != bin_:
                span = b[axis] - a[axis]
                t = 0.0 if abs(span) < 1e-12 else (bound - a[axis]) / span
                clipped.append(
                    (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                )
        out = clipped
    return out


def pour_bands(bw, bh, holes, inset=EDGE_CLEAR):
    """Rectangles that together cover the board but keep clear of the pads.

    One rectangle would swallow the mounting pads, which DRC reads as both a
    clearance and a soldermask bridge violation. Five bands notch out the four
    corners without needing polygon subtraction.
    """
    xs = sorted({x for x, _ in holes})
    ys = sorted({y for _, y in holes})
    hx, hy = PAD[0] / 2 + PAD_CLEAR, PAD[1] / 2 + PAD_CLEAR
    x0, x1 = xs[0] - hx, xs[0] + hx
    x2, x3 = xs[-1] - hx, xs[-1] + hx
    y1, y2 = ys[0] + hy, ys[-1] - hy

    lo, hi_x, hi_y = inset, bw - inset, bh - inset
    bands = [
        (lo, lo, x0, hi_y),      # left of the pads, full height
        (x3, lo, hi_x, hi_y),    # right of the pads, full height
        (x0, y1, x3, y2),        # between the pad rows
        (x1, lo, x2, y1),        # above, between the pad columns
        (x1, y2, x2, hi_y),      # below, between the pad columns
    ]
    out = []
    for bx0, by0, bx1, by1 in bands:
        bx0, by0 = max(bx0, lo), max(by0, lo)
        bx1, by1 = min(bx1, hi_x), min(by1, hi_y)
        if bx1 - bx0 > 0.2 and by1 - by0 > 0.2:
            out.append((bx0, by0, bx1, by1))
    return out


# --- pattern to layer geometry ------------------------------------------------

def arc_points(c, r, a1, a2):
    """Flatten an arc into a polyline within ARC_SAG of the true curve."""
    if r <= 1e-6:
        return [c]
    ratio = max(-1.0, min(1.0, 1.0 - ARC_SAG / r))
    step = max(2.0, min(30.0, math.degrees(2 * math.acos(ratio))))
    n = max(2, int(math.ceil(abs(a2 - a1) / step)))
    return [pp.on_circle(c, r, a1 + (a2 - a1) * k / n) for k in range(n + 1)]


def dedupe(pts, eps=1e-6):
    """Drop repeated vertices, which clipping leaves wherever a corner of the
    shape happens to land exactly on the clip line."""
    out = []
    for p in pts:
        if not out or math.dist(p, out[-1]) > eps:
            out.append(p)
    while len(out) > 1 and math.dist(out[0], out[-1]) <= eps:
        out.pop()
    return out


def too_thin(pts):
    """True for slivers a fab cannot hold.

    For an elongated shape area/perimeter approaches half its width, which is a
    cheap stand-in for measuring the width of an arbitrary clipped fragment.
    """
    perimeter = sum(math.dist(a, b) for a, b in zip(pts, pts[1:] + pts[:1]))
    if perimeter < 1e-9:
        return True
    return abs(pp.signed_area(pts)) / perimeter < COPPER_MIN / 2


def capsule(a, b, width, steps=6):
    """Closed contour of a stroked segment, rounded at both ends.

    Strokes become real outlines rather than wide lines, so a stroke that runs
    off the edge of the clip window is cut flat at the window instead of
    bulging half a linewidth past it.
    """
    h = width / 2
    ex, ey = b[0] - a[0], b[1] - a[1]
    base = math.degrees(math.atan2(ey, ex))
    pts = [pp.on_circle(b, h, base - 90 + k * 180 / steps)
           for k in range(steps + 1)]
    pts += [pp.on_circle(a, h, base + 90 + k * 180 / steps)
            for k in range(steps + 1)]
    return pts


def flatten(shapes, rect):
    """Turn the pattern into filled polygons clipped to `rect`.

    Overlapping polygons are fine: copper and soldermask artwork unions, so
    shared edges and stroke joins need no special handling.
    """
    polys = []

    def add_poly(pts):
        clipped = dedupe(clip_polygon(pts, rect))
        if len(clipped) >= 3 and not too_thin(clipped):
            polys.append(clipped)

    def add_stroke(points, width):
        h = width / 2
        for a, b in zip(points, points[1:]):
            if math.dist(a, b) < 1e-9:
                continue
            if (max(a[0], b[0]) + h < rect[0] or min(a[0], b[0]) - h > rect[2]
                    or max(a[1], b[1]) + h < rect[1]
                    or min(a[1], b[1]) - h > rect[3]):
                continue
            add_poly(capsule(a, b, width))

    for shape in shapes:
        kind = shape[0]
        if kind == "poly":
            add_poly(shape[1])
        elif kind == "polyline":
            add_stroke(shape[1], shape[2])
        elif kind == "circle":
            c, r, width = shape[1], shape[2], shape[3]
            add_stroke(arc_points(c, r, 0, 360), width)
        elif kind == "arc":
            c, r, a1, a2, width = shape[1], shape[2], shape[3], shape[4], shape[5]
            add_stroke(arc_points(c, r, a1, a2), width)
        else:
            raise ValueError(kind)
    return polys


def build(fn, style, clear_pads=False):
    inset = EDGE_CLEAR if style == "emboss" else MASK_CLEAR
    shapes = fn()
    if not clear_pads:
        return flatten(shapes, (inset, inset, W - inset, H - inset))
    # Clipping to the pad-avoiding bands instead of the whole board leaves the
    # mounting pads bare, which costs a notch of pattern but keeps DRC quiet.
    out = []
    for band in pour_bands(W, H, pp.HOLES, inset):
        out += flatten(shapes, band)
    return out


# --- verification -------------------------------------------------------------

def raster(polys, size=None):
    """Render the copper pattern as a bitmap for feature-size checks."""
    w_mm, h_mm = size or (W, H)
    img = Image.new("L", (int(w_mm * PPM) + 1, int(h_mm * PPM) + 1), 0)
    draw = ImageDraw.Draw(img)
    for poly in polys:
        draw.polygon([(x * PPM, y * PPM) for x, y in poly], fill=255)
    return img


def _disc_offsets(r):
    return [(dx, dy)
            for dx in range(-r, r + 1) for dy in range(-r, r + 1)
            if dx * dx + dy * dy <= r * r and (dx or dy)]


def erode(img, r):
    if r <= 0:
        return img
    w, h = img.size
    padded = Image.new("L", (w + 2 * r, h + 2 * r), 0)
    padded.paste(img, (r, r))
    out = padded
    for dx, dy in _disc_offsets(r):
        out = ImageChops.darker(out, ImageChops.offset(padded, dx, dy))
    return out.crop((r, r, r + w, r + h))


def dilate(img, r):
    if r <= 0:
        return img
    out = img
    for dx, dy in _disc_offsets(r):
        out = ImageChops.lighter(out, ImageChops.offset(img, dx, dy))
    return out


def area(img):
    return sum(img.histogram()[128:])


def verify(polys, size=None):
    """Flag copper features or gaps that a standard 5 mil process cannot hold.

    Morphological opening deletes anything thinner than the kernel and cannot
    bring it back, so a drop in area means the pattern has sub-minimum detail.
    Closing the inverse does the same for the gaps between features.
    """
    img = raster(polys, size)
    r = max(1, int(round(COPPER_MIN / 2 * PPM)))
    problems = []

    copper = area(img)
    if copper == 0:
        return ["pattern is empty"]
    if area(dilate(erode(img, r), r)) < copper * 0.98:
        problems.append(f"copper thinner than {COPPER_MIN} mm")

    inverse = ImageChops.invert(img)
    gap = area(inverse)
    rg = max(1, int(round(GAP_MIN / 2 * PPM)))
    if gap and area(dilate(erode(inverse, rg), rg)) < gap * 0.98:
        problems.append(f"gap narrower than {GAP_MIN} mm")

    coverage = copper / (img.size[0] * img.size[1])
    if not 0.08 < coverage < 0.94:
        problems.append(f"copper coverage {coverage:.0%} is out of range")
    return problems


# --- KiCad output -------------------------------------------------------------

INFO = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0 Transitional//EN">
<HTML>
<HEAD>
<META HTTP-EQUIV="CONTENT-TYPE" CONTENT="text/html; charset=utf-8">
<TITLE>1U Intellijel 8HP {name} relief panel</TITLE>
</HEAD>
<BODY>
<P>Intellijel 1U 8HP blank, 40.3 x 39.65 mm, textured with a repeating
<B>{name}</B> pattern ({note}).</P>
<P>{blurb}</P>
<P>Nothing is milled through the board and all four mounting pads are intact,
so this costs the same as an untextured blank.</P>
</BODY>
</HTML>
"""

BLURB = {
    "emboss": "The pattern is copper on F.Cu with the soldermask left closed "
              "over it, so the panel stays one colour and the pattern reads as "
              "a 35 um raised relief. Order 2 oz outer copper to double the "
              "step to 70 um.",
    "expose": "The panel is a solid copper pour with the pattern opened in "
              "F.Mask, so the pattern comes out as bare plated metal against "
              "matte soldermask.",
}


# Spelling the stackup out tells the fab black mask over ENIG and makes the
# KiCad 3D view show the relief with the right colours.
STACKUP = """    (stackup
      (layer "F.SilkS" (type "Top Silk Screen"))
      (layer "F.Mask" (type "Top Solder Mask") (color "Black") (thickness 0.01))
      (layer "F.Cu" (type "copper") (thickness {copper}))
      (layer "dielectric 1" (type "core") (thickness {core}) (material "FR4")
        (epsilon_r 4.5) (loss_tangent 0.02))
      (layer "B.Cu" (type "copper") (thickness {copper}))
      (layer "B.Mask" (type "Bottom Solder Mask") (color "Black") (thickness 0.01))
      (layer "B.SilkS" (type "Bottom Silk Screen"))
      (copper_finish "ENIG")
      (dielectric_constraints no)
    )
"""

COPPER_MM = 0.035  # 1 oz; ask for 2 oz to double the relief




def gr_poly(pts, layer):
    coords = " ".join(f"(xy {x:.4f} {y:.4f})" for x, y in pts)
    return (f'  (gr_poly (pts {coords}) (layer "{layer}") (width 0) '
            f'(fill solid) (tstamp {uuid.uuid4()}))\n')


def gr_text(text, at, layer, size=1.5, thickness=0.25, justify=""):
    """Stroke-font text. Back layers need `mirror` to read the right way round."""
    just = f" (justify {justify})" if justify else ""
    return (f'  (gr_text "{text}" (at {at[0]:.4f} {at[1]:.4f}) '
            f'(layer "{layer}") (tstamp {uuid.uuid4()})\n'
            f'    (effects (font (size {size} {size}) '
            f'(thickness {thickness})){just})\n  )\n')


def write_panel(name, note, polys, style):
    folder = OUT / f"1U_Intellijel_08HP_Relief_{name}"
    (folder / "meta").mkdir(parents=True, exist_ok=True)

    base_pcb = (BASE / f"{BASE.name}.kicad_pcb").read_text()
    stackup = STACKUP.format(copper=COPPER_MM, core=1.6 - 2 * COPPER_MM - 0.02)
    base_pcb = base_pcb.replace(
        "  (setup\n", f"  (setup\n{stackup}", 1
    )
    body = [base_pcb.rsplit("  (gr_rect", 1)[0]]
    body.append(
        f'  (gr_rect (start 0 0) (end {W} {H}) (layer "Edge.Cuts") '
        f'(width 0.1) (fill none) (tstamp {uuid.uuid4()}))\n\n'
    )

    # Back copper stays solid so the two sides balance and the board keeps flat.
    pour = [pp.rect(x0, y0, x1 - x0, y1 - y0)
            for x0, y0, x1, y1 in pour_bands(W, H, pp.HOLES)]
    for rect in pour:
        body.append(gr_poly(rect, "B.Cu"))

    pattern_layer = "F.Cu" if style == "emboss" else "F.Mask"
    if style == "expose":
        for rect in pour:
            body.append(gr_poly(rect, "F.Cu"))
    body.append("\n")
    for poly in polys:
        body.append(gr_poly(poly, pattern_layer))
    body.append("\n)\n")

    (folder / f"{folder.name}.kicad_pcb").write_text("".join(body))
    shutil.copy(BASE / f"{BASE.name}.kicad_pro", folder / f"{folder.name}.kicad_pro")
    shutil.copy(BASE / "meta" / "icon.png", folder / "meta" / "icon.png")
    (folder / "meta" / "info.html").write_text(
        INFO.format(name=name, note=note, blurb=BLURB[style])
    )
    return folder


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--style", default="emboss", choices=STYLES)
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--clear-pads", action="store_true",
                    help="leave the mounting pads bare instead of running the "
                         "pattern over them, which makes KiCad DRC clean")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    wanted = {n.strip() for n in args.only.split(",") if n.strip()}

    # Only a full run owns the output directory; --only just refreshes a panel.
    if not args.check_only and not wanted and OUT.exists():
        shutil.rmtree(OUT)

    results = []
    sheets = {1: [], 2: []}
    for batch, name, note, fn in pp.PATTERNS:
        if wanted and name not in wanted:
            continue
        polys = build(fn, args.style, args.clear_pads)
        problems = verify(polys)
        results.append((name, len(polys), problems))
        if not problems and not args.check_only:
            write_panel(name, note, polys, args.style)
        label = name if not problems else f"{name}  [FAIL]"
        sheets[batch].append((label, note, [("poly", p) for p in polys]))

    ok = [r for r in results if not r[2]]
    print(f"{len(ok)}/{len(results)} panels pass  (style={args.style})\n")
    for name, n_poly, problems in results:
        flag = "ok  " if not problems else "FAIL"
        detail = "" if not problems else "  <- " + "; ".join(problems)
        print(f"  {flag} {name:<16} {n_poly:5d} poly{detail}")

    # Draw the geometry that was actually emitted, not the abstract pattern.
    pp.OUT_DIR.mkdir(exist_ok=True)
    for batch, entries in sheets.items():
        if not entries:
            continue
        stem = f"relief_{args.style}_b{batch}"
        png = pp.contact_sheet(entries, args.style, pp.OUT_DIR / f"{stem}.svg")
        print(f"\n{png}")


if __name__ == "__main__":
    main()
