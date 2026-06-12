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

# Image processing
VIEW_SIZE = 512          # longest side of derived view images
QUERY_RESIZE = 384       # downscale query images before rembg to stay under the latency target
# Type A (4-up CAD sheet) detection — env-tunable so the window can be adjusted
# against real sheets without rebuilding. Real Sierra West sheets measured ~1.6-1.7;
# single beauty renders are ~1.33 (4:3), so the windows don't overlap.
SHEET_AR_MIN = float(os.environ.get("SHEET_AR_MIN", "1.38"))
SHEET_AR_MAX = float(os.environ.get("SHEET_AR_MAX", "1.95"))
SHEET_LINE_STD = float(os.environ.get("SHEET_LINE_STD", "12.0"))  # max std for a "uniform" divider band
QUADRANT_INSET = float(os.environ.get("QUADRANT_INSET", "0.06"))  # crop each quadrant inward per edge

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# Model weight caches live under /data/models so container recreation doesn't re-download.
os.environ.setdefault("HF_HOME", str(MODELS_DIR / "hf"))
os.environ.setdefault("U2NET_HOME", str(MODELS_DIR / "u2net"))


def ensure_dirs() -> None:
    for d in (INBOX_DIR, INBOX_DIR / "sheets", INBOX_DIR / "singles",
              LIBRARY_DIR, FAILED_DIR, QUERIES_DIR, DB_PATH.parent, MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)
