"""Attribute classification via the Anthropic API — one call per SKU, up to 4 views.

The system stays fully functional without classification: on any failure the SKU
is left 'pending' and retried on the next worker cycle.
"""
import base64
import json
import re
from pathlib import Path

from . import config

TAXONOMY_PROMPT = """\
You are a jewelry cataloger. You are shown up to 4 views of ONE ring (3D renders; \
some may be orthographic CAD views with green dimension annotations).

Respond with ONLY a JSON object — no prose, no code fences — using EXACTLY this schema \
and ONLY the allowed values:

{
  "metal_color": "yellow_gold|white_gold|rose_gold|two_tone|platinum_look|other",
  "center_stone_shape": "round|oval|marquise|pear|emerald|cushion|princess|radiant|asscher|heart|none|other",
  "setting_type": "solitaire|halo|hidden_halo|three_stone|cluster|bezel|tension|eternity|other",
  "side_stones": true,
  "pave": "none|pave|channel|bead|unclear",
  "milgrain": true,
  "engraving": true,
  "cathedral": true,
  "band_style": "straight|tapered|split_shank|twisted|knife_edge|other",
  "style_tags": ["vintage", "modern", "classic", "nature_inspired", "art_deco", "minimalist", "ornate", "romantic"],
  "annotated_dimensions_mm": {"label of any number visible in green annotations": "value"},
  "confidence_notes": "one sentence on any field you are unsure about",
  "overall_confidence": 0.0
}

Boolean fields must be true/false. style_tags must be a subset of the listed values. \
annotated_dimensions_mm is {} when no annotations are visible. overall_confidence is 0.0-1.0."""

QUERY_PROMPT = """\
You are a jewelry cataloger. You are shown a customer's photo of a ring — it may be \
a phone photo on a hand, a social-media screenshot, low resolution, or show a ring \
stack (engagement ring worn with a wedding band). Describe ONLY the main engagement \
ring: the one with the largest center stone. Ignore plain bands, hands, and background.

Respond with ONLY a JSON object — no prose, no code fences — using EXACTLY this schema \
and ONLY the allowed values:

{
  "metal_color": "yellow_gold|white_gold|rose_gold|two_tone|platinum_look|other",
  "center_stone_shape": "round|oval|marquise|pear|emerald|cushion|princess|radiant|asscher|heart|none|other",
  "setting_type": "solitaire|halo|hidden_halo|three_stone|cluster|bezel|tension|eternity|other",
  "side_stones": true,
  "pave": "none|pave|channel|bead|unclear",
  "milgrain": true,
  "engraving": true,
  "cathedral": true,
  "band_style": "straight|tapered|split_shank|twisted|knife_edge|other",
  "style_tags": ["vintage", "modern", "classic", "nature_inspired", "art_deco", "minimalist", "ornate", "romantic"],
  "confidence_notes": "one sentence on any field you are unsure about",
  "overall_confidence": 0.0
}

If a field is not visible in the photo, use "other"/"unclear" rather than guessing. \
overall_confidence is 0.0-1.0."""

_MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def parse_tags(text: str) -> dict:
    """Defensive parse: strip code fences, grab the outermost JSON object."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in response: {text[:200]!r}")
    return json.loads(text[start:end + 1])


def classify_views(view_paths: list[Path]) -> dict:
    """One Anthropic API call with up to 4 view images. Raises on any failure."""
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    import anthropic

    content = []
    for p in view_paths[:4]:
        data = base64.standard_b64encode(p.read_bytes()).decode("utf-8")
        content.append({
            "type": "image",
            "source": {"type": "base64",
                       "media_type": _MEDIA.get(p.suffix.lower(), "image/jpeg"),
                       "data": data},
        })
    content.append({"type": "text", "text": TAXONOMY_PROMPT})

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=config.CLASSIFY_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": content}],
    )
    text = next((b.text for b in response.content if b.type == "text"), "")
    return parse_tags(text)


def classify_query_image(img) -> dict:
    """Classify a customer's query photo (in-memory PIL image). One API call.

    Sends the ORIGINAL photo (downscaled), not the rembg-cleaned crop — the
    model reads metal color and setting better with full photo context, and
    it's instructed to ignore hands/stacks/background itself.
    """
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    import io

    import anthropic

    img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > 768:
        s = 768 / max(w, h)
        img = img.resize((round(w * s), round(h * s)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    data = base64.standard_b64encode(buf.getvalue()).decode("utf-8")

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=config.CLASSIFY_MODEL,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
                {"type": "text", "text": QUERY_PROMPT},
            ],
        }],
    )
    text = next((b.text for b in response.content if b.type == "text"), "")
    return parse_tags(text)
