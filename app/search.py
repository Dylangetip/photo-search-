"""Search: brute-force cosine similarity over all view embeddings (numpy),
scored per SKU as max over its views. Text search blends CLIP text similarity
50/50 with simple keyword matching against tags_json.
"""
import json
import re
import threading

import numpy as np

from . import db

_cache_lock = threading.Lock()
_cache = {"sig": None, "mat": None, "meta": None}

FILTER_FIELDS = ("metal_color", "center_stone_shape", "setting_type")

FILTER_OPTIONS = {
    "metal_color": ["yellow_gold", "white_gold", "rose_gold", "two_tone", "platinum_look", "other"],
    "center_stone_shape": ["round", "oval", "marquise", "pear", "emerald", "cushion",
                           "princess", "radiant", "asscher", "heart", "none", "other"],
    "setting_type": ["solitaire", "halo", "hidden_halo", "three_stone", "cluster",
                     "bezel", "tension", "eternity", "other"],
}


def _matrix():
    sig = db.views_signature()
    with _cache_lock:
        if _cache["sig"] != sig:
            mat, meta = db.all_embeddings()
            _cache.update(sig=sig, mat=mat, meta=meta)
        return _cache["mat"], _cache["meta"]


def _tags_of(row) -> dict:
    if row is None or row["tags_json"] is None:
        return {}
    try:
        return json.loads(row["tags_json"])
    except (json.JSONDecodeError, TypeError):
        return {}


def _passes_filters(tags: dict, filters: dict) -> bool:
    for field, want in filters.items():
        if want and tags.get(field) != want:
            return False
    return True


def _best_per_sku(scores: np.ndarray, meta: list[tuple[str, str]]) -> dict[str, tuple[float, str]]:
    """sku -> (max score, file_path of best view)."""
    best: dict[str, tuple[float, str]] = {}
    for s, (sku, path) in zip(scores, meta):
        if sku not in best or s > best[sku][0]:
            best[sku] = (float(s), path)
    return best


def _build_results(best: dict, filters: dict, top_k: int) -> list[dict]:
    ranked = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)
    rows = db.get_skus([sku for sku, _ in ranked])
    out = []
    for sku, (score, path) in ranked:
        row = rows.get(sku)
        tags = _tags_of(row)
        if not _passes_filters(tags, filters):
            continue
        out.append({
            "sku": sku,
            "score": round(score, 4),
            "image": path,
            "tags": tags,
            "tags_status": row["tags_status"] if row else "pending",
            "price": row["price"] if row else None,
            "name": row["name"] if row else None,
        })
        if len(out) >= top_k:
            break
    return out


def search_by_embedding(query_vecs: np.ndarray, filters: dict, top_k: int = 12) -> list[dict]:
    """query_vecs: one [d] vector or several [n, d] (multi-crop) — scored as the
    best match across crops. All vectors are L2-normalized, so dot = cosine."""
    mat, meta = _matrix()
    if mat.size == 0:
        return []
    vecs = np.atleast_2d(np.asarray(query_vecs, dtype=np.float32))
    scores = (mat @ vecs.T).max(axis=1)
    return _build_results(_best_per_sku(scores, meta), filters, top_k)


# Attribute weights for query-photo re-ranking: stone shape is the strongest
# identity signal in a messy photo, then metal color, then setting.
_AGREE_WEIGHTS = {"center_stone_shape": 0.45, "metal_color": 0.30, "setting_type": 0.25}
_UNINFORMATIVE = {None, "", "other", "none", "unclear"}


def attribute_agreement(query_tags: dict, tags: dict) -> float | None:
    """0..1 agreement between query-photo attributes and a catalog SKU's tags.
    Returns None when there is nothing informative to compare."""
    if not query_tags or not tags:
        return None
    total = got = 0.0
    for field, wt in _AGREE_WEIGHTS.items():
        q = query_tags.get(field)
        if q in _UNINFORMATIVE:
            continue
        total += wt
        if tags.get(field) == q:
            got += wt
    return got / total if total > 0 else None


def rerank_with_query_tags(results: list[dict], query_tags: dict,
                           weight: float, top_k: int = 12) -> list[dict]:
    """Boost results whose tags agree with the query's detected attributes.

    Bonus-only: score' = cos + weight * agree * (1 - cos). Agreement lifts a
    result toward 1.0; disagreement and missing tags change nothing — so a
    noisy zero-shot read can reorder close calls but can never bury a strong
    visual match (an exact render stays at ~1.0)."""
    for r in results:
        agree = attribute_agreement(query_tags, r.get("tags") or {})
        if agree is not None:
            r["score"] = round(r["score"] + weight * agree * (1.0 - r["score"]), 4)
            r["attr_match"] = round(agree, 2)
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:top_k]


def _keyword_score(query: str, row) -> float:
    """Fraction of query tokens found in the SKU's tags_json (plus sku/name)."""
    tokens = [t for t in re.split(r"[^a-z0-9]+", query.lower()) if len(t) >= 2]
    if not tokens:
        return 0.0
    hay_parts = []
    if row is not None:
        hay_parts.append((row["tags_json"] or "").lower())
        hay_parts.append((row["name"] or "").lower())
        hay_parts.append(row["sku"].lower())
    hay = " ".join(hay_parts).replace("_", " ")
    hits = sum(1 for t in tokens if t in hay)
    return hits / len(tokens)


def search_by_text(query: str, filters: dict, top_k: int = 12) -> list[dict]:
    from .pipeline import embed_text
    mat, meta = _matrix()
    if mat.size == 0:
        return []
    tvec = embed_text(query)
    clip_scores = mat @ tvec
    best = _best_per_sku(clip_scores, meta)

    rows = db.get_skus(list(best.keys()))
    # Hybrid: normalize CLIP similarity to ~0-1 and blend 50/50 with keyword match.
    blended: dict[str, tuple[float, str]] = {}
    for sku, (clip_s, path) in best.items():
        kw = _keyword_score(query, rows.get(sku))
        clip_norm = (clip_s + 1.0) / 2.0
        blended[sku] = (0.5 * clip_norm + 0.5 * kw, path)
    return _build_results(blended, filters, top_k)
