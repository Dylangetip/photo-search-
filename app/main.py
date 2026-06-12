"""RingFinder API — FastAPI app serving the search endpoints, admin, media, and the static UI."""
import base64
import csv
import io
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from . import config, db, search, worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("ringfinder.api")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    stop = None
    if os.environ.get("DISABLE_WORKER", "").lower() not in ("1", "true"):
        stop = worker.start_in_background()
    yield
    if stop is not None:
        stop.set()


app = FastAPI(title="RingFinder", lifespan=lifespan)


def _filters(metal_color: str | None, center_stone_shape: str | None, setting_type: str | None) -> dict:
    return {"metal_color": metal_color or None,
            "center_stone_shape": center_stone_shape or None,
            "setting_type": setting_type or None}


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/filters")
def filters():
    return search.FILTER_OPTIONS


@app.get("/api/search/text")
def search_text(q: str = Query(..., min_length=1),
                metal_color: str | None = None,
                center_stone_shape: str | None = None,
                setting_type: str | None = None):
    results = search.search_by_text(q, _filters(metal_color, center_stone_shape, setting_type))
    return {"query": q, "results": results}


@app.post("/api/search/image")
async def search_image(file: UploadFile = File(...),
                       metal_color: str | None = None,
                       center_stone_shape: str | None = None,
                       setting_type: str | None = None):
    from . import pipeline  # lazy: model load happens on first query
    raw = await file.read()
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        return JSONResponse({"error": "Could not read the uploaded image."}, status_code=400)

    # Image search is 100% local — no API calls, no tokens. Reverse-image-search
    # style: rembg -> white bg -> CLIP over several crops (full subject, center,
    # and a stone-focused crop that isolates the ring head from a hand or a
    # stacked wedding band), scored as best-match per catalog view.
    crops = pipeline.query_crops(img)
    cleaned = crops[0]
    vecs = pipeline.embed_images(crops)
    flt = _filters(metal_color, center_stone_shape, setting_type)

    # Local zero-shot attribute read (CLIP text prompts — still no API):
    # confident fields re-rank the candidate pool by attribute agreement.
    query_tags = None
    if config.LOCAL_ATTRS:
        try:
            from . import local_attrs
            detected = local_attrs.detect_attributes(vecs[0])
            query_tags = {k: v for k, v in detected.items() if not k.startswith("_")} or None
        except Exception as e:
            log.warning("local attribute detection skipped: %s", e)

    if query_tags:
        results = search.search_by_embedding(vecs, flt, top_k=36)
        results = search.rerank_with_query_tags(results, query_tags,
                                                config.QUERY_RERANK_WEIGHT)
    else:
        results = search.search_by_embedding(vecs, flt)

    # Query log: keep the cleaned query image + top result for later tuning.
    config.QUERIES_DIR.mkdir(parents=True, exist_ok=True)
    qname = f"q_{db.now_iso().replace(':', '-')}.png"
    qpath = config.QUERIES_DIR / qname
    try:
        cleaned.save(qpath)
        db.log_query(str(qpath.relative_to(config.DATA_DIR)),
                     [{"sku": r["sku"], "score": r["score"]} for r in results[:3]])
    except Exception:
        log.exception("query logging failed")

    buf = io.BytesIO()
    cleaned.save(buf, format="PNG")
    preview = base64.standard_b64encode(buf.getvalue()).decode()
    return {"results": results,
            "query_preview": f"data:image/png;base64,{preview}",
            "query_tags": query_tags,
            "usage": None}  # image search is fully local — zero tokens, always


@app.get("/api/sku/{sku}")
def sku_detail(sku: str):
    row = db.get_sku(sku)
    if row is None:
        return JSONResponse({"error": "unknown sku"}, status_code=404)
    # Whole-CAD display images (one per original file), browser-safe JPEGs.
    displays = []
    disp_dir = config.LIBRARY_DIR / sku / "display"
    if disp_dir.exists():
        displays = [str(p.relative_to(config.DATA_DIR))
                    for p in sorted(disp_dir.iterdir())
                    if p.suffix.lower() == ".jpg"]
    main_image = row["display_path"] if row["display_path"] else (displays[0] if displays else None)
    tags = search._tags_of(row)
    return {"sku": sku, "name": row["name"], "price": row["price"],
            "tags": tags, "tags_status": row["tags_status"],
            "image": main_image, "views": displays}


@app.get("/api/admin/status")
def admin_status():
    failed = []
    if config.FAILED_DIR.exists():
        for p in sorted(config.FAILED_DIR.iterdir()):
            if p.suffix.lower() in config.IMAGE_EXTS:
                logf = p.parent / (p.name + ".log.txt")
                reason = ""
                if logf.exists():
                    lines = logf.read_text().strip().splitlines()
                    reason = lines[2] if len(lines) > 2 else (lines[-1] if lines else "")
                failed.append({"file": p.name, "reason": reason})
    return {**db.stats(),
            "classify_enabled": config.CLASSIFY_ENABLED and bool(config.ANTHROPIC_API_KEY),
            "recent": worker.RECENT[:20],
            "failed": failed[:20],
            "api_usage": db.usage_stats()}


@app.post("/api/admin/classify")
def admin_classify():
    n = worker.classify_pending()
    return {"classified": n, "still_pending": len(db.pending_skus())}


@app.post("/api/admin/pricing")
async def admin_pricing(file: UploadFile = File(...)):
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    if reader.fieldnames is None or "sku" not in [f.strip().lower() for f in reader.fieldnames]:
        return JSONResponse({"error": "CSV must have a 'sku' column."}, status_code=400)
    field_map = {f.strip().lower(): f for f in reader.fieldnames}
    matched, unmatched = 0, []
    for row in reader:
        sku = (row.get(field_map["sku"]) or "").strip()
        if not sku:
            continue
        price = None
        if "price" in field_map:
            p = (row.get(field_map["price"]) or "").strip().replace("$", "").replace(",", "")
            try:
                price = float(p) if p else None
            except ValueError:
                price = None
        name = (row.get(field_map.get("name", ""), "") or "").strip() or None
        if db.set_pricing(sku, price, name):
            matched += 1
        else:
            unmatched.append(sku)
    return {"matched": matched, "unmatched": unmatched[:200], "unmatched_count": len(unmatched)}


@app.get("/media/{path:path}")
def media(path: str):
    full = (config.DATA_DIR / path).resolve()
    if not str(full).startswith(str(config.DATA_DIR.resolve())) or not full.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(full)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
