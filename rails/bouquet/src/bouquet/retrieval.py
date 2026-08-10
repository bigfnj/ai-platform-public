"""Retrieval-grounding for the identify step.

Embed the uploaded photo with the broker's SigLIP encoder, find the nearest KB
reference photos (a baked 200-vector index), and return a short candidate flower
list. That shortlist is injected into the vision prompt so the model maps a bloom to
a flower we actually profile — which fixes the out-of-vocabulary confusions (ruscus
called "holly", statice called "solidago") that prompt/model tuning could not.

Best-effort by design: a missing index, a broker embed failure, or a shape mismatch
returns ``[]`` and identify runs exactly as before. Grounding can never break identify.

Validated on the reference eval: recall 0.855 -> 0.913 (ruscus 0/4 -> 4/4,
spirea 1/4 -> 4/4). See docs/vision-eval.md + docs/BACKLOG.md.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from bouquet import broker, config, kb

log = logging.getLogger("bouquet.retrieval")


@lru_cache(maxsize=1)
def _index():
    """Load the baked reference index once: (vectors [N,d] float32, slugs [N]). Returns
    None (grounding disabled) if numpy or the index file is unavailable."""
    path = config.KB_DIR / "images" / "reference-index.npz"
    if not path.is_file():
        log.info("no reference index at %s — grounding off", path)
        return None
    try:
        import numpy as np
        data = np.load(path)
        # Guard against a stale index in a different embedding space. SigLIP v1 and v2 base are
        # both 768-d, so the dim check in shortlist() can't catch a model swap — compare the id
        # the index was built with to the one we expect and disable grounding loudly on mismatch.
        built = str(data["model"]) if "model" in data.files else ""
        if built and built != config.GROUNDING_MODEL:
            log.warning("reference index built with %r but expected %r — grounding OFF; rebuild it "
                        "(tools/build_reference_index.py under the media venv)", built, config.GROUNDING_MODEL)
            return None
        return data["vectors"].astype("float32"), [str(s) for s in data["slugs"]]
    except Exception as exc:  # noqa: BLE001
        log.warning("could not load reference index: %s", exc)
        return None


def shortlist(image_b64: str, k: int | None = None, max_flowers: int | None = None) -> list[str]:
    """Candidate flower titles for this photo, nearest-first, or [] on any failure."""
    idx = _index()
    if idx is None:
        return []
    vectors, slugs = idx
    k = k or config.GROUNDING_K
    max_flowers = max_flowers or config.GROUNDING_MAX
    try:
        import numpy as np
        q = np.asarray(broker.embed_image(image_b64), dtype="float32")
    except broker.BrokerError as exc:
        log.warning("grounding embed failed, going ungrounded: %s", exc)
        return []
    except Exception as exc:  # noqa: BLE001
        log.warning("grounding error, going ungrounded: %s", exc)
        return []
    if q.ndim != 1 or q.shape[0] != vectors.shape[1]:
        return []
    sims = vectors @ q                      # cosine (both unit-normalised)
    order = sims.argsort()[::-1][:k]        # nearest k reference photos
    out: list[str] = []
    for j in order:
        flower = kb.get_flower(slugs[j])
        title = flower.title if flower else slugs[j]
        if title not in out:
            out.append(title)
        if len(out) >= max_flowers:
            break
    return out
