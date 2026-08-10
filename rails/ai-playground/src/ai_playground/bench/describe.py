"""Draft a plain-English "about" line for a model an admin is adding.

Grounds on the model's Hugging Face card when it can find one (the "quick internet lookup"): if no
repo is given, it searches the Hub by name; then it reads that repo's README and asks the broker LLM
to condense it into one warm, jargon-free sentence. The draft is returned for the admin to approve
or edit before saving — never auto-published. Fully best-effort: if grounding fails it still drafts
from the name, and if the broker is down the caller surfaces a clean error.
"""
from __future__ import annotations

import os
import re

from ai_playground import broker, config

# A capable instruction-follower keeps the one-liner clean. @chat (mistral-small) returns clean
# prose via the buffered endpoint where some models return empty. Overridable; resolves via @roles.
_MODEL = os.environ.get("AI_PLAYGROUND_DESCRIBE_MODEL", "@chat")

_SYS = (
    "You write ONE short, plain-English sentence (max 22 words) that tells a NON-technical reader "
    "why an embedding model was made and what it is good at. Warm and benefit-first, no jargon, no "
    "statistics, no marketing hype, no markdown or quotes. Output only the sentence."
)


def _search_repo(name: str) -> str | None:
    from huggingface_hub import HfApi
    api = HfApi()
    base = re.sub(r"[:@].*$", "", name or "").strip()   # drop an ollama :tag
    for q in dict.fromkeys([name, base]):               # de-duped, name first
        if not q:
            continue
        try:
            hits = list(api.list_models(search=q, limit=5))
        except Exception:  # noqa: BLE001
            hits = []
        for h in hits:
            rid = getattr(h, "id", None) or getattr(h, "modelId", None)
            if rid:
                return rid
    return None


def _card_text(repo: str) -> str | None:
    """First chunk of a repo's README prose (frontmatter + headings/badges stripped)."""
    from huggingface_hub import hf_hub_download
    try:
        path = hf_hub_download(repo, "README.md")
    except Exception:  # noqa: BLE001
        return None
    txt = open(path, encoding="utf-8", errors="ignore").read()
    if txt.startswith("---"):
        end = txt.find("\n---", 3)
        if end != -1:
            txt = txt[end + 4:]
    lines = [ln.strip() for ln in txt.splitlines()
             if ln.strip() and not ln.lstrip().startswith(("#", "!", "|", "<", "-", "*", "["))]
    return " ".join(lines)[:1200] or None


def _clean(text: str) -> str:
    text = re.sub(r"(?is)<think>.*?</think>", "", text or "").strip()
    for line in text.splitlines():
        line = line.strip().strip('"').strip("*").strip("-").strip()
        low = line.lower()
        if len(line) >= 20 and not low.startswith(("here", "sure", "about", "model:", "the model is called")):
            return line[:220]
    return text.strip().strip('"')[:220]


def describe(name: str, family: str | None = None, hf_repo: str | None = None,
             broker_model: str | None = None) -> dict:
    repo = hf_repo or _search_repo(broker_model or name)
    card = _card_text(repo) if repo else None
    context = card or f"Model name: {name}. Family: {family or 'unknown'}. It is a text embedding model."
    user = (f"Model: {name}\n\nReference (may be noisy, use only what is relevant):\n{context}\n\n"
            "Write the one-sentence 'about' line now.")
    out = broker.chat(_MODEL,
                      [{"role": "system", "content": _SYS}, {"role": "user", "content": user}],
                      options={"num_predict": 90, "temperature": 0.3})
    about = _clean(out)
    if len(about) < 20 and card:                        # LLM gave nothing usable: fall back to the card
        about = (re.split(r"(?<=[.!?])\s", card)[0] or card)[:220]
    return {"about": about, "grounded_from": repo}
