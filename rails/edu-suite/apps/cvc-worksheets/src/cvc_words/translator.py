"""CVC word → child-friendly Mexican Spanish.

This module owns only the word-specific prompt and result handling; the Ollama +
caching mechanic lives in edu_media_core.translate.
"""
from __future__ import annotations

from edu_media_core import broker_media as core, profiles  # translate via the platform broker

from .words import Word

_OPTIONS = {
    "temperature": 0.1,
    # The 15GB q3 model loads fully onto a 24GB card, so no num_gpu override.
    "num_ctx": 2048,
}

_SYSTEM_PROMPT = """\
You translate English words into Mexican Spanish for a phonics worksheet used by young \
children (age 4-7). Translate by MEANING only.

CRITICAL RULE — NEVER match by sound or spelling:
The English and Spanish words will usually look and sound COMPLETELY different. \
Do NOT pick a Spanish word just because it starts with the same letters or sounds similar. \
That is always wrong.
  - "jab" means a quick punch -> "golpe"   (NEVER "jabón"/soap)
  - "zap" means to hit with electricity -> "rayo"   (NEVER "zapato"/shoe)
  - "ram" is a male sheep -> "carnero"   (NEVER "rama"/branch)
  - "lag" means a delay -> "retraso"   (NEVER "sapo"/toad)
  - "sob" means to cry hard -> "llanto"   (NEVER "primo"/cousin)

Work in three steps and return all three fields:
1. meaning_en: a 3-8 word plain-English definition of the word's most common meaning.
2. word_es: the single everyday Mexican Spanish word for that meaning. Use the simplest word \
a 4-7 year old would know. Prefer concrete, picturable words. For abstract words, pick the \
closest concrete idea a child could picture (e.g. "pep" = energy/cheer -> "ánimo").
3. image_query: a 2-4 word English search query for a simple cartoon illustration of the MEANING.

Return ONLY valid JSON, nothing else:
{"meaning_en": "<definition>", "word_es": "<single Spanish word>", "image_query": "<2-4 words>"}

EXAMPLES:
Input: web
{"meaning_en": "a spider's web", "word_es": "telaraña", "image_query": "spider web cartoon"}

Input: jab
{"meaning_en": "a quick punch", "word_es": "golpe", "image_query": "cartoon fist punch"}

Input: ram
{"meaning_en": "a male sheep", "word_es": "carnero", "image_query": "cartoon ram sheep"}

Input: cot
{"meaning_en": "a small simple bed", "word_es": "catre", "image_query": "camping cot bed cartoon"}
"""

PROFILE = profiles.register(profiles.Profile(
    key="cvc_phonics",
    label="CVC phonics (age 4-7)",
    system_prompt=_SYSTEM_PROMPT,
    options=_OPTIONS,
    required_keys=("word_es", "image_query"),
))


def clear_cache() -> None:
    """Delete the shared translation cache so the next run re-queries the LLM."""
    core.clear_cache()


def translate_word(word: Word, guidance: str = "") -> dict[str, str]:
    """Return {"word_es": str, "image_query": str}, using the shared cache when available.

    ``guidance`` is optional in-scope teacher guidance (from the dashboard's Additional
    instructions -> /api/interpret): it nudges the Spanish word choice and picture subject.
    It's appended to the system prompt, so the content-addressed cache keys a guided run
    apart from an unguided one automatically."""
    guidance = (guidance or "").strip()
    system = PROFILE.system_prompt
    if guidance:
        system += (f"\n\nADDITIONAL TEACHER GUIDANCE (apply to word_es and image_query; "
                   f"word_es must still be a single everyday word): {guidance}")
    return core.translate_cached(
        system_prompt=system,
        user_message=f"Word: {word.en}",
        options=PROFILE.options,
        required_keys=PROFILE.required_keys,
    )


def translate_all(words: list[Word], verbose: bool = True) -> None:
    """Translate any words with missing es/image_query. Mutates words in-place."""
    pending = [w for w in words if w.needs_translation]
    if not pending:
        if verbose:
            print("  All words already translated.")
        return

    for i, word in enumerate(pending, 1):
        if verbose:
            print(f"  [{i}/{len(pending)}] Translating '{word.en}'...", end=" ", flush=True)
        result = translate_word(word)
        word.es = result["word_es"]
        word.image_query = result["image_query"]
        if verbose:
            print(f"→ {word.es!r}  (image: {word.image_query!r})")
