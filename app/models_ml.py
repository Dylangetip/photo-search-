"""Lazy, load-once model holders. CPU-only. Weights cached under /data/models.

Both models are loaded at most once per process — never per request. The worker
loads them eagerly on its first ingestion; the API loads them lazily on the
first search query.
"""
import threading

from . import config  # noqa: F401  (sets HF_HOME / U2NET_HOME before model imports)

_lock = threading.Lock()
_clip = None      # (model, preprocess, tokenizer)
_rembg = None     # rembg session


def get_clip():
    global _clip
    if _clip is None:
        with _lock:
            if _clip is None:
                import open_clip
                import torch

                from . import config
                torch.set_num_threads(max(1, (torch.get_num_threads() or 4)))
                # `hf-hub:Org/Model` loads an open_clip-compatible HF checkpoint
                # (e.g. Marqo/marqo-fashionSigLIP, a fashion-retrieval-tuned
                # model) with no separate `pretrained` tag.
                if config.CLIP_MODEL.startswith("hf-hub:"):
                    model, _, preprocess = open_clip.create_model_and_transforms(config.CLIP_MODEL)
                else:
                    model, _, preprocess = open_clip.create_model_and_transforms(
                        config.CLIP_MODEL, pretrained=config.CLIP_PRETRAINED
                    )
                model.eval()
                # Text tower is optional: some retrieval checkpoints need an HF
                # tokenizer (transformers). If it can't load, image search still
                # works and text search falls back to keyword-only.
                try:
                    tokenizer = open_clip.get_tokenizer(config.CLIP_MODEL)
                except Exception:
                    tokenizer = None
                _clip = (model, preprocess, tokenizer)
    return _clip


def get_rembg():
    global _rembg
    if _rembg is None:
        with _lock:
            if _rembg is None:
                from rembg import new_session
                _rembg = new_session("u2netp")  # lightweight, CPU-friendly
    return _rembg
