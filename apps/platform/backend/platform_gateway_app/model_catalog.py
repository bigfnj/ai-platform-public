"""Editorial metadata for the admin Models pool: a CATEGORY and a one-line "good at / why" for
each model, so the tab reads as grouped, explained sections instead of a flat list.

The broker already tells us a model's `class` (embed vs heavy) and `vision` flag; this adds the
human layer on top. Keyed by a name glob, most-specific first (fnmatch). A model that matches
nothing falls back to a category derived from its class/vision with a generic blurb — so a freshly
pulled model is never *uncatalogued*, just not yet hand-described (and the scheduled model-scan /
Refresh will surface it the moment it's installed).
"""
from __future__ import annotations

import fnmatch

# category id -> (display label, sort order). The Models tab groups by this.
CATEGORIES: dict[str, tuple[str, int]] = {
    "chat": ("Chat & instruction-following", 0),
    "reasoning": ("Reasoning & long-form", 1),
    "vision": ("Vision (multimodal)", 2),
    "code": ("Code", 3),
    "embed": ("Embedding (retrieval)", 4),
    "image": ("Image generation", 5),
    "other": ("Other", 9),
}

# (glob, category, blurb) — FIRST match wins, so order specific → general.
_ENTRIES: list[tuple[str, str, str]] = [
    ("bge-m3*", "embed",
     "The platform's default retrieval embedder — turns text into vectors for RAG/semantic "
     "search. Multilingual, strong recall; light enough to stay resident in VRAM alongside a "
     "heavy chat model, so retrieval never costs a model swap."),
    ("embeddinggemma*", "embed",
     "Google's EmbeddingGemma 300M — a compact, multilingual on-device text embedder with "
     "Matryoshka (truncatable) dimensions. A lighter alternative to bge-m3 for RAG; benchmarked "
     "against it in the ai-playground Embedding Lab."),
    ("qwen3-embedding*", "embed",
     "Alibaba's Qwen3-Embedding 0.6B — a tiny multilingual retrieval embedder that punches above "
     "its size on MTEB. The lean-box embedding option; compared with bge-m3 in the ai-playground "
     "Embedding Lab."),
    ("*embedding*", "embed",
     "A retrieval embedder — vectorises text for search/RAG. Not a chat model; used by the "
     "ai-playground Embedding Lab to benchmark alternatives."),
    ("*embed*", "embed", "Retrieval embedder — vectorises text for semantic search / RAG."),
    ("mistral-small3*", "chat",
     "The everyday workhorse — a 24B general instruction-follower and the platform's default "
     "@chat. Reliable prose and tool-following at low latency; backs edu, finance chat, job-aid, "
     "co-worker synthesis, and the SMB/Gemini RAG answers."),
    ("qwen3.6*", "reasoning",
     "Deep reasoning and long-form — a 27B model for multi-step problems and long documents "
     "(finance fraud, IEP goal writing, bouquet analysis). Higher quality than the default chat "
     "model, slower; reach for it when the answer matters more than the milliseconds."),
    ("qwen*coder*", "code",
     "Qwen Coder — tuned for generating and editing source; better at code than a general "
     "chat model of the same size."),
    ("*coder*", "code", "Code-specialised model — stronger at writing/editing source than general chat."),
    ("gemma4*:26b", "vision",
     "The pool's default multimodal model — reads images as well as text (recipe-photo import, "
     "bouquet flower identification). Use it for any slot that must see a picture."),
    ("gemma4*:12b", "chat",
     "A snappy general chat model — lighter Gemma for latency-sensitive helpers (terminal-fun "
     "assistant, gemini-cx). Good quality for its speed; not for the heaviest reasoning."),
    ("gemma4*", "vision", "Gemma multimodal family — handles text and vision."),
    ("gemma3*", "chat",
     "Small, fast Gemma — the lean-install default (8 GB boxes). Multimodal and cheap; the "
     "fallback when VRAM is tight rather than the first-choice quality model."),
    ("nemotron*", "chat",
     "NVIDIA's own small generator — quick first token, used for the ai-playground RAG demo "
     "(the showcase of a grounded, cited answer on the NVIDIA card). Fast, short-answer oriented."),
    ("flux*", "image",
     "FLUX text-to-image — renders the illustrated recipe-card icons. A media-worker backend, "
     "not an Ollama chat model, so it appears only when a render is running."),
    ("sdxl*", "image", "SDXL text-to-image — a faster, lighter image generator than FLUX."),
    ("dolphin-mistral*", "chat",
     "An uncensored Mistral 7B fine-tune (Cognitive Computations' Dolphin) — small and fast, "
     "follows instructions with no built-in refusals, so its behaviour is set entirely by the "
     "system prompt. For legitimate tasks a guardrailed model balks at; steer it deliberately."),
    ("dolphin3*", "chat",
     "Dolphin 3.0 on Llama 3.1 8B — a compact, steerable general/agentic instruct model (chat, "
     "code, function-calling) with alignment left to your system prompt rather than baked in. "
     "The newer, more capable sibling of dolphin-mistral."),
    ("llama2-uncensored*", "chat",
     "An older (2023) Llama 2 7B uncensored fine-tune, kept for legacy prompts and comparison. "
     "Noticeably weaker than the Gemma/Mistral/Qwen models here — prefer dolphin3 or "
     "mistral-small for real work."),
    ("*mistral*", "chat", "A Mistral general chat model."),
    ("*qwen*", "chat", "A Qwen general chat model."),
    ("*llama*", "chat", "A Llama general chat model."),
]


VALID_CATEGORIES = set(CATEGORIES)


def curated(name: str) -> dict[str, str] | None:
    """The hand-authored {category, blurb} for a model, or None if it matches no glob (i.e. it's
    a model we haven't described — the scheduled scan will LLM-generate one for it)."""
    low = (name or "").lower()
    for glob, cat, blurb in _ENTRIES:
        if fnmatch.fnmatch(low, glob):
            return {"category": cat, "blurb": blurb}
    return None


def fallback(klass: str | None = None, vision: bool = False) -> dict[str, str]:
    """Class/vision-derived category + placeholder blurb, for a model with neither a curated
    entry nor a generated one yet (grouped correctly, just not described)."""
    if klass == "embed":
        return {"category": "embed", "blurb": "A retrieval embedder (not yet described)."}
    if klass == "image":
        return {"category": "image", "blurb": "An image-generation backend (not yet described)."}
    if vision:
        return {"category": "vision", "blurb": "A multimodal model — text and vision (not yet described)."}
    return {"category": "chat", "blurb": "A general-purpose model (not yet described)."}
