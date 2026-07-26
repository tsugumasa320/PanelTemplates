#!/usr/bin/env python3
"""Run DRC and pack a fab-ready Gerber + drill zip for a panel project.

Needs the KiCad command line tool, 8.0 or newer. Set KICAD_CLI if it lives
somewhere the search below does not cover.

    python3 _export_gerbers.py                       # the relief test sheet
    python3 _export_gerbers.py relief/1U_..._Ribs    # any panel folder
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "gerbers"
DEFAULT = ROOT / "testsheet" / "3U_28HP_ReliefTest"

LAYERS = "F.Cu,B.Cu,F.Mask,B.Mask,F.SilkS,B.SilkS,Edge.Cuts"

CLI_CANDIDATES = (
    "/Applications/KiCad8/KiCad.app/Contents/MacOS/kicad-cli",
    "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
    "/usr/bin/kicad-cli",
)

NOTES_HEAD = """Fabrication notes for {name}

Board            {size}, 2 layers, 1.6 mm FR4
Soldermask       {mask}
Silkscreen       white
Surface finish   {finish}
"""

# Only true of the relief panels, where the copper is decorative and covered.
NOTES_RELIEF = """Outer copper     2 oz (70 um) preferred. The texture is copper relief under
                 closed soldermask, so copper weight is what sets how deep it
                 feels. 1 oz (35 um) also works, at half the depth.

Two things about this board are deliberate and should not be "corrected":

- The soldermask has no openings except over the mounting pads. The pattern is
  meant to stay covered; that is what keeps the panel a single colour and lets
  the copper read as relief.
- The copper is unconnected artwork with no net. There is no circuit on this
  board, the copper only exists to form the texture.
"""

NOTES_PADS = """
This is a mechanical panel with no circuit on it. The only copper is the plated
mounting pads, so the copper layers look nearly empty. That is not a mistake or
a missing file. Everything else is on Edge.Cuts, including the module cutout.
Please quote it as a plain 2 layer board.
"""

NOTES_BARE = """
This is a mechanical panel with no circuit on it, so the copper layers are
empty and there is nothing to drill. That is not a mistake or a missing file.
Everything is on Edge.Cuts, including the mounting slots, which are milled
rather than drilled. Please quote it as a plain 2 layer board rather than
rejecting it for having no copper.
"""

NOTES_TAIL = """
Gerbers are Gerber X2 with Protel file extensions.{drill}
"""

NOTES_DRILL = "\nDrill file is Excellon in mm, absolute origin, slots routed."


def kicad_cli():
    env = os.environ.get("KICAD_CLI")
    if env and Path(env).exists():
        return env
    found = shutil.which("kicad-cli")
    if found:
        return found
    for path in CLI_CANDIDATES:
        if Path(path).exists():
            return path
    raise SystemExit(
        "kicad-cli not found; install KiCad 8 or newer, or point KICAD_CLI at it"
    )


def run(cli, *args):
    result = subprocess.run([cli, *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(
            f"kicad-cli {' '.join(args[:3])} failed:\n{result.stderr.strip()}"
        )
    return result.stdout


def drc(cli, pcb, work):
    """Report DRC violations by severity, without treating them as fatal.

    A blank panel is all no-net copper artwork, so KiCad flags things that do
    not apply here. The counts are still worth seeing before ordering.
    """
    report = work / "drc.json"
    run(cli, "pcb", "drc", "--format", "json", "--severity-all",
        "-o", str(report), str(pcb))
    data = json.loads(report.read_text())

    groups = {}
    for key in ("violations", "unconnected_items", "schematic_parity"):
        for item in data.get(key) or []:
            rule = item.get("type", key)
            groups.setdefault((item.get("severity", "?"), rule), 0)
            groups[(item.get("severity", "?"), rule)] += 1

    if not groups:
        print("  DRC: clean")
        return
    for (severity, rule), count in sorted(groups.items()):
        print(f"  DRC: {severity:<9} {rule:<28} x{count}")


def board_size(text):
    """Extent of the Edge.Cuts outline, whatever primitives it is drawn with."""
    xs, ys = [], []
    for line in text.splitlines():
        if 'layer "Edge.Cuts"' not in line:
            continue
        for key in ("(start ", "(end ", "(mid ", "(center "):
            for part in line.split(key)[1:]:
                nums = part.split(")", 1)[0].split()
                if len(nums) == 2:
                    try:
                        xs.append(float(nums[0]))
                        ys.append(float(nums[1]))
                    except ValueError:
                        pass
    if not xs:
        return "see Edge.Cuts"
    return f"{max(xs) - min(xs):.4g} x {max(ys) - min(ys):.4g} mm"


def notes(pcb):
    """Write the notes the board actually warrants.

    The relief panels need the fab told that bare-looking copper under closed
    mask is intentional; the plain converter panels have no copper at all and
    need the opposite warning.
    """
    text = pcb.read_text()
    # gr_poly on a panel means decorative copper; pads alone do not count.
    decorative = "gr_poly" in text
    pads = "thru_hole" in text

    body = NOTES_HEAD.format(
        name=pcb.stem,
        size=board_size(text),
        mask="black, both sides" if '(color "Black")' in text else "your choice",
        finish="ENIG" if '(copper_finish "ENIG")' in text else "your choice",
    )
    if decorative:
        body += NOTES_RELIEF
    else:
        body += NOTES_PADS if pads else NOTES_BARE
    return body + NOTES_TAIL.format(drill=NOTES_DRILL if pads else "")


def export(folder):
    pcb = folder / f"{folder.name}.kicad_pcb"
    if not pcb.exists():
        raise SystemExit(f"no .kicad_pcb in {folder}")

    cli = kicad_cli()
    OUT.mkdir(exist_ok=True)
    print(f"{folder.name}")

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        # kicad-cli rewrites the .kicad_pro of any project it opens, so run it
        # against a copy and leave the template in the repo untouched.
        scratch = work / folder.name
        shutil.copytree(folder, scratch)
        source = scratch / pcb.name

        drc(cli, source, work)

        plot = work / "plot"
        plot.mkdir()
        run(cli, "pcb", "export", "gerbers", "--layers", LAYERS,
            "-o", f"{plot}/", str(source))
        run(cli, "pcb", "export", "drill", "--format", "excellon",
            "--excellon-units", "mm", "--excellon-oval-format", "route",
            "--drill-origin", "absolute", "-o", f"{plot}/", str(source))

        (plot / "ORDER_NOTES.txt").write_text(notes(pcb))

        zip_path = OUT / f"{folder.name}_gerbers.zip"
        files = sorted(p for p in plot.iterdir() if p.is_file())
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for path in files:
                z.write(path, path.name)

    for name in (p.name for p in files):
        print(f"  + {name}")
    print(f"  -> {zip_path.relative_to(ROOT)} "
          f"({zip_path.stat().st_size / 1024:.0f} kB)")
    return zip_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folders", nargs="*", help="panel project folders")
    args = ap.parse_args()

    targets = [Path(f).resolve() for f in args.folders] or [DEFAULT]
    for folder in targets:
        export(folder)


if __name__ == "__main__":
    main()
