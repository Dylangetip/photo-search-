#!/usr/bin/env python3
"""Download all images from a PUBLIC ("anyone with the link") Google Drive
folder — the testing on-ramp for RingFinder. Standard library only.

    python tools/pull_drive.py "https://drive.google.com/drive/folders/<ID>" ./out
    python tools/pull_drive.py <FOLDER_ID> ./out

Files are saved with their Drive names. HEIC/large files are handled via the
usercontent download host. For private folders this won't work — share the
folder as "Anyone with the link" first.
"""
import re
import sys
import urllib.request
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
UA = {"User-Agent": "Mozilla/5.0"}


def folder_id(arg: str) -> str:
    m = re.search(r"/folders/([\w-]+)", arg)
    return m.group(1) if m else arg


def list_folder(fid: str) -> list[tuple[str, str]]:
    url = f"https://drive.google.com/embeddedfolderview?id={fid}#list"
    html = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read().decode("utf-8", "replace")
    ids = re.findall(r'id="entry-([\w-]+)"', html)
    names = re.findall(r'class="flip-entry-title">([^<]+)<', html)
    return list(zip(ids, names))


def download(fid: str, dest: Path) -> bool:
    for url in (f"https://drive.google.com/uc?export=download&id={fid}",
                f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t"):
        try:
            data = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120).read()
            if data[:200].lower().lstrip().startswith(b"<!doctype html") or b"<html" in data[:200].lower():
                continue  # got the confirm page; try the other host
            dest.write_bytes(data)
            return True
        except Exception:
            continue
    return False


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    fid = folder_id(sys.argv[1])
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "./drive_pull")
    out.mkdir(parents=True, exist_ok=True)

    entries = [(i, n) for i, n in list_folder(fid) if Path(n).suffix.lower() in IMAGE_EXTS]
    print(f"{len(entries)} images in folder {fid} -> {out}")
    ok = 0
    for i, name in entries:
        if download(i, out / name):
            ok += 1
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}")
    print(f"\n{ok}/{len(entries)} downloaded")


if __name__ == "__main__":
    main()
