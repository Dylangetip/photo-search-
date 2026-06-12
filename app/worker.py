"""Ingestion worker: polls the inbox, processes files sequentially, classifies pending SKUs.

Run as a background thread inside the API container (started from main.py).
Correctness over speed — strictly sequential, no parallelism.
"""
import logging
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


def process_file(path: Path, forced_type: str | None = None) -> None:
    """Ingest one inbox file. forced_type: 'sheet' | 'single' | None (auto-detect)."""
    sku = pipeline.extract_sku(path.stem)
    if not sku:
        _fail(path, f"No SKU match for regex {config.SKU_REGEX!r} on filename stem {path.stem!r}. "
                    "Rename the file so it starts with the SKU and re-drop.")
        return

    try:
        img = Image.open(path)
        img.load()
    except Exception as e:
        _fail(path, f"Could not read image: {e}")
        return

    _record(path.name, sku, "processing")

    if forced_type == "sheet":
        is_sheet = True
    elif forced_type == "single":
        is_sheet = False
    else:
        is_sheet = pipeline.detect_sheet(img)

    views = pipeline.split_quadrants(img) if is_sheet else [img]
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

    originals_dir = config.LIBRARY_DIR / sku / "originals"
    originals_dir.mkdir(parents=True, exist_ok=True)
    dest = originals_dir / path.name
    if dest.exists():
        dest = originals_dir / f"{path.stem}_{int(time.time())}{path.suffix}"
    shutil.move(str(path), dest)
    _record(path.name, sku, "done")
    log.info("ingested %s -> %s (%d new views)", path.name, sku, len(new_paths))


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


def _stable_scan(pending_sizes: dict) -> list[tuple[Path, str | None]]:
    """Return files whose size is unchanged since the last poll (fully written)."""
    candidates: list[tuple[Path, str | None]] = []
    scan = [(config.INBOX_DIR, None),
            (config.INBOX_DIR / "sheets", "sheet"),
            (config.INBOX_DIR / "singles", "single")]
    ready = []
    for folder, forced in scan:
        if not folder.exists():
            continue
        for p in sorted(folder.iterdir()):
            if not p.is_file() or p.suffix.lower() not in config.IMAGE_EXTS:
                continue
            candidates.append((p, forced))
    seen = set()
    for p, forced in candidates:
        key = str(p)
        seen.add(key)
        size = p.stat().st_size
        if pending_sizes.get(key) == size:
            ready.append((p, forced))
            pending_sizes.pop(key, None)
        else:
            pending_sizes[key] = size
    for key in list(pending_sizes):
        if key not in seen:
            del pending_sizes[key]
    return ready


def run_forever(stop_event: threading.Event | None = None) -> None:
    config.ensure_dirs()
    db.init_db()
    pending_sizes: dict[str, int] = {}
    log.info("ingestion worker started (poll every %ss)", config.INBOX_POLL_SECONDS)
    while stop_event is None or not stop_event.is_set():
        try:
            for path, forced in _stable_scan(pending_sizes):
                try:
                    process_file(path, forced)
                except Exception:
                    _fail(path, "Unexpected error during processing:\n" + traceback.format_exc())
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
