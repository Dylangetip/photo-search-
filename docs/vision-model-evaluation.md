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

## Also tested: Marqo-FashionSigLIP (the *right class* of model)

C-RADIO is a general backbone; the right tool for "photo → product catalog" is a
**retrieval-tuned embedding model**, ideally domain-matched. Rings are a fashion
category, so **Marqo-FashionSigLIP** (SigLIP fine-tuned for fashion product
search) is the strongest candidate — and unlike C-RADIO it keeps a text tower.

| | ViT-B-32 (default) | Marqo-FashionSigLIP | C-RADIOv4-SO400M |
|---|---|---|---|
| Params | ~88M | 203M | 431M |
| CPU embed | ~0.1 s | ~0.15 s | ~1.4 s |
| Text tower | yes | yes (needs HF tokenizer) | no |
| Built for | general | **fashion product retrieval** | general backbone |

On the **37-ring** test it was **roughly even with CLIP and showed its own
hubness** (one oval-solitaire CAD became #1 for three different queries; it also
read a round solitaire as oval and missed the princess stack). Marqo's published
benchmarks show FashionSigLIP beating generic CLIP/SigLIP on fashion retrieval by
large margins — but that can't be reproduced on 37 unlabeled rings.

## The real finding: the model is not the current bottleneck

**Four models** (CLIP ViT-B-32, ViT-L-14, C-RADIOv4, FashionSigLIP) all land
"roughly even / mixed" on these 5 queries. The limiters are:
1. **Tiny test catalog (37 rings)** — many queries have no close match, so the
   #1 is semi-arbitrary among mediocre options.
2. **No ground-truth labels** — we can't measure which model is actually better;
   we're eyeballing 5 hard photos.

Swapping models is premature optimization until there's (a) the real ~1k-ring
catalog and (b) ~10–20 labeled query→CAD pairs to A/B against.

## How to A/B a different model (no code change)

The image embedder is swappable via env vars (re-ingest after switching):
```
# any open_clip checkpoint, including HF retrieval models:
CLIP_MODEL=hf-hub:Marqo/marqo-fashionSigLIP
# (leave CLIP_PRETRAINED unset for hf-hub: models)
```
If the chosen model's text tower can't load on the installed transformers,
**image search still works**; text search and the attribute re-rank degrade to
keyword-only automatically.

## Conclusion — not adopted (yet)

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
