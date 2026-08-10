"""Build the retrieval-grounding reference index: embed the KB reference photos with
SigLIP and save the vectors + slugs as ``seed/knowledge-base/reference-index.npz``.

Committed artifact — regenerate it whenever the reference photos change (e.g. after a
photo swap). Run with the media venv (torch + transformers + PIL), which the broker's
embed_image worker also uses, so the baked vectors match what the broker produces at
runtime:

    D:\\.claude\\media-venv\\Scripts\\python.exe rails/bouquet/tools/build_reference_index.py

Uses the SAME model as the broker default (google/siglip2-base-patch16-384). This id MUST match
the broker's do_embed_image default AND bouquet's config.GROUNDING_MODEL, or grounding self-disables.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("HF_HOME", r"D:\.claude\hf-cache")

import numpy as np
import torch
from PIL import Image
from transformers import AutoModel, AutoProcessor

RAIL = Path(__file__).resolve().parents[1]
IMAGES = RAIL / "seed" / "knowledge-base" / "images"
OUT = IMAGES / "reference-index.npz"
MODEL = "google/siglip2-base-patch16-384"

sys.path.insert(0, str(RAIL / "src"))
from bouquet import kb  # noqa: E402

known = {f.slug for f in kb.all_flowers()}
model = AutoModel.from_pretrained(MODEL).eval()
proc = AutoProcessor.from_pretrained(MODEL)

vectors: list[list[float]] = []
slugs: list[str] = []
paths = [p for d in sorted(IMAGES.iterdir()) if d.is_dir() and d.name in known
         for p in sorted(d.glob("*.jpg"))]
print(f"embedding {len(paths)} reference photos with {MODEL} ...", file=sys.stderr)
for i, p in enumerate(paths):
    img = Image.open(p).convert("RGB")
    inp = proc(images=[img], return_tensors="pt")
    with torch.no_grad():
        out = model.get_image_features(**inp)
    feat = out.pooler_output if hasattr(out, "pooler_output") else out
    feat = torch.nn.functional.normalize(feat, dim=-1)
    vectors.append([round(x, 6) for x in feat[0].tolist()])   # match broker's 6-dp output
    slugs.append(p.parent.name)
    if (i + 1) % 40 == 0:
        print(f"  {i+1}/{len(paths)}", file=sys.stderr)

np.savez(OUT, vectors=np.asarray(vectors, dtype=np.float32),
         slugs=np.asarray(slugs), model=MODEL)
print(f"saved {len(slugs)} vectors ({len(vectors[0])}-d) -> {OUT}")
