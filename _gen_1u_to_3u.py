#!/usr/bin/env python3
"""Build horizontal Intellijel 1U-to-3U converter panels.

Three 1U rows in a 3U blank. Each row has a body window with a capsule slot
above and below it. The top slot of the top row and the bottom slot of the
bottom row sit on the 3U rail centres (Y = 3 / 125.5), so the same milled
slots mount both the adapter to the rack and the outer 1U modules — the way
commercial horizontal adapters are built. There are no separate plated rail
pads.

Side rails are about 1 HP each: an N HP panel holds (N - 2) HP of 1U modules
per row.

    python3 _gen_1u_to_3u.py            # 8HP and 18HP
    python3 _gen_1u_to_3u.py 10 18      # any panel HP that has a 3U blank
"""
from __future__ import annotations

import argparse
import re
import shutil
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent

BH = 128.5
U1 = 39.65                 # Intellijel 1U panel height
SLOT_SPAN = 33.65          # Intellijel mounting-hole spacing
SLOT_H = 3.2               # M3 oval / capsule height
WINDOW_H = 22.0            # body cutout; leaves ~4.2 mm webs to the slots
EDGE = 0.1

# Top and bottom rows flush with the 3U outline so their outer slots land on
# the rail centres. The leftover (128.5 - 3*39.65 = 9.55 mm) is split evenly
# into the two gaps between rows.
ROWS = [0.0, (BH - U1) / 2, BH - U1]   # 0 / 44.425 / 88.85


def uid():
    return str(uuid.uuid4())


def capsule_h(x0, x1, cy, h=SLOT_H):
    """Horizontal capsule on Edge.Cuts, centres of the round ends at x0 / x1."""
    r = h / 2
    return "".join([
        f'  (gr_line (start {x0 + r:.4f} {cy - r:.4f}) '
        f'(end {x1 - r:.4f} {cy - r:.4f}) (layer "Edge.Cuts") '
        f'(width {EDGE}) (tstamp {uid()}))\n',
        f'  (gr_arc (start {x1 - r:.4f} {cy - r:.4f}) '
        f'(mid {x1:.4f} {cy:.4f}) (end {x1 - r:.4f} {cy + r:.4f}) '
        f'(layer "Edge.Cuts") (width {EDGE}) (tstamp {uid()}))\n',
        f'  (gr_line (start {x1 - r:.4f} {cy + r:.4f}) '
        f'(end {x0 + r:.4f} {cy + r:.4f}) (layer "Edge.Cuts") '
        f'(width {EDGE}) (tstamp {uid()}))\n',
        f'  (gr_arc (start {x0 + r:.4f} {cy + r:.4f}) '
        f'(mid {x0:.4f} {cy:.4f}) (end {x0 + r:.4f} {cy - r:.4f}) '
        f'(layer "Edge.Cuts") (width {EDGE}) (tstamp {uid()}))\n',
    ])


def blank_geom(hp):
    """Width and (hp-2) capacity width from the matching 3U blank."""
    folder = ROOT / f"3U_{hp:02d}HP_Blank"
    pcb = folder / f"{folder.name}.kicad_pcb"
    if not pcb.exists():
        raise SystemExit(f"no blank for {hp}HP at {folder}")
    text = pcb.read_text()
    width = float(re.search(
        r'gr_rect \(start 0 0\) \(end ([0-9.]+)', text
    ).group(1))
    cap_hp = hp - 2
    cap_folder = ROOT / f"3U_{cap_hp:02d}HP_Blank"
    if not cap_folder.exists():
        cap_w = cap_hp * 5.08
    else:
        cap_w = float(re.search(
            r'gr_rect \(start 0 0\) \(end ([0-9.]+)',
            (cap_folder / f"{cap_folder.name}.kicad_pcb").read_text(),
        ).group(1))
    return folder, width, cap_hp, cap_w


def strip_pads(pcb_text):
    """Remove plated mounting footprints; outer slots take their place."""
    return re.sub(
        r'\n  \(footprint "" \(layer "F\.Cu"\).*?\n  \)\n',
        '\n',
        pcb_text,
        flags=re.DOTALL,
    )


def build(hp):
    base, width, cap_hp, cap_w = blank_geom(hp)
    side = (width - cap_w) / 2
    x0, x1 = side, width - side

    out = ROOT / f"1U_to_3U_Intellijel_{hp:02d}HP"
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(base, out)
    for src in list(out.iterdir()):
        if src.is_file() and src.name.startswith(base.name):
            src.rename(out / src.name.replace(base.name, out.name, 1))

    pcb_path = out / f"{out.name}.kicad_pcb"
    pcb = strip_pads(pcb_path.read_text())
    head, _, rest = pcb.partition('(gr_rect (start 0 0)')
    outer_end = rest.index(")\n") + 2
    outer = "(gr_rect (start 0 0)" + rest[:outer_end]
    body = [head + outer]

    for y0 in ROWS:
        cy_top = y0 + (U1 - SLOT_SPAN) / 2          # = y0 + 3.0
        cy_bot = cy_top + SLOT_SPAN
        win_y0 = cy_top + SLOT_H / 2 + (SLOT_SPAN - SLOT_H - WINDOW_H) / 2
        win_y1 = win_y0 + WINDOW_H
        body.append(
            f'  (gr_rect (start {x0:.4f} {win_y0:.4f}) '
            f'(end {x1:.4f} {win_y1:.4f}) (layer "Edge.Cuts") '
            f'(width {EDGE}) (fill none) (tstamp {uid()}))\n'
        )
        body.append(capsule_h(x0, x1, cy_top))
        body.append(capsule_h(x0, x1, cy_bot))

    body.append(")\n")
    pcb_path.write_text("".join(body))

    (out / "meta" / "info.html").write_text(f"""<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0 Transitional//EN">
<HTML>
<HEAD>
<META HTTP-EQUIV="CONTENT-TYPE" CONTENT="text/html; charset=utf-8">
<TITLE>1U to 3U Intellijel {hp}HP</TITLE>
</HEAD>
<BODY>
<P>Horizontal 1U-to-3U converter, {hp}HP x 3U ({width} x {BH} mm). Three
Intellijel 1U rows, each holding up to {cap_hp}HP of modules. Per row: a
{cap_w:.1f} x {WINDOW_H:.0f} mm body window with a {SLOT_H} mm capsule slot
above and below (side rails {side:.2f} mm / ~1HP each).</P>
<P>The top row's upper slot and the bottom row's lower slot sit on the 3U rail
centres (Y = 3 / 125.5), so the same milled slots mount the adapter to the
rack and the outer modules. No separate plated rail pads. Screw+nut.</P>
</BODY>
</HTML>
""")

    top = ROWS[0] + 3.0
    bot = ROWS[2] + 3.0 + SLOT_SPAN
    print(
        f"{out.name}: {width} x {BH} mm, "
        f"window/slots X {x0:.2f}–{x1:.2f} ({cap_w:.1f} mm = {cap_hp}HP), "
        f"side {side:.2f} mm, outer slots Y {top:.1f} / {bot:.1f} (= 3U rails)"
    )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("hps", nargs="*", type=int, default=[8, 18])
    args = ap.parse_args()
    for hp in args.hps:
        build(hp)


if __name__ == "__main__":
    main()
