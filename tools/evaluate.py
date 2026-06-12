#!/usr/bin/env python3
"""Batch-evaluate image search: run every photo in a folder through the
running RingFinder server and print the top matches per query.

Made for the "ring requests" workflow — point it at the folder of customer-style
photos and see what the catalog returns for each, in one shot:

    python tools/evaluate.py "C:\\Users\\Bwilde\\Desktop\\ring requests"
    python tools/evaluate.py "C:\\Users\\Bwilde\\Desktop\\ring requests" --url http://localhost:8420 --top 5

Requires only the Python standard library. The server must be running and the
catalog ingested (drop the CADs in data/inbox first and let the worker finish).
"""
import argparse
import json
import mimetypes
import sys
import time
import urllib.request
import uuid
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def post_image(url: str, path: Path) -> dict:
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/api/search/image",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", help="folder of query photos (e.g. the 'ring requests' folder)")
    ap.add_argument("--url", default="http://localhost:8420", help="RingFinder server URL")
    ap.add_argument("--top", type=int, default=5, help="matches to show per query")
    args = ap.parse_args()

    folder = Path(args.folder)
    queries = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTS) \
        if folder.is_dir() else []
    if not queries:
        sys.exit(f"No images found in {folder}")

    print(f"{len(queries)} queries -> {args.url}\n")
    times = []
    for q in queries:
        t0 = time.time()
        try:
            data = post_image(args.url, q)
        except Exception as e:
            print(f"{q.name:36s}  ERROR: {e}")
            continue
        dt = time.time() - t0
        times.append(dt)
        results = data.get("results", [])
        tags = data.get("query_tags") or {}
        detected = ", ".join(f"{k.split('_')[0]}={v}" for k, v in tags.items()
                             if not k.startswith("_")) or "none"
        print(f"{q.name}   ({dt:.1f}s, detected: {detected})")
        for i, r in enumerate(results[: args.top]):
            attr = f"  attrs✓{r['attr_match']:.2f}" if "attr_match" in r else ""
            name = f"  {r['name']}" if r.get("name") else ""
            print(f"   {i + 1}. {r['sku']:16s} {r['score'] * 100:5.1f}%{attr}{name}")
        if not results:
            print("   (no results — is the catalog ingested?)")
        print()

    if times:
        print(f"avg {sum(times) / len(times):.1f}s / search · "
              f"slowest {max(times):.1f}s · all local, 0 tokens")


if __name__ == "__main__":
    main()
