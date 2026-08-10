"""The photo -> report pipeline, as two human-straddled steps.

**identify** (vision) returns a structured flower inventory from the photo; the
florist reviews and corrects it; **generate** (the writer model) writes the report
from the *corrected* inventory + each flower's retrieved KB profile + the
cross-cutting references, in one of two voices. Splitting the two model loads
around the human edit means the vision->writer evict/reload lands during the pause.

Pure orchestration — no filesystem I/O beyond the broker and the KB (the API tier
owns the upload/derivative files), so it stays unit-testable with a fake broker.
The small image helpers here are the exception: they only transform bytes/paths.
"""

from __future__ import annotations

import base64
import io
import re
import shutil
from pathlib import Path

from bouquet import broker, config, kb, prompts

# Some chat models wrap the whole Markdown report in a ```markdown … ``` fence,
# which would render as a code block instead of formatted text. Strip a single
# wrapping fence if the entire body is enclosed in one.
_FENCE_RE = re.compile(r"^\s*```(?:markdown|md)?\s*\n(.*)\n```\s*$", re.DOTALL)


def _strip_md_fence(text: str) -> str:
    m = _FENCE_RE.match(text or "")
    return m.group(1).strip() if m else (text or "").strip()

# References always applied across the whole bouquet (the color/occasion lenses
# matter most); the other two are included trimmed for style/history colour.
_CONTEXT_REFERENCES = {
    "color-symbolism": "Color Symbolism",
    "occasions-and-events": "Occasions & Events",
    "bouquet-types": "Bouquet Types & Design Roles",
    "floriography-and-history": "Floriography & History",
}


# --- image helpers (bytes/paths only — no model, no KB) ---------------------

def _load_rgb(data: bytes):
    """Decode uploaded bytes to an RGB Pillow image, or None if unreadable / no
    Pillow (callers fall back to the raw bytes)."""
    try:
        from PIL import Image  # lazy: keeps import cost off non-analyze paths
    except ImportError:
        return None
    try:
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:  # noqa: BLE001 — unreadable/corrupt image
        return None


def image_to_b64(data: bytes, max_edge: int) -> str:
    """Downscale + re-encode to base64 JPEG (no data-URI prefix — Ollama wants raw
    base64). Falls back to the raw bytes (still base64) if Pillow can't read it."""
    img = _load_rgb(data)
    if img is None:
        return base64.b64encode(data).decode("ascii")
    img.thumbnail((max_edge, max_edge))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def prepare_image(data: bytes) -> str:
    """The base64 the vision model receives: downscaled to ``MAX_IMAGE_EDGE``
    (896 — see config; gemma3 returns empty on a larger image)."""
    return image_to_b64(data, config.MAX_IMAGE_EDGE)


def save_upload_jpeg(data: bytes, path: Path) -> None:
    """Write the uploaded photo to ``path`` as full-resolution JPEG (the pending
    original, kept only until generate). Falls back to the raw bytes."""
    img = _load_rgb(data)
    if img is None:
        path.write_bytes(data)
        return
    img.save(path, format="JPEG", quality=90)


def render_derivative(src: Path, dst: Path) -> None:
    """Render the permanent per-analysis image: ``src`` downscaled to
    ``DERIVATIVE_EDGE`` (720) as JPEG. Copies the source verbatim if it can't be
    decoded, so an analysis always keeps *some* image."""
    try:
        from PIL import Image
        img = Image.open(src).convert("RGB")
        img.thumbnail((config.DERIVATIVE_EDGE, config.DERIVATIVE_EDGE))
        img.save(dst, format="JPEG", quality=88)
    except Exception:  # noqa: BLE001
        shutil.copyfile(src, dst)


# --- step 1: identify -------------------------------------------------------

def identify(image_b64: str, shortlist: list[str] | None = None) -> dict:
    """Vision identification -> structured inventory dict (defaults filled in). An
    optional retrieval-grounding ``shortlist`` (nearest KB flowers) is injected into
    the system prompt to steer naming toward profiled flowers."""
    system = prompts.VISION_SYSTEM
    if shortlist:
        system += prompts.grounding_block(shortlist)
    inventory = broker.chat_json(
        config.VISION_MODEL,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": prompts.VISION_USER, "images": [image_b64]},
        ],
        options={"temperature": 0.2},
    )
    return _normalize_inventory(inventory)


def _normalize_inventory(inventory: dict) -> dict:
    """Coerce a raw/edited inventory into the canonical shape (tolerant of a model
    or a client that omits a field or sends a bare string where a list is expected)."""
    inv = dict(inventory or {})
    flowers = []
    for f in inv.get("flowers") or []:
        if not isinstance(f, dict):
            continue
        colors = f.get("colors")
        if isinstance(colors, str):
            colors = [c.strip() for c in colors.split(",") if c.strip()]
        flowers.append({
            "name": (f.get("name") or "").strip(),
            "colors": colors or [],
            "confidence": f.get("confidence") or "",
            "notes": f.get("notes") or "",
        })
    inv["flowers"] = [f for f in flowers if f["name"]]
    greenery = inv.get("greenery")
    if isinstance(greenery, str):
        greenery = [g.strip() for g in greenery.split(",") if g.strip()]
    inv["greenery"] = greenery or []
    inv["palette"] = (inv.get("palette") or "").strip()
    inv["arrangement"] = (inv.get("arrangement") or "").strip()
    inv["context"] = (inv.get("context") or "").strip()
    return inv


def annotate_inventory(inventory: dict) -> dict:
    """Tag each identified flower with its resolved KB slug + in-library flag so the
    editor can show the ✓ / not-profiled indicator without a per-line round-trip."""
    for f in inventory.get("flowers", []):
        slug = kb.resolve(f.get("name", ""))
        f["slug"] = slug
        f["in_library"] = slug is not None
    return inventory


def _match(inventory: dict) -> tuple[list, list[str]]:
    """Resolve each identified flower to a KB profile. Returns (matched Flowers in
    a stable de-duplicated order, unprofiled names)."""
    matched: list = []
    seen: set[str] = set()
    unprofiled: list[str] = []
    for f in inventory.get("flowers", []):
        name = f.get("name", "")
        slug = kb.resolve(name)
        if slug and slug not in seen:
            flower = kb.get_flower(slug)
            if flower:
                matched.append(flower)
                seen.add(slug)
        elif not slug and name:
            unprofiled.append(name)
    return matched, unprofiled


def _title(inventory: dict, mode: str) -> str:
    palette = (inventory.get("palette") or "").strip()
    arrangement = (inventory.get("arrangement") or "bouquet").strip()
    label = " ".join(x for x in (palette, arrangement) if x) or "bouquet"
    return label[:120]


def _norm_mode(mode: str) -> str:
    """Two output modes. Anything that isn't the expert 'analysis' is the Frenchies
    'florist' description (the UI's primary "Generate Description")."""
    return "analysis" if mode == "analysis" else "florist"


# --- step 2: generate -------------------------------------------------------

def write_report(inventory: dict, matched: list, unprofiled: list[str],
                 mode: str, guidance: str = "") -> str:
    """Write the report/copy from the (edited) inventory + KB context + optional
    florist guidance. The writer runs on the large chat model (see config)."""
    references = {label: kb.reference_excerpt(slug)
                  for slug, label in _CONTEXT_REFERENCES.items()}
    context = prompts.build_context(inventory, matched, unprofiled, references, guidance=guidance)
    if mode == "florist":
        system, task, temp, model = (
            prompts.FLORIST_SYSTEM, prompts.FLORIST_TASK, 0.7, config.DESCRIPTION_MODEL)
    else:
        system, task, temp, model = (
            prompts.ANALYSIS_SYSTEM, prompts.ANALYSIS_TASK, 0.4, config.ANALYSIS_MODEL)
    report = broker.chat(
        model,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": context + task},
        ],
        options={"temperature": temp},
    )
    return _strip_md_fence(report)


def generate(inventory: dict, *, guidance: str = "", mode: str = "florist") -> dict:
    """Step 2 of the pipeline: write the report from a *corrected* inventory. The
    caller has already validated the inventory is non-empty (the writer would
    confabulate a bouquet from an empty one). Returns a result dict the API persists."""
    mode = _norm_mode(mode)
    inventory = _normalize_inventory(inventory)
    matched, unprofiled = _match(inventory)
    report_md = write_report(inventory, matched, unprofiled, mode, guidance)
    writer = config.DESCRIPTION_MODEL if mode == "florist" else config.ANALYSIS_MODEL
    return {
        "mode": mode,
        "title": _title(inventory, mode),
        "inventory": inventory,
        "matched": [f.summary() for f in matched],
        "matched_slugs": [f.slug for f in matched],
        "unprofiled": unprofiled,
        "report_md": report_md,
        "model": f"{config.VISION_MODEL}+{writer}",
    }
