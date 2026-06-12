"""Ingestion worker: polls the inbox, processes files sequentially, classifies pending SKUs.

Run as a background thread inside the API container (started from main.py).
Correctness over speed — strictly sequential, no parallelism.
"""
import logging
import re
import shutil
import threading
import time
import traceback
from pathlib import Path

from PIL import Image

from . import config, db, pipeline
from .classify import classify_views

log = logging.getLogger("ringfinder.worker")

# In-memory ingest activity feed for the admin UI (newest first, capped).
RECENT: list[dict] = []
_recent_lock = threading.Lock()


def _record(file: str, sku: str | None, status: str) -> None:
    with _recent_lock:
        RECENT.insert(0, {"file": file, "sku": sku, "status": status, "ts": db.now_iso()})
        del RECENT[60:]


def _fail(path: Path, reason: str) -> None:
    config.FAILED_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.FAILED_DIR / path.name
    if dest.exists():
        dest = config.FAILED_DIR / f"{path.stem}_{int(time.time())}{path.suffix}"
    shutil.move(str(path), dest)
    dest.with_name(dest.name + ".log.txt").write_text(f"{db.now_iso()}\n{path.name}\n{reason}\n")
    _record(path.name, None, "failed")
    log.warning("failed %s: %s", path.name, reason)


def process_file(path: Path, forced_type: str | None = None,
                 sku_override: str | None = None) -> None:
    """Ingest one inbox file. forced_type: 'sheet' | 'single' | None (auto-detect).
    sku_override: set when the file came from a ring folder (folder name = SKU),
    which groups any number of files into one ring regardless of filenames."""
    if sku_override:
        sku = re.sub(r"[^A-Za-z0-9_-]+", "-", sku_override).strip("-")
    else:
        sku = pipeline.extract_sku(path.stem)
    if not sku:
        _fail(path, f"No SKU match for regex {config.SKU_REGEX!r} on filename stem {path.stem!r}. "
                    "Rename the file so it starts with the SKU — or put all of this "
                    "ring's images in a folder named after the SKU and drop the folder.")
        return

    try:
        img = Image.open(path)
        img.load()
    except Exception as e:
        _fail(path, f"Could not read image: {e}")
        return

    _record(path.name, sku, "processing")

    dividers = pipeline.find_sheet_dividers(img)
    if forced_type == "sheet":
        is_sheet = True          # dividers may be None -> midline fallback
    elif forced_type == "single":
        is_sheet = False
    else:
        is_sheet = dividers is not None

    views = pipeline.split_quadrants(img, dividers) if is_sheet else [img]
    source_type = "sheet_quadrant" if is_sheet else "single"

    db.upsert_sku(sku)
    views_dir = config.LIBRARY_DIR / sku / "views"
    views_dir.mkdir(parents=True, exist_ok=True)

    new_paths: list[tuple[Path, Image.Image]] = []
    for i, v in enumerate(views):
        suffix = f"_q{i}" if is_sheet else ""
        out_path = views_dir / f"{path.stem}{suffix}.png"
        rel = str(out_path.relative_to(config.DATA_DIR))
        if db.view_exists(rel):
            continue  # idempotent: re-dropping the same file adds nothing
        processed = pipeline.preprocess_view(v)
        processed.save(out_path)
        new_paths.append((out_path, processed))

    if new_paths:
        embeddings = pipeline.embed_images([im for _, im in new_paths])
        for (out_path, _), emb in zip(new_paths, embeddings):
            db.add_view(sku, str(out_path.relative_to(config.DATA_DIR)), source_type, emb)

    if new_paths:
        local_tag_sku(sku)

    # Whole-CAD display image (unclipped, browser-safe JPEG) — shown as the
    # suggested match. Built from the original in memory, so HEIC/webp/etc. all
    # render. Sheets win over singles as the SKU's representative image.
    try:
        disp_dir = config.LIBRARY_DIR / sku / "display"
        disp_dir.mkdir(parents=True, exist_ok=True)
        disp_path = disp_dir / f"{path.stem}.jpg"
        pipeline.make_display(img).save(disp_path, "JPEG", quality=90)
        db.set_display_path(sku, str(disp_path.relative_to(config.DATA_DIR)), overwrite=is_sheet)
    except Exception:
        log.exception("display image generation failed for %s", sku)

    originals_dir = config.LIBRARY_DIR / sku / "originals"
    originals_dir.mkdir(parents=True, exist_ok=True)
    dest = originals_dir / path.name
    if dest.exists():
        dest = originals_dir / f"{path.stem}_{int(time.time())}{path.suffix}"
    shutil.move(str(path), dest)
    _record(path.name, sku, "done")
    log.info("ingested %s -> %s (%d new views)", path.name, sku, len(new_paths))


def local_tag_sku(sku: str) -> None:
    """Zero-shot tags from the SKU's own view embeddings — free, local, instant.
    Status stays 'pending' so API classification upgrades these tags whenever a
    key is available; until then they power the attribute re-rank on searches.
    Never overwrites API ('done') tags."""
    if not config.LOCAL_ATTRS:
        return
    try:
        row = db.get_sku(sku)
        if row is None or row["tags_status"] == "done":
            return
        import numpy as np

        from . import local_attrs
        embs = db.sku_embeddings(sku)
        if embs.size == 0:
            return
        mean = embs.mean(axis=0)
        mean = mean / (np.linalg.norm(mean) or 1.0)
        tags = {k: v for k, v in local_attrs.detect_attributes(mean).items()
                if not k.startswith("_")}
        if tags:
            db.set_tags(sku, tags, "pending")
    except Exception:
        log.exception("local tagging failed for %s", sku)


def classify_pending(limit: int | None = None) -> int:
    """Classify SKUs with no tags yet — one API call per SKU, up to 4 best views."""
    if not config.CLASSIFY_ENABLED or not config.ANTHROPIC_API_KEY:
        return 0
    done = 0
    for sku in db.pending_skus()[: limit or 1_000_000]:
        view_rows = db.sku_views(sku)
        if not view_rows:
            continue
        paths = [config.DATA_DIR / r["file_path"] for r in view_rows[:4]]
        try:
            tags, usage = classify_views(paths)
            db.set_tags(sku, tags, "done")
            db.log_api_usage("sku_classify", sku, config.CLASSIFY_MODEL, usage)
            done += 1
            log.info("classified %s (%d in / %d out tokens)",
                     sku, usage["input_tokens"], usage["output_tokens"])
        except Exception as e:  # API failure: keep pending, retry next cycle
            log.warning("classification failed for %s (will retry): %s", sku, e)
    return done


RESERVED_DIRS = {"sheets", "singles"}


def _stable_scan(pending_sizes: dict) -> list[tuple[Path, str | None, str | None]]:
    """Return (path, forced_type, sku_override) for files whose size is
    unchanged since the last poll (fully written).

    Inbox layout:
      inbox/*.png            -> SKU from filename (SKU_REGEX)
      inbox/sheets/*.png     -> forced Type A, SKU from filename
      inbox/singles/*.png    -> forced Type B, SKU from filename
      inbox/<RING>/*.png     -> ring folder: ALL files group under SKU <RING>,
                                type auto-detected per file. Use this when a
                                ring's views are a bunch of arbitrarily-named
                                single files.
    """
    candidates: list[tuple[Path, str | None, str | None]] = []
    scan: list[tuple[Path, str | None, str | None]] = [
        (config.INBOX_DIR, None, None),
        (config.INBOX_DIR / "sheets", "sheet", None),
        (config.INBOX_DIR / "singles", "single", None),
    ]
    if config.INBOX_DIR.exists():
        for d in sorted(config.INBOX_DIR.iterdir()):
            if d.is_dir() and d.name not in RESERVED_DIRS:
                scan.append((d, None, d.name))
    ready = []
    for folder, forced, sku_override in scan:
        if not folder.exists():
            continue
        for p in sorted(folder.iterdir()):
            if not p.is_file() or p.suffix.lower() not in config.IMAGE_EXTS:
                continue
            candidates.append((p, forced, sku_override))
    seen = set()
    for p, forced, sku_override in candidates:
        key = str(p)
        seen.add(key)
        size = p.stat().st_size
        if pending_sizes.get(key) == size:
            ready.append((p, forced, sku_override))
            pending_sizes.pop(key, None)
        else:
            pending_sizes[key] = size
    for key in list(pending_sizes):
        if key not in seen:
            del pending_sizes[key]
    return ready


def _cleanup_ring_folders() -> None:
    """Remove emptied ring folders so the inbox reads as 'caught up'."""
    if not config.INBOX_DIR.exists():
        return
    for d in config.INBOX_DIR.iterdir():
        if d.is_dir() and d.name not in RESERVED_DIRS:
            try:
                next(d.iterdir())
            except StopIteration:
                d.rmdir()
            except OSError:
                pass


def run_forever(stop_event: threading.Event | None = None) -> None:
    config.ensure_dirs()
    db.init_db()
    pending_sizes: dict[str, int] = {}
    log.info("ingestion worker started (poll every %ss)", config.INBOX_POLL_SECONDS)
    while stop_event is None or not stop_event.is_set():
        try:
            for path, forced, sku_override in _stable_scan(pending_sizes):
                try:
                    process_file(path, forced, sku_override)
                except Exception:
                    _fail(path, "Unexpected error during processing:\n" + traceback.format_exc())
            _cleanup_ring_folders()
            classify_pending()
        except Exception:
            log.exception("worker cycle error")
        if stop_event is not None:
            stop_event.wait(config.INBOX_POLL_SECONDS)
        else:
            time.sleep(config.INBOX_POLL_SECONDS)


def start_in_background() -> threading.Event:
    stop = threading.Event()
    t = threading.Thread(target=run_forever, args=(stop,), daemon=True, name="ingest-worker")
    t.start()
    return stop
