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

    # Query-photo classification runs in parallel with preprocessing/embedding —
    # the API call is network-bound while rembg/CLIP are CPU-bound.
    classify_future = None
    if config.QUERY_CLASSIFY and config.CLASSIFY_ENABLED and config.ANTHROPIC_API_KEY:
        from concurrent.futures import ThreadPoolExecutor

        from .classify import classify_query_image
        _executor = ThreadPoolExecutor(max_workers=1)
        classify_future = _executor.submit(classify_query_image, img)
        _executor.shutdown(wait=False)

    cleaned = pipeline.preprocess_query(img)
    vec = pipeline.embed_images([cleaned])[0]
    flt = _filters(metal_color, center_stone_shape, setting_type)

    query_tags = None
    if classify_future is not None:
        try:
            query_tags = classify_future.result(timeout=20)
        except Exception as e:
            log.warning("query classification skipped: %s", e)

    if query_tags:
        # Wider candidate pool, then blend in attribute agreement.
        results = search.search_by_embedding(vec, flt, top_k=36)
        results = search.rerank_with_query_tags(results, query_tags,
                                                config.QUERY_RERANK_WEIGHT)
    else:
        results = search.search_by_embedding(vec, flt)

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
            "query_tags": query_tags}


@app.get("/api/sku/{sku}")
def sku_detail(sku: str):
    row = db.get_sku(sku)
    if row is None:
        return JSONResponse({"error": "unknown sku"}, status_code=404)
    views = [{"id": v["id"], "image": v["file_path"], "source_type": v["source_type"]}
             for v in db.sku_views(sku)]
    tags = search._tags_of(row)
    return {"sku": sku, "name": row["name"], "price": row["price"],
            "tags": tags, "tags_status": row["tags_status"], "views": views}


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
            "failed": failed[:20]}


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
