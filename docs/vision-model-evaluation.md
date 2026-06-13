# Vision model evaluation — NVIDIA C-RADIOv4 vs CLIP (June 2026)

Evaluated NVIDIA's **C-RADIOv4** vision foundation model as a possible
replacement for the default `open_clip` ViT-B-32 image embedder, on the real
37-CAD catalog + 5 customer query photos.

## What C-RADIOv4 is

A distilled vision backbone that unifies DINOv3 + SigLIP2 + SAM3 into one model
and emits a "summary" embedding intended for image-level retrieval.
- **License:** NVIDIA Open Model License — **commercial use allowed**.
- **Sizes:** B 98M, L 320M, **SO400M 431M**, H 653M. Only SO400M and H are on
  Hugging Face; B/L are torch.hub-only (and `c-radio_v4-b` was not yet resolvable
  via torch.hub at time of test).
- Refs: <https://huggingface.co/nvidia/C-RADIOv4-SO400M>,
  <https://github.com/NVlabs/RADIO>, tech report arXiv:2601.17237.

## How it tested (CPU, this is a CPU-only product)

| | open_clip ViT-B-32 (current default) | C-RADIOv4-SO400M |
|---|---|---|
| Params | ~88M | 431M |
| Embed time (CPU, 384px) | ~0.1 s | ~1.4 s (batched) / ~2.3 s single |
| Per query (3 role crops) | <0.5 s | ~4 s (over the <4s target) |
| Catalog embed, ~1k rings ×4 views | minutes | ~1.5 hours (one-time) |
| Text → image search | **yes** (CLIP text tower) | **no** (vision-only) |
| Local zero-shot attribute re-rank | yes (CLIP text prompts) | would need a separate CLIP pass |

## Match quality on the 5 real queries (top-1)

| query | CLIP #1 | C-RADIO #1 | verdict |
|---|---|---|---|
| round solitaire (1) | 1780161798421 | 1780161798421 | tie |
| rose emerald halo (2) | 1780161798421 | 1778688762928 (square stone) | RADIO slightly better |
| oval three-stone (3) | 1778939759141 | 1781200184023 | tie (both oval) |
| round solitaire (4) | 1779991973885 (round) | 147591 (marquise) | **CLIP clearly better** |
| princess stack | 1778688762928 | 1778688762928 | tie |

## Conclusion — not adopted

C-RADIOv4 is **not** a clear improvement for this photo→CAD task: roughly even
on the real queries (one win each), while costing markedly more on CPU, being
vision-only (breaks text search and the attribute re-rank), and needing
`trust_remote_code` + a ~1.7 GB download. Lesson already learned with ViT-L-14:
a bigger backbone is not automatically better and can regress (hubness).

**Kept the default `open_clip` ViT-B-32.** The image embedder remains swappable
to any `open_clip` model via `CLIP_MODEL` / `CLIP_PRETRAINED` for experimentation.

### When it would be worth revisiting
- The catalog moves to a **GPU** box (per-query latency stops mattering).
- The smaller **B (98M)** variant becomes easily loadable and A/B-tests as a
  clear win on a labeled query set (the missing piece throughout: ground-truth
  query→CAD labels to measure real accuracy rather than eyeballing).
- A future model offers strong image embeddings **and** a text tower (so text
  search and the attribute re-rank don't regress).

If any of those hold, the integration path is: add an embedder backend behind
`pipeline.embed_images`, store image embeddings under the active backend
(re-ingest on switch), and keep CLIP for the text-search path.
