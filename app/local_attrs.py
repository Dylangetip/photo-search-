"""Local zero-shot attribute detection — no API, no tokens.

CLIP embeds text and images into the same space, so we can read coarse ring
attributes off a query photo by comparing its embedding against pre-computed
text-prompt embeddings ("a ring with a marquise cut diamond", ...). Detected
attributes feed the same re-ranking used for tagged catalog SKUs.

Per-search cost: a handful of small matrix multiplications. The text-prompt
embeddings are computed once per process and cached.
"""
import threading

import numpy as np

from . import config

# Multiple prompt phrasings per value are averaged (standard CLIP ensembling).
# Values map onto the catalog taxonomy so attribute_agreement compares 1:1.
PROMPT_SETS: dict[str, dict[str, list[str]]] = {
    "center_stone_shape": {
        "round": ["a ring with a round brilliant cut diamond",
                  "an engagement ring with a circular diamond"],
        "oval": ["a ring with an oval cut diamond",
                 "an engagement ring with an oval shaped center stone"],
        "marquise": ["a ring with a marquise cut diamond",
                     "an engagement ring with a pointed eye-shaped diamond"],
        "pear": ["a ring with a pear shaped diamond",
                 "an engagement ring with a teardrop shaped diamond"],
        "emerald": ["a ring with an emerald cut rectangular step-cut diamond"],
        "cushion": ["a ring with a cushion cut diamond with rounded square shape"],
        "princess": ["a ring with a princess cut square diamond"],
        "radiant": ["a ring with a radiant cut rectangular diamond"],
        "asscher": ["a ring with an asscher cut square step-cut diamond"],
        "heart": ["a ring with a heart shaped diamond"],
    },
    "metal_color": {
        "yellow_gold": ["a yellow gold ring", "a ring made of yellow gold"],
        "white_gold": ["a white gold ring", "a silver colored platinum ring"],
        "rose_gold": ["a rose gold ring", "a pink rose gold ring"],
        "two_tone": ["a two tone ring with both yellow gold and white gold"],
    },
    "setting_type": {
        "solitaire": ["a solitaire engagement ring with a single diamond on a plain band"],
        "halo": ["a halo engagement ring with a circle of small diamonds around the center stone"],
        "three_stone": ["a three stone engagement ring with a center diamond and two side stones"],
        "cluster": ["a cluster ring with many small diamonds grouped together"],
        "bezel": ["a bezel set ring with the diamond surrounded by a metal rim"],
        "eternity": ["an eternity band ring with diamonds all the way around"],
    },
    # Side-stone configuration — the arrangement of accent stones around the
    # center stone. A major design dimension on its own (separate from setting),
    # and reliably read zero-shot.
    "side_stone_style": {
        "solitaire": ["a solitaire ring with a single center diamond and no side stones",
                      "a plain engagement ring with one diamond and nothing beside it"],
        "three_stone": ["a three stone ring with one stone on each side of the center diamond",
                        "an engagement ring with two side stones flanking the center"],
        "halo": ["a halo ring with a ring of small diamonds surrounding the center stone"],
        "cluster": ["a ring with small accent diamonds clustered beside the center stone",
                    "an engagement ring with a cluster of small side diamonds"],
    },
}

# Acceptance thresholds: softmax(temperature * cosine) over the field's values;
# accept the top value only when it is confident AND clearly ahead.
TEMPERATURE = float(__import__("os").environ.get("LOCAL_ATTR_TEMP", "100.0"))
ACCEPT_PROB = float(__import__("os").environ.get("LOCAL_ATTR_ACCEPT", "0.35"))
ACCEPT_GAP = float(__import__("os").environ.get("LOCAL_ATTR_GAP", "0.10"))

_lock = threading.Lock()
_cache: dict[str, tuple[list[str], np.ndarray]] = {}


def _field_matrix(field: str) -> tuple[list[str], np.ndarray]:
    """(values, [n_values, d] matrix of averaged, normalized prompt embeddings)."""
    if field not in _cache:
        with _lock:
            if field not in _cache:
                from .pipeline import embed_text
                values, rows = [], []
                for value, prompts in PROMPT_SETS[field].items():
                    vecs = [embed_text(p) for p in prompts]
                    if any(v is None for v in vecs):
                        raise RuntimeError("active model has no text tower; "
                                           "zero-shot attribute detection disabled")
                    embs = np.stack(vecs)
                    mean = embs.mean(axis=0)
                    mean = mean / np.linalg.norm(mean)
                    values.append(value)
                    rows.append(mean.astype(np.float32))
                _cache[field] = (values, np.stack(rows))
    return _cache[field]


def detect_attributes(query_vec: np.ndarray) -> dict:
    """Zero-shot attributes from a query image embedding. Uncertain fields are
    omitted, so the re-ranker simply skips them (never a wrong hard signal)."""
    out: dict = {}
    q = np.asarray(query_vec, dtype=np.float32)
    for field in PROMPT_SETS:
        values, mat = _field_matrix(field)
        sims = mat @ q
        logits = TEMPERATURE * sims
        logits -= logits.max()
        probs = np.exp(logits)
        probs /= probs.sum()
        order = np.argsort(probs)[::-1]
        top, second = order[0], order[1] if len(order) > 1 else order[0]
        if probs[top] >= ACCEPT_PROB and (probs[top] - probs[second]) >= ACCEPT_GAP:
            out[field] = values[top]
            out.setdefault("_confidence", {})[field] = round(float(probs[top]), 3)
    return out


def warm_cache() -> None:
    """Embed all prompt sets once (call at worker/API startup if desired)."""
    for field in PROMPT_SETS:
        _field_matrix(field)
