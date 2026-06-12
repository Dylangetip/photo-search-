"""Image pipeline: SKU extraction, Type A/B detection, quadrant split,
rembg -> white composite -> crop -> resize, and CLIP embedding.

The same preprocessing funnel is used for ingestion and for query images.
"""
import re

import numpy as np
from PIL import Image

from . import config
from .models_ml import get_clip, get_rembg


# ---------------- SKU extraction ----------------

def extract_sku(filename_stem: str) -> str | None:
    m = re.match(config.SKU_REGEX, filename_stem)
    if not m:
        return None
    try:
        sku = m.group(1)
    except IndexError:
        sku = m.group(0)
    return sku or None


# ---------------- Type A (4-up CAD sheet) detection ----------------

def _uniform_line(band: np.ndarray, axis: int) -> bool:
    """True if some line in the band is near-uniform (the sheet's dividing line).

    Handles both real-sheet divider styles: a thin grey rule and a plain white
    gap between quadrants both read as a low-std line down the midline.
    """
    stds = band.std(axis=axis)
    return bool((stds < config.SHEET_LINE_STD).any())


def detect_sheet(img: Image.Image) -> bool:
    """Type A: landscape AR ~1.6-1.8 AND near-uniform dividing lines at the midlines."""
    w, h = img.size
    if h == 0:
        return False
    ar = w / h
    if not (config.SHEET_AR_MIN <= ar <= config.SHEET_AR_MAX):
        return False
    g = np.asarray(img.convert("L"), dtype=np.float32)
    bw = max(2, int(w * 0.01))
    bh = max(2, int(h * 0.01))
    col_band = g[:, w // 2 - bw: w // 2 + bw]   # vertical dividing line -> uniform columns
    row_band = g[h // 2 - bh: h // 2 + bh, :]   # horizontal dividing line -> uniform rows
    return _uniform_line(col_band, axis=0) and _uniform_line(row_band, axis=1)


def split_quadrants(img: Image.Image) -> list[Image.Image]:
    """Split a 4-up sheet into quadrants, cropping each inward ~6% per edge to
    drop view labels, axis gizmos, and most annotation text."""
    w, h = img.size
    qw, qh = w // 2, h // 2
    inset = config.QUADRANT_INSET
    out = []
    for qy in (0, 1):
        for qx in (0, 1):
            x0, y0 = qx * qw, qy * qh
            dx, dy = int(qw * inset), int(qh * inset)
            out.append(img.crop((x0 + dx, y0 + dy, x0 + qw - dx, y0 + qh - dy)))
    return out


# ---------------- Preprocess funnel ----------------

def remove_background(img: Image.Image) -> Image.Image:
    """rembg -> RGBA with transparent background."""
    from rembg import remove
    return remove(img, session=get_rembg())


def composite_crop_resize(rgba: Image.Image,
                          pad: float = 0.08,
                          size: int = config.VIEW_SIZE) -> Image.Image:
    """Composite onto pure white, crop to subject bbox with padding, resize longest side."""
    rgba = rgba.convert("RGBA")
    alpha = np.asarray(rgba.split()[-1])
    ys, xs = np.where(alpha > 10)
    if len(xs) == 0:  # rembg found nothing — keep the full frame
        box = (0, 0, rgba.width, rgba.height)
    else:
        x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        px, py = int((x1 - x0) * pad), int((y1 - y0) * pad)
        box = (max(0, x0 - px), max(0, y0 - py),
               min(rgba.width, x1 + px), min(rgba.height, y1 + py))
    white = Image.new("RGB", rgba.size, (255, 255, 255))
    white.paste(rgba, mask=rgba.split()[-1])
    out = white.crop(box)
    w, h = out.size
    scale = size / max(w, h)
    if scale < 1 or max(w, h) < size:
        out = out.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    return out


def preprocess_view(img: Image.Image) -> Image.Image:
    """The full funnel: rembg -> white bg -> bbox crop (8% pad) -> 512px."""
    return composite_crop_resize(remove_background(img.convert("RGB")))


def preprocess_query(img: Image.Image) -> Image.Image:
    """Query funnel — identical, but downscale first so rembg stays under the latency target."""
    img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > config.QUERY_RESIZE:
        scale = config.QUERY_RESIZE / max(w, h)
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    return composite_crop_resize(remove_background(img))


# ---------------- Embeddings ----------------

def embed_images(images: list[Image.Image]) -> np.ndarray:
    """CLIP-embed PIL images. Returns L2-normalized float32 [n, d]."""
    import torch
    model, preprocess, _ = get_clip()
    batch = torch.stack([preprocess(im.convert("RGB")) for im in images])
    with torch.no_grad():
        feats = model.encode_image(batch)
        feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats.cpu().numpy().astype(np.float32)


def embed_text(text: str) -> np.ndarray:
    """CLIP-embed a text query. Returns L2-normalized float32 [d]."""
    import torch
    model, _, tokenizer = get_clip()
    tokens = tokenizer([text])
    with torch.no_grad():
        feats = model.encode_text(tokens)
        feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats.cpu().numpy().astype(np.float32)[0]
