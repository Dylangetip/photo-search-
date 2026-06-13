"""Image pipeline: SKU extraction, Type A/B detection, quadrant split,
rembg -> white composite -> crop -> resize, and CLIP embedding.

The same preprocessing funnel is used for ingestion and for query images.
"""
import re

import numpy as np
from PIL import Image, ImageDraw

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


def make_display(img: Image.Image, max_side: int = config.DISPLAY_SIZE) -> Image.Image:
    """The WHOLE CAD, unclipped — just flattened to RGB on white (handles
    transparency / HEIC) and downscaled for the browser. No background removal,
    no cropping: this is what staff see as the suggested match."""
    img = img.convert("RGBA")
    flat = Image.new("RGB", img.size, (255, 255, 255))
    flat.paste(img, mask=img.split()[-1])
    w, h = flat.size
    if max(w, h) > max_side:
        s = max_side / max(w, h)
        flat = flat.resize((round(w * s), round(h * s)), Image.LANCZOS)
    return flat


def preprocess_view(img: Image.Image) -> Image.Image:
    """The full funnel: rembg -> white bg -> bbox crop (8% pad) -> 512px."""
    return composite_crop_resize(remove_background(img.convert("RGB")))


def _find_stone(rgb: Image.Image, rgba: Image.Image):
    """Locate the center stone: the largest WHITE (bright + desaturated) region
    inside the subject. Returns (cx, cy, stone_extent_px) or None.

    Whiteness (not raw brightness) is key: a diamond and its metal setting are
    bright AND colorless, while skin — including nail/skin specular highlights
    that fooled a pure-brightness detector — keeps a skin hue. Faceted stones
    are merged with a morphological close, and an implausibly small or off
    detection returns None so the caller can fall back to the full crop."""
    from scipy import ndimage

    a = np.asarray(rgba.split()[-1])
    mask = a > 10
    if mask.sum() < 400:
        return None
    hsv = np.asarray(rgb.convert("HSV"))
    s, v = hsv[..., 1].astype(np.float32), hsv[..., 2].astype(np.float32)
    white = mask & (v >= config.STONE_VALUE_MIN) & (s <= config.STONE_SAT_MAX)
    if white.sum() < 40:
        return None
    # Merge faceted highlights into one stone blob, then take the largest.
    white = ndimage.binary_closing(white, structure=np.ones((5, 5)), iterations=2)
    labels, n = ndimage.label(white)
    if n == 0:
        return None
    sizes = ndimage.sum(white, labels, range(1, n + 1))
    blob = labels == (int(np.argmax(sizes)) + 1)
    ys, xs = np.where(blob)
    extent = max(xs.max() - xs.min() + 1, ys.max() - ys.min() + 1)
    # Reject implausibly small detections (a speck of glare, not the stone).
    if extent < max(12, min(rgb.size) * config.STONE_MIN_EXTENT_FRAC):
        return None
    return int(xs.mean()), int(ys.mean()), int(extent)


def _crop_around(rgba: Image.Image, cx: int, cy: int, half: int) -> Image.Image:
    """Square crop on white centered at (cx, cy) with the given half-size."""
    W, H = rgba.size
    half = max(half, min(W, H) // 9)
    white = Image.new("RGB", rgba.size, (255, 255, 255))
    white.paste(rgba.convert("RGB"), mask=rgba.split()[-1])
    crop = white.crop((max(0, cx - half), max(0, cy - half),
                       min(W, cx + half), min(H, cy + half)))
    side = config.VIEW_SIZE
    s = side / max(crop.size)
    return crop.resize((max(1, round(crop.width * s)), max(1, round(crop.height * s))), Image.LANCZOS)


def _skin_fraction(rgb: Image.Image, rgba: Image.Image) -> float:
    """Fraction of the subject that is skin-toned — high means a hand/finger
    shot whose full-subject crop embeds the hand, not the ring."""
    a = np.asarray(rgba.split()[-1])
    arr = np.asarray(rgb).astype(np.int16)
    mask = a > 10
    if mask.sum() < 100:
        return 0.0
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    skin = (r > 95) & (g > 40) & (b > 20) & (r > g) & (g > b) & ((r - b) > 15) & mask
    return float(skin.sum()) / float(mask.sum())


def _band_crop(rgba: Image.Image, cx: int, cy: int, extent: int) -> Image.Image:
    """Whole-ring crop with the bright center stone masked to white, so the
    embedding keys on the BAND/shank shape (split-shank, twisted, tapered,
    pave-set vs plain) rather than the stone."""
    white = Image.new("RGB", rgba.size, (255, 255, 255))
    white.paste(rgba.convert("RGB"), mask=rgba.split()[-1])
    r = int(extent * config.BAND_STONE_MASK)
    ImageDraw.Draw(white).ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255))
    W, H = white.size
    half = max(int(extent * config.STONE_RING_SCALE), min(W, H) // 9)
    crop = white.crop((max(0, cx - half), max(0, cy - half),
                       min(W, cx + half), min(H, cy + half)))
    s = config.VIEW_SIZE / max(crop.size)
    return crop.resize((max(1, round(crop.width * s)), max(1, round(crop.height * s))), Image.LANCZOS)


def query_crops(img: Image.Image) -> list[tuple[str, Image.Image]]:
    """Role-tagged query crops (rembg once). Roles are scored separately and
    combined with per-role weights so the BAND can be prioritized:
      full  — full subject on white (clean product shots; dropped on hand shots)
      stone — ring head: center stone shape + setting
      band  — whole ring with the stone masked: the band/shank shape
    """
    img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > config.QUERY_RESIZE:
        s = config.QUERY_RESIZE / max(w, h)
        img = img.resize((round(w * s), round(h * s)), Image.LANCZOS)
    rgba = remove_background(img)

    crops: list[tuple[str, Image.Image]] = []
    skin = _skin_fraction(img, rgba)
    stone_found = None
    if config.QUERY_STONE_CROP:
        try:
            stone_found = _find_stone(img, rgba)
        except Exception:
            stone_found = None

    # Drop the hand-polluted full crop on clear finger/stack shots.
    if not (stone_found is not None and skin >= config.SKIN_DROP_FRACTION):
        crops.append(("full", composite_crop_resize(rgba)))

    if stone_found is not None:
        cx, cy, extent = stone_found
        crops.append(("stone", _crop_around(rgba, cx, cy, int(extent * config.STONE_HEAD_SCALE))))
        crops.append(("band", _band_crop(rgba, cx, cy, extent)))

    if not crops:  # nothing detected — fall back to the full subject
        crops.append(("full", composite_crop_resize(rgba)))
    return crops


def preprocess_query(img: Image.Image) -> Image.Image:
    """Single cleaned query image (full subject) — used for the UI preview and
    query logging. Embedding uses query_crops() for the richer crop set."""
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
