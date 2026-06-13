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
_cache = {"sig": None, "cmat": None, "mu": None, "meta": None}


def _normalize_rows(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return m / n

FILTER_FIELDS = ("metal_color", "center_stone_shape", "setting_type")

FILTER_OPTIONS = {
    "metal_color": ["yellow_gold", "white_gold", "rose_gold", "two_tone", "platinum_look", "other"],
    "center_stone_shape": ["round", "oval", "marquise", "pear", "emerald", "cushion",
                           "princess", "radiant", "asscher", "heart", "none", "other"],
    "setting_type": ["solitaire", "halo", "hidden_halo", "three_stone", "cluster",
                     "bezel", "tension", "eternity", "other"],
}


def _matrix():
    """Returns (centered_matrix, mu, meta). Mean-centering subtracts the average
    catalog embedding so matching keys on what's DIFFERENT between rings, not the
    'ring on white' component they all share — this fixes hubness (a few generic
    CADs matching every query) and sharpens ranking, especially on hand/stack
    photos. mu is None when centering is off."""
    from . import config
    sig = db.views_signature()
    with _cache_lock:
        if _cache["sig"] != sig:
            mat, meta = db.all_embeddings()
            if mat.size and config.CENTER_EMBEDDINGS:
                mu = mat.mean(axis=0)
                mu = mu / (np.linalg.norm(mu) or 1.0)
                cmat = _normalize_rows(mat - mu)
            else:
                mu, cmat = None, mat
            _cache.update(sig=sig, cmat=cmat, mu=mu, meta=meta)
        return _cache["cmat"], _cache["mu"], _cache["meta"]


def _prep_query(vecs: np.ndarray, mu) -> np.ndarray:
    vecs = np.atleast_2d(np.asarray(vecs, dtype=np.float32))
    if mu is not None:
        vecs = _normalize_rows(vecs - mu)
    return vecs


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
        # Show the WHOLE CAD (display image) when available; fall back to the
        # matched view only if a ring has no display image yet.
        display = row["display_path"] if row and row["display_path"] else path
        out.append({
            "sku": sku,
            "score": round(score, 4),
            "image": display,
            "match_view": path,
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
    best match across crops, against the mean-centered catalog."""
    cmat, mu, meta = _matrix()
    if cmat.size == 0:
        return []
    vecs = _prep_query(query_vecs, mu)
    scores = (cmat @ vecs.T).max(axis=1).clip(0.0, 1.0)  # centered cosine can go negative
    return _build_results(_best_per_sku(scores, meta), filters, top_k)


def search_by_roles(role_vecs: dict, weights: dict, filters: dict, top_k: int = 12) -> list[dict]:
    """Score each query role (full / stone / band) separately against the
    catalog, then combine per SKU as a WEIGHTED sum of per-role best matches.
    Band is weighted highest, so band-shape agreement dominates ranking."""
    cmat, mu, meta = _matrix()
    if cmat.size == 0 or not role_vecs:
        return []
    # Per role: best cosine of (role's query crops) against every catalog view.
    sims_by_role = {}
    for role, vecs in role_vecs.items():
        if vecs is None or len(vecs) == 0:
            continue
        v = _prep_query(vecs, mu)
        sims_by_role[role] = (cmat @ v.T).max(axis=1)  # [n_views]
    if not sims_by_role:
        return []
    wsum = sum(weights.get(r, 0.0) for r in sims_by_role) or 1.0
    # Aggregate to per-SKU best per role, then weighted-combine.
    per_sku: dict[str, dict] = {}
    for i, (sku, path) in enumerate(meta):
        d = per_sku.setdefault(sku, {})
        for role, sims in sims_by_role.items():
            s = float(sims[i])
            if role not in d or s > d[role][0]:
                d[role] = (s, path)
    best: dict[str, tuple[float, str]] = {}
    for sku, roled in per_sku.items():
        score = sum(weights.get(r, 0.0) * v[0] for r, v in roled.items()) / wsum
        # representative image = the highest weighted-contribution role's best view
        path = max(roled.items(), key=lambda kv: weights.get(kv[0], 0.0) * kv[1][0])[1][1]
        best[sku] = (max(0.0, min(1.0, score)), path)
    return _build_results(best, filters, top_k)


# Attribute weights for query-photo re-ranking. Stone shape and metal are the
# most RELIABLY detected from a messy photo; setting type (bezel/solitaire/halo)
# is the least reliable zero-shot — kept low so a misread setting can't bury a
# good visual match (e.g. a 4-prong solitaire misdetected as "bezel").
_AGREE_WEIGHTS = {"center_stone_shape": 0.50, "metal_color": 0.35, "setting_type": 0.15}
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
    cmat, mu, meta = _matrix()
    if cmat.size == 0:
        return []
    tvec = _prep_query(embed_text(query), mu)[0]
    clip_scores = cmat @ tvec
    best = _best_per_sku(clip_scores, meta)

    rows = db.get_skus(list(best.keys()))
    # Hybrid: normalize CLIP similarity to ~0-1 and blend 50/50 with keyword match.
    blended: dict[str, tuple[float, str]] = {}
    for sku, (clip_s, path) in best.items():
        kw = _keyword_score(query, rows.get(sku))
        clip_norm = (clip_s + 1.0) / 2.0
        blended[sku] = (0.5 * clip_norm + 0.5 * kw, path)
    return _build_results(blended, filters, top_k)
