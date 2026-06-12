"""Test setup: point DATA_DIR at a temp dir BEFORE app modules import,
and stub the heavy ML models (CLIP / rembg) with deterministic fakes."""
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

_TMP = tempfile.mkdtemp(prefix="ringfinder_test_")
os.environ["DATA_DIR"] = _TMP
os.environ["DISABLE_WORKER"] = "1"
os.environ["CLASSIFY_ENABLED"] = "false"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, db, pipeline  # noqa: E402


def fake_embedding(img) -> np.ndarray:
    """Deterministic 32-dim embedding from a downsampled grayscale thumbnail."""
    small = np.asarray(img.convert("L").resize((8, 4)), dtype=np.float32).flatten()
    v = small - small.mean()
    n = np.linalg.norm(v)
    return (v / n if n > 0 else np.ones(32, dtype=np.float32) / np.sqrt(32)).astype(np.float32)


@pytest.fixture(autouse=True)
def stub_models(monkeypatch):
    monkeypatch.setattr(pipeline, "remove_background", lambda img: img.convert("RGBA"))
    monkeypatch.setattr(pipeline, "embed_images",
                        lambda imgs: np.stack([fake_embedding(im) for im in imgs]))

    def fake_embed_text(text):
        rng = np.random.RandomState(abs(hash(text)) % (2 ** 31))
        v = rng.randn(32).astype(np.float32)
        return v / np.linalg.norm(v)
    monkeypatch.setattr(pipeline, "embed_text", fake_embed_text)


@pytest.fixture(autouse=True)
def fresh_db():
    config.ensure_dirs()
    if config.DB_PATH.exists():
        config.DB_PATH.unlink()
    db.init_db()
    from app import search
    search._cache.update(sig=None, mat=None, meta=None)
    yield
