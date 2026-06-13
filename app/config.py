"""RingFinder configuration — all values come from environment variables (.env via docker-compose)."""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))

INBOX_DIR = DATA_DIR / "inbox"
LIBRARY_DIR = DATA_DIR / "library"
FAILED_DIR = DATA_DIR / "failed"
QUERIES_DIR = DATA_DIR / "queries"
DB_PATH = DATA_DIR / "db" / "ringfinder.sqlite"
MODELS_DIR = DATA_DIR / "models"

PORT = int(os.environ.get("PORT", "8420"))

# Capture group 1 = SKU, applied to the filename stem.
# Confirm against real filenames before relying on it in production.
SKU_REGEX = os.environ.get("SKU_REGEX", r"^([A-Za-z0-9-]+)")

INBOX_POLL_SECONDS = float(os.environ.get("INBOX_POLL_SECONDS", "15"))

# When SKU_REGEX doesn't match a filename, fall back to the sanitized filename
# stem as the SKU instead of sending the file to failed/. Keeps arbitrarily
# named CADs (timestamp/IMG exports) in the catalog. Files with no usable
# characters still fail. A ring folder (folder name = SKU) always overrides.
SKU_FALLBACK_STEM = os.environ.get("SKU_FALLBACK_STEM", "true").lower() in ("1", "true", "yes")

CLASSIFY_ENABLED = os.environ.get("CLASSIFY_ENABLED", "true").lower() in ("1", "true", "yes")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLASSIFY_MODEL = os.environ.get("CLASSIFY_MODEL", "claude-sonnet-4-6")

# Image search is fully LOCAL — no API calls, no tokens, ever. To bridge messy
# queries (finger shots, Pinterest screenshots, ring stacks) to clean CAD
# renders, the query's coarse attributes are read zero-shot with CLIP text
# prompts (see local_attrs.py) and blended into the ranking.
LOCAL_ATTRS = os.environ.get("LOCAL_ATTRS", "true").lower() in ("1", "true", "yes")
QUERY_RERANK_WEIGHT = float(os.environ.get("QUERY_RERANK_WEIGHT", "0.25"))

# Pricing for cost estimates (USD per million tokens). Defaults are
# claude-sonnet-4-6 rates; override in .env if the model or prices change.
PRICE_IN_PER_MTOK = float(os.environ.get("PRICE_IN_PER_MTOK", "3.0"))
PRICE_OUT_PER_MTOK = float(os.environ.get("PRICE_OUT_PER_MTOK", "15.0"))

# CLIP model. ViT-B-32 is the default: on real finger/stack queries it gave
# better, more varied matches than ViT-L-14, which collapsed several queries
# onto one ornate multi-stone "hub" CAD. ViT-L-14 (laion2b_s32b_b82k) can be
# switched in for testing but verify it doesn't reintroduce that hubbing on
# your catalog before relying on it.
CLIP_MODEL = os.environ.get("CLIP_MODEL", "ViT-B-32")
CLIP_PRETRAINED = os.environ.get("CLIP_PRETRAINED", "laion2b_s34b_b79k")
# Mean-center embeddings before matching (hubness fix; sharper ranking).
CENTER_EMBEDDINGS = os.environ.get("CENTER_EMBEDDINGS", "true").lower() in ("1", "true", "yes")

# Image processing
VIEW_SIZE = 512          # longest side of derived view images (used for matching)
DISPLAY_SIZE = 1100      # longest side of the whole-CAD image shown in the UI
QUERY_RESIZE = 384       # downscale query images before rembg to stay under the latency target
# Stone-focused query crop: isolates the engagement ring's center stone + head
# from the hand and any stacked wedding band. Critical for finger/stack photos.
QUERY_STONE_CROP = os.environ.get("QUERY_STONE_CROP", "true").lower() in ("1", "true", "yes")
STONE_VALUE_MIN = float(os.environ.get("STONE_VALUE_MIN", "175"))   # stone is bright (HSV value >= this)
STONE_SAT_MAX = float(os.environ.get("STONE_SAT_MAX", "55"))        # ...and desaturated (white, not skin)
STONE_MIN_EXTENT_FRAC = float(os.environ.get("STONE_MIN_EXTENT_FRAC", "0.07"))  # reject tiny glints
STONE_RING_SCALE = float(os.environ.get("STONE_RING_SCALE", "3.6"))   # whole-ring box = stone extent x this
STONE_HEAD_SCALE = float(os.environ.get("STONE_HEAD_SCALE", "1.9"))   # head/stone box = stone extent x this
SKIN_DROP_FRACTION = float(os.environ.get("SKIN_DROP_FRACTION", "0.45"))  # >= this skin -> drop full crop
BAND_STONE_MASK = float(os.environ.get("BAND_STONE_MASK", "0.85"))  # mask radius (x stone extent) for band crop

# Per-role match weights — BAND is weighted highest: staff care most about
# matching the engagement ring's band/shank shape, then the stone, then overall.
WEIGHT_BAND = float(os.environ.get("WEIGHT_BAND", "0.45"))
WEIGHT_STONE = float(os.environ.get("WEIGHT_STONE", "0.30"))
WEIGHT_FULL = float(os.environ.get("WEIGHT_FULL", "0.25"))
# Type A (4-up CAD sheet) detection — env-tunable so the window can be adjusted
# against real sheets without rebuilding. Real Sierra West sheets measured ~1.6-1.7;
# single beauty renders are ~1.33 (4:3), so the windows don't overlap.
SHEET_AR_MIN = float(os.environ.get("SHEET_AR_MIN", "1.38"))
SHEET_AR_MAX = float(os.environ.get("SHEET_AR_MAX", "1.95"))
SHEET_LINE_STD = float(os.environ.get("SHEET_LINE_STD", "12.0"))  # max std for a "uniform" divider band
QUADRANT_INSET = float(os.environ.get("QUADRANT_INSET", "0.06"))  # crop each quadrant inward per edge

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}

# Model weight caches live under /data/models so container recreation doesn't re-download.
os.environ.setdefault("HF_HOME", str(MODELS_DIR / "hf"))
os.environ.setdefault("U2NET_HOME", str(MODELS_DIR / "u2net"))


def ensure_dirs() -> None:
    for d in (INBOX_DIR, INBOX_DIR / "sheets", INBOX_DIR / "singles",
              LIBRARY_DIR, FAILED_DIR, QUERIES_DIR, DB_PATH.parent, MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)
