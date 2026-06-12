# RingFinder — Sierra West Jewelers

Self-hosted ring catalog visual search. Staff drop 3D render images into an inbox
folder; the system extracts the SKU, removes backgrounds, generates CLIP image
embeddings (local, offline), classifies attributes via the Anthropic API, and
serves a LAN web app where staff can:

- **Search by photo** — upload a customer's photo of a ring, get the 12 closest catalog matches
- **Search by text** — "vintage marquise yellow gold"

Runs as a single Docker container on a low-power Windows mini PC. CPU-only.

---

## Setup (Windows + Docker Desktop)

1. **Install Docker Desktop** from <https://www.docker.com/products/docker-desktop/>.
   During setup keep the default **WSL 2** backend. Reboot if asked.
2. **Get this folder onto the mini PC** (git clone or copy the project directory),
   e.g. to `C:\ringfinder`.
3. **Create the `.env` file**: copy `.env.example` to `.env` (same folder as
   `docker-compose.yml`) and fill in:
   - `ANTHROPIC_API_KEY` — for attribute tagging. Leave empty to run
     embeddings-only; tagging queues until a key is added.
   - `SKU_REGEX` — **confirm this against your real filenames** (see below).
4. **Start it**: open a terminal in the project folder and run

   ```
   docker compose up -d --build
   ```

   The first run downloads the CLIP and rembg model weights (~600 MB) into
   `data/models/`. They are cached there — recreating the container does NOT
   re-download.
5. **Open the app**: on any device on the LAN, browse to
   `http://<mini-pc-ip>:8420` (find the IP with `ipconfig` on the mini PC).
   Tip: give the mini PC a fixed IP in the router so the bookmark never breaks.

The container restarts automatically with Docker Desktop
(`restart: unless-stopped`) — enable *"Start Docker Desktop when you sign in"*
in Docker Desktop settings.

## How staff use it

**Adding rings:** drop render files (`.jpg`, `.png`, `.webp`) into
`C:\ringfinder\data\inbox\`. That's the only folder staff touch. Within a poll
cycle (15s default) each file is processed and moved to
`data/library/<SKU>/originals/`; the inbox stays empty when caught up.

- The SKU is read from the start of the filename: `SW-2841_views.jpg` → `SW-2841`.
- 4-up CAD sheets (2×2 grid of orthographic views) are detected automatically
  and split into 4 view images. If detection ever guesses wrong, force it:
  files dropped into `data/inbox/sheets/` are always treated as 4-up sheets,
  `data/inbox/singles/` always as single renders.
- Files that can't be processed land in `data/failed/` with a `*.log.txt`
  explaining why (also shown on the admin page).
- Re-dropping a file for an existing SKU adds views — it never duplicates.

**Searching:** type a description, or tap **Photo** / drag a customer photo onto
the home screen. Image results carry a similarity hallmark; tap a card for the
detail view and "Find similar rings".

**Admin page** (gear icon, top right): counts, recent ingests, failed files,
"Run classification now", and pricing CSV import (columns: `sku`, optional
`price`, `name`; unmatched rows are reported, never fatal).

## Backup

Everything lives in one folder: **`data/`** (originals, processed views, the
SQLite database, model caches). Copy that folder anywhere and you have a full
backup. Restore = put it back and `docker compose up -d`.

## Configuration (.env)

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(empty)* | Key for attribute classification (`claude-sonnet-4-6`). Empty → SKUs marked `tags_pending`, search still works. |
| `PORT` | `8420` | Host port for the web app. |
| `SKU_REGEX` | `^([A-Za-z0-9-]+)` | Capture group 1 = SKU, applied to the filename stem. **Confirm with real filenames.** |
| `INBOX_POLL_SECONDS` | `15` | Inbox scan interval. |
| `CLASSIFY_ENABLED` | `true` | `false` → embeddings-only; classification stays queued. |
| `LOCAL_ATTRS` | `true` | Local zero-shot attribute read on query photos (CLIP text prompts — no API). |
| `QUERY_RERANK_WEIGHT` | `0.25` | Strength of the bonus-only attribute re-rank on image search. |
| `SHEET_AR_MIN/MAX` | `1.5/1.9` | Aspect-ratio window for 4-up sheet detection. |
| `PRICE_IN_PER_MTOK` / `PRICE_OUT_PER_MTOK` | `3.0` / `15.0` | $/M tokens for the admin cost estimate (catalog tagging only). |

> **⚠ Confirm before bulk ingestion:** the default `SKU_REGEX` grabs the leading
> run of letters/digits/dashes — `SW-2841_beauty.png` → `SW-2841`, but
> `SW 2841.png` → `SW`. Send a handful of real filenames and adjust the regex
> in `.env` first. The Type A sheet detector (landscape 1.6–1.8 aspect ratio +
> grid midlines) was likewise tuned on synthetic sheets — verify it on a few
> real CAD sheets, and use `inbox/sheets/` / `inbox/singles/` as the manual
> override either way.

## API

| Endpoint | Description |
|---|---|
| `POST /api/search/image` | multipart `file` + optional `metal_color`, `center_stone_shape`, `setting_type`. Top 12 by max cosine over view embeddings. |
| `GET /api/search/text?q=` | hybrid CLIP-text + keyword score, same filters. |
| `GET /api/filters` | closed taxonomy options for the three filters. |
| `GET /api/sku/{sku}` | views, tags, price/name for one SKU. |
| `GET /api/admin/status` | counts, recent ingests, failed files. |
| `POST /api/admin/classify` | run pending classifications now. |
| `POST /api/admin/pricing` | upload pricing CSV. |

## Architecture

- **One container**: FastAPI (uvicorn) + a background ingestion thread.
- **Image search is 100% local — zero API tokens, ever.** Like a reverse image
  search: rembg → white background → CLIP embedding (plus a tighter center crop
  so small-in-frame rings on fingers still match), brute-force cosine against
  all catalog views. The query's coarse attributes (stone shape, metal,
  setting) are also read locally — zero-shot, by comparing the image embedding
  against CLIP *text* prompts — and confident detections give agreeing SKUs a
  bonus-only re-rank boost (never penalizing a strong visual match). The admin
  page shows API spend; image searches contribute $0.00 by construction.
- **Embeddings**: `open_clip` ViT-B-32 (`laion2b_s34b_b79k`), CPU, L2-normalized,
  stored as float32 BLOBs in SQLite. Brute-force numpy cosine — correct and fast
  at ~1k SKUs / ~5k views; no vector DB.
- **Background removal**: `rembg` with the lightweight `u2netp` model. Query
  images are downscaled to 384px before rembg to keep image search under the
  ~4s CPU target.
- **Classification**: one Anthropic API call per SKU (up to 4 views,
  `claude-sonnet-4-6`), closed-taxonomy JSON, parsed defensively. API failures
  leave the SKU `pending` and retry on the next worker cycle.
- **Models load once** per process: eagerly in the worker on first ingest,
  lazily in the API on first query. Never per-request.

## Tests

```
pip install -r requirements.txt pytest httpx
python -m pytest tests/ -q
```

The suite stubs the heavy models, so it runs anywhere in seconds. It covers the
pipeline (SKU regex, sheet detection/split, preprocess), ingestion (acceptance
tests 1–3: sheet → 4 views, idempotent re-drop, failed-file handling), search
math and API endpoints (acceptance 4 & 6), pricing import, and defensive
classification parsing.

### Manual acceptance checklist (on the real box)

1. ☐ Drop a 4-up CAD sheet `SKU123_views.jpg` in `data/inbox/` → within 60s:
   4 images in `data/library/SKU123/views/`, tags populated, inbox empty.
2. ☐ Drop a beauty render for the same SKU → 1 more view, **no** second
   classification call (tags already done).
3. ☐ Drop `random photo.png` (no SKU) → lands in `data/failed/` with a log file.
4. ☐ Image-search with one of the indexed renders → that SKU is #1, similarity > 0.95.
5. ☐ Image-search with a phone photo of a ring → 12 results, no crash, < 6s.
6. ☐ Text-search "vintage marquise yellow gold" → marquise/yellow-gold/vintage
   SKUs rank top.
7. ☐ `docker compose restart` → index intact, no re-download, no re-classification.
8. ☐ Remove `ANTHROPIC_API_KEY` from `.env`, `docker compose up -d` → ingestion
   still works (SKUs `tags_pending`), search still works.

## Repo layout

```
app/            FastAPI app, worker, pipeline, search, classification
static/         single-page UI (vanilla JS, no build step)
tests/          pytest suite (models stubbed)
ringfinder/     original Claude Design prototype (reference)
docker-compose.yml, Dockerfile, .env.example
data/           (created at runtime, volume-mounted at /data)
```
