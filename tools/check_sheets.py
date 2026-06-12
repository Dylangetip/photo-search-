#!/usr/bin/env python3
"""Diagnose SKU extraction + Type A sheet detection on real files.

Point it at a folder (or files) of real CAD images and it prints, per file:
the SKU the regex would extract, the aspect ratio, whether each detection check
passes, and the final SHEET / SINGLE verdict. Use it to confirm and tune
SKU_REGEX and the sheet thresholds against your actual exports — no deployment
needed, just Python + Pillow + numpy.

    pip install pillow numpy
    python tools/check_sheets.py /path/to/cad_sheets      # expect SHEET
    python tools/check_sheets.py /path/to/single_renders  # expect SINGLE

Thresholds are read from the same env vars the app uses, so you can experiment:
    SHEET_AR_MIN=1.4 SHEET_AR_MAX=1.95 python tools/check_sheets.py ./samples
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from app import config, pipeline  # noqa: E402


def diagnose(path: Path) -> None:
    try:
        img = Image.open(path)
        img.load()
    except Exception as e:
        print(f"{path.name:40s}  ERROR: {e}")
        return

    w, h = img.size
    ar = w / h if h else 0.0
    sku = pipeline.extract_sku(path.stem)

    ar_ok = config.SHEET_AR_MIN <= ar <= config.SHEET_AR_MAX
    g = np.asarray(img.convert("L"), dtype=np.float32)
    bw, bh = max(2, int(w * 0.01)), max(2, int(h * 0.01))
    vline = pipeline._uniform_line(g[:, w // 2 - bw: w // 2 + bw], axis=0)
    hline = pipeline._uniform_line(g[h // 2 - bh: h // 2 + bh, :], axis=1)
    verdict = "SHEET " if (ar_ok and vline and hline) else "single"

    mark = lambda b: "✓" if b else "·"  # noqa: E731
    sku_str = sku or "—NO MATCH—"
    print(f"{path.name:40s}  {w}x{h:<5d} ar={ar:4.2f}  "
          f"AR{mark(ar_ok)} vline{mark(vline)} hline{mark(hline)}  "
          f"-> {verdict}   sku={sku_str}")


def main(args: list[str]) -> None:
    targets: list[Path] = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            targets += sorted(q for q in p.iterdir()
                              if q.suffix.lower() in config.IMAGE_EXTS)
        elif p.is_file():
            targets.append(p)
    if not targets:
        print("No images found. Usage: python tools/check_sheets.py <folder-or-files>")
        return
    print(f"SKU_REGEX={config.SKU_REGEX!r}  "
          f"AR window=[{config.SHEET_AR_MIN}, {config.SHEET_AR_MAX}]  "
          f"line_std<{config.SHEET_LINE_STD}\n")
    for t in targets:
        diagnose(t)
    print("\nLegend: AR/vline/hline must ALL be ✓ for SHEET. "
          "Wrong verdict? Adjust SHEET_AR_MIN/MAX or SHEET_LINE_STD env vars and re-run.\n"
          "Wrong/blank sku? Adjust SKU_REGEX in .env. "
          "Either way, inbox/sheets and inbox/singles override detection at upload time.")


if __name__ == "__main__":
    main(sys.argv[1:])
