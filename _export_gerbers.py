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

NOTES = """Fabrication notes for {name}

Board            {size}, 2 layers, 1.6 mm FR4
Soldermask       black, both sides
Silkscreen       white
Surface finish   ENIG
Outer copper     2 oz (70 um) preferred. The texture is copper relief under
                 closed soldermask, so copper weight is what sets how deep it
                 feels. 1 oz (35 um) also works, at half the depth.
Holes            plated slots 5.0 x 3.2 mm for M3 mounting screws

Two things about this board are deliberate and should not be "corrected":

- The soldermask has no openings except over the mounting pads. The pattern is
  meant to stay covered; that is what keeps the panel a single colour and lets
  the copper read as relief.
- The front copper is unconnected artwork with no net. There is no circuit on
  this board, the copper only exists to form the texture.

Gerbers are Gerber X2 with Protel file extensions. Drill file is Excellon in
mm, absolute origin, slots routed.
"""


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


def board_size(pcb):
    for line in pcb.read_text().splitlines():
        if 'layer "Edge.Cuts"' in line and "gr_rect" in line:
            end = line.split("(end ", 1)[1].split(")", 1)[0].split()
            return f"{end[0]} x {end[1]} mm"
    return "see Edge.Cuts"


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

        (plot / "ORDER_NOTES.txt").write_text(
            NOTES.format(name=folder.name, size=board_size(pcb))
        )

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
