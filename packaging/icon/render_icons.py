#!/usr/bin/env python3
"""Rasterize the GrokDeck icon set from one SVG.

The artwork is packaging/icon/grokdeck.svg, the rounded-corner mark.
This script does not redraw it and does not overwrite that file.
Every size is a raster of the same SVG via rsvg-convert.

Outputs
  <out>/<size>/grokdeck.png
  packaging/grokdeck-icon.png          256, or --icon-256
  shell/electron/grokdeck-icon.png     512, or --electron-icon

Usage
  python3 packaging/icon/render_icons.py --out DIR [--hicolor THEME]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ALL_SIZES = (16, 24, 32, 48, 64, 128, 256, 512, 1024)


def rsvg_file_to_png(svg: Path, out_png: Path, size: int) -> None:
    rsvg = shutil.which("rsvg-convert")
    if not rsvg:
        raise SystemExit("rsvg-convert not found. Install librsvg2-tools.")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [rsvg, "-w", str(size), "-h", str(size), "-o", str(out_png), str(svg)],
        check=True,
    )


def main() -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(here / "rendered"),
                    help="directory to write <size>/grokdeck.png sets into")
    ap.add_argument("--source", default=str(here / "grokdeck.svg"),
                    help="SVG to rasterize. Defaults to packaging/icon/grokdeck.svg")
    ap.add_argument("--svg-only", action="store_true",
                    help="print the source SVG and exit without rasterizing")
    ap.add_argument("--hicolor", help="also install into this hicolor theme dir")
    ap.add_argument("--electron-icon",
                    default=str(here.parent.parent / "shell" / "electron" / "grokdeck-icon.png"))
    ap.add_argument("--icon-256", default=str(here.parent / "grokdeck-icon.png"),
                    help="where to write the 256px PNG (defaults to packaging/grokdeck-icon.png)")
    args = ap.parse_args()

    source = Path(args.source)
    if not source.is_file():
        raise SystemExit(f"icon source missing: {source}")
    print(f"  svg    {source}")
    if args.svg_only:
        return 0

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for size in ALL_SIZES:
        png = out / str(size) / "grokdeck.png"
        rsvg_file_to_png(source, png, size)
        print(f"  png    {png}  ({png.stat().st_size} bytes)")

    icon_256 = Path(args.icon_256)
    icon_256.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(out / "256" / "grokdeck.png", icon_256)
    electron = Path(args.electron_icon)
    electron.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(out / "512" / "grokdeck.png", electron)
    print(f"  copy   {icon_256} (256)")
    print(f"  copy   {electron} (512)")

    if args.hicolor:
        theme = Path(args.hicolor)
        for size in ALL_SIZES:
            dest = theme / f"{size}x{size}" / "apps"
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out / str(size) / "grokdeck.png", dest / "grokdeck.png")
        scalable = theme / "scalable" / "apps"
        scalable.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, scalable / "grokdeck.svg")
        print(f"  hicolor {theme} ({len(ALL_SIZES)} sizes + svg)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
