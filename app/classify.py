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


def _usage_of(response) -> dict:
    """Exact token usage from an API response (the source of truth for billing)."""
    u = response.usage
    return {
        "input_tokens": getattr(u, "input_tokens", 0) or 0,
        "output_tokens": getattr(u, "output_tokens", 0) or 0,
        "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
        "cache_creation_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
    }


def classify_views(view_paths: list[Path]) -> tuple[dict, dict]:
    """One Anthropic API call with up to 4 view images. Returns (tags, usage).
    Raises on any failure."""
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
    return parse_tags(text), _usage_of(response)

