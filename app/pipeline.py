"""Image pipeline: SKU extraction, Type A/B detection, quadrant split,
rembg -> white composite -> crop -> resize, and CLIP embedding.

The same preprocessing funnel is used for ingestion and for query images.
"""
import re

import numpy as np
from PIL import Image

from . import config
from .models_ml import get_clip, get_rembg

try:  # iPhone photos (HEIC) appear in real CAD folders — support them if available
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass


# ---------------- SKU extraction ----------------

def extract_sku(filename_stem: str) -> str | None:
    """SKU_REGEX capture group 1 applied to the filename stem. re.search, not
    re.match, so patterns can float (e.g. 'Copy of P.O. #140892 - Name' with
    SKU_REGEX matching the P.O. number); anchor with ^ for prefix behavior."""
    m = re.search(config.SKU_REGEX, filename_stem)
    if not m:
        return None
    try:
        sku = m.group(1)
    except IndexError:
        sku = m.group(0)
    return sku or None


# ---------------- Type A (4-up CAD sheet) detection ----------------
#
# Real CAD sheets are viewport captures: four panes separated by divider lines
# (thin grey rules or plain gaps). The dividers are near — but not exactly at —
# the midlines, and sheet aspect ratios vary (observed 1.42-1.67 against
# singles at 1.33). So the AR gate is wide and the decisive test is finding a
# near-uniform full-length line in BOTH orientations within the center band:
# a single centered render always interrupts at least one orientation.

def _find_divider(g: np.ndarray, axis: int) -> int | None:
    """Uniform full-length line within the center band (35-65%), preferring the
    candidate CLOSEST TO CENTER — an empty pane margin can also be uniform, and
    splitting at the centermost uniform line avoids cutting through a view.
    axis=0: scan columns, returns x. axis=1: scan rows, returns y."""
    n = g.shape[1] if axis == 0 else g.shape[0]
    lo, hi = int(n * 0.35), int(n * 0.65)
    stds = g[:, lo:hi].std(axis=0) if axis == 0 else g[lo:hi, :].std(axis=1)
    candidates = np.where(stds < config.SHEET_LINE_STD)[0]
    if len(candidates) == 0:
        return None
    center = n // 2 - lo
    return lo + int(candidates[np.abs(candidates - center).argmin()])


def find_sheet_dividers(img: Image.Image) -> tuple[int, int] | None:
    """(x, y) divider position if the image reads as a 4-up sheet, else None."""
    w, h = img.size
    if h == 0:
        return None
    ar = w / h
    if not (config.SHEET_AR_MIN <= ar <= config.SHEET_AR_MAX):
        return None
    g = np.asarray(img.convert("L"), dtype=np.float32)
    x = _find_divider(g, axis=0)
    y = _find_divider(g, axis=1)
    return (x, y) if x is not None and y is not None else None


def detect_sheet(img: Image.Image) -> bool:
    return find_sheet_dividers(img) is not None


def split_quadrants(img: Image.Image,
                    dividers: tuple[int, int] | None = None) -> list[Image.Image]:
    """Split a 4-up sheet at the detected dividers (midlines as fallback),
    cropping each quadrant inward ~6% per edge to drop view labels, axis
    gizmos, and most annotation text."""
    w, h = img.size
    dx_div, dy_div = dividers if dividers else (w // 2, h // 2)
    inset = config.QUADRANT_INSET
    boxes = [(0, 0, dx_div, dy_div), (dx_div, 0, w, dy_div),
             (0, dy_div, dx_div, h), (dx_div, dy_div, w, h)]
    out = []
    for x0, y0, x1, y1 in boxes:
        ix, iy = int((x1 - x0) * inset), int((y1 - y0) * inset)
        out.append(img.crop((x0 + ix, y0 + iy, x1 - ix, y1 - iy)))
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
