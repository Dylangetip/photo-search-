"""SQLite storage. One file, embeddings as float32 BLOBs — brute-force cosine at this scale."""
import json
import sqlite3
import threading
from datetime import datetime, timezone

import numpy as np

from . import config

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS skus (
    sku         TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    tags_json   TEXT,
    tags_status TEXT NOT NULL DEFAULT 'pending',  -- pending | done | disabled
    price       REAL,
    name        TEXT
);
CREATE TABLE IF NOT EXISTS views (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sku         TEXT NOT NULL REFERENCES skus(sku),
    file_path   TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,                    -- sheet_quadrant | single
    embedding   BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_views_sku ON views(sku);
CREATE TABLE IF NOT EXISTS query_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT NOT NULL,
    query_image_path TEXT,
    top_skus_json    TEXT
);
CREATE TABLE IF NOT EXISTS api_usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    kind          TEXT NOT NULL,        -- query_classify | sku_classify
    ref           TEXT,                 -- sku for sku_classify; null for queries
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_tokens     INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    config.ensure_dirs()
    with _lock, connect() as conn:
        conn.executescript(SCHEMA)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_sku(sku: str) -> None:
    with _lock, connect() as conn:
        conn.execute(
            "INSERT INTO skus (sku, created_at) VALUES (?, ?) ON CONFLICT(sku) DO NOTHING",
            (sku, now_iso()),
        )


def add_view(sku: str, file_path: str, source_type: str, embedding: np.ndarray) -> bool:
    """Insert a view; returns False if this file_path is already indexed (idempotent re-drop)."""
    blob = np.asarray(embedding, dtype=np.float32).tobytes()
    with _lock, connect() as conn:
        try:
            conn.execute(
                "INSERT INTO views (sku, file_path, source_type, embedding) VALUES (?, ?, ?, ?)",
                (sku, file_path, source_type, blob),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def view_exists(file_path: str) -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1 FROM views WHERE file_path = ?", (file_path,)).fetchone() is not None


def all_embeddings() -> tuple[np.ndarray, list[tuple[str, str]]]:
    """Returns (matrix [n, d] float32, [(sku, file_path), ...]) for brute-force search."""
    with connect() as conn:
        rows = conn.execute("SELECT sku, file_path, embedding FROM views ORDER BY id").fetchall()
    if not rows:
        return np.zeros((0, 0), dtype=np.float32), []
    mat = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    return mat, [(r["sku"], r["file_path"]) for r in rows]


def views_signature() -> int:
    """Cheap cache key for the embedding matrix."""
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS c, COALESCE(MAX(id), 0) AS m FROM views").fetchone()
    return row["c"] * 1_000_003 + row["m"]


def get_sku(sku: str):
    with connect() as conn:
        return conn.execute("SELECT * FROM skus WHERE sku = ?", (sku,)).fetchone()


def get_skus(skus: list[str]) -> dict:
    if not skus:
        return {}
    ph = ",".join("?" * len(skus))
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM skus WHERE sku IN ({ph})", skus).fetchall()
    return {r["sku"]: r for r in rows}


def sku_views(sku: str) -> list:
    with connect() as conn:
        return conn.execute(
            "SELECT id, file_path, source_type FROM views WHERE sku = ? ORDER BY id", (sku,)
        ).fetchall()


def set_tags(sku: str, tags: dict | None, status: str) -> None:
    with _lock, connect() as conn:
        conn.execute(
            "UPDATE skus SET tags_json = ?, tags_status = ? WHERE sku = ?",
            (json.dumps(tags) if tags is not None else None, status, sku),
        )


def pending_skus() -> list[str]:
    with connect() as conn:
        rows = conn.execute("SELECT sku FROM skus WHERE tags_status = 'pending'").fetchall()
    return [r["sku"] for r in rows]


def set_pricing(sku: str, price: float | None, name: str | None) -> bool:
    """Left-join semantics: only updates existing SKUs. Returns True if matched."""
    with _lock, connect() as conn:
        cur = conn.execute(
            "UPDATE skus SET price = COALESCE(?, price), name = COALESCE(?, name) WHERE sku = ?",
            (price, name, sku),
        )
        return cur.rowcount > 0


def log_query(query_image_path: str | None, top_skus: list[dict]) -> None:
    with _lock, connect() as conn:
        conn.execute(
            "INSERT INTO query_log (ts, query_image_path, top_skus_json) VALUES (?, ?, ?)",
            (now_iso(), query_image_path, json.dumps(top_skus)),
        )


def log_api_usage(kind: str, ref: str | None, model: str, usage: dict) -> None:
    with _lock, connect() as conn:
        conn.execute(
            "INSERT INTO api_usage (ts, kind, ref, model, input_tokens, output_tokens,"
            " cache_read_tokens, cache_creation_tokens) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now_iso(), kind, ref, model,
             int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)),
             int(usage.get("cache_read_tokens", 0)), int(usage.get("cache_creation_tokens", 0))),
        )


def estimate_cost_usd(input_tokens: int, output_tokens: int) -> float:
    from . import config
    return (input_tokens * config.PRICE_IN_PER_MTOK
            + output_tokens * config.PRICE_OUT_PER_MTOK) / 1_000_000


def usage_stats() -> dict:
    """API token usage totals, overall and for the current UTC day, per kind."""
    today = now_iso()[:10]
    out = {"total": {}, "today": {}, "by_kind": {}}
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) calls, COALESCE(SUM(input_tokens),0) tin,"
            " COALESCE(SUM(output_tokens),0) tout FROM api_usage").fetchone()
        out["total"] = {"calls": row["calls"], "input_tokens": row["tin"],
                        "output_tokens": row["tout"],
                        "estimated_cost_usd": round(estimate_cost_usd(row["tin"], row["tout"]), 4)}
        row = conn.execute(
            "SELECT COUNT(*) calls, COALESCE(SUM(input_tokens),0) tin,"
            " COALESCE(SUM(output_tokens),0) tout FROM api_usage WHERE ts LIKE ?",
            (today + "%",)).fetchone()
        out["today"] = {"calls": row["calls"], "input_tokens": row["tin"],
                        "output_tokens": row["tout"],
                        "estimated_cost_usd": round(estimate_cost_usd(row["tin"], row["tout"]), 4)}
        for r in conn.execute(
                "SELECT kind, COUNT(*) calls, COALESCE(SUM(input_tokens),0) tin,"
                " COALESCE(SUM(output_tokens),0) tout FROM api_usage GROUP BY kind"):
            out["by_kind"][r["kind"]] = {
                "calls": r["calls"], "input_tokens": r["tin"], "output_tokens": r["tout"],
                "estimated_cost_usd": round(estimate_cost_usd(r["tin"], r["tout"]), 4)}
    return out


def stats() -> dict:
    with connect() as conn:
        skus = conn.execute("SELECT COUNT(*) AS c FROM skus").fetchone()["c"]
        views = conn.execute("SELECT COUNT(*) AS c FROM views").fetchone()["c"]
        pending = conn.execute("SELECT COUNT(*) AS c FROM skus WHERE tags_status = 'pending'").fetchone()["c"]
    failed = sum(1 for p in config.FAILED_DIR.glob("*") if p.suffix.lower() in config.IMAGE_EXTS)
    return {"skus_indexed": skus, "views_embedded": views, "tags_pending": pending, "failed_files": failed}
