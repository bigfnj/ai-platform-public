"""Slide → Mexican-Spanish transcreation.

This module owns only the slide-specific prompt, cache key, and user message;
the Ollama + caching mechanic lives in edu_media_core.translate.
"""
from edu_media_core import profiles, translate as core

_OPTIONS = {
    "temperature": 0.2,
    # Slide content is short — override the global OLLAMA_CONTEXT_LENGTH so the
    # kv cache stays small enough to fit alongside desktop VRAM usage.
    "num_ctx": 4096,
    # Offload 40 of 64 layers to GPU; rest run on CPU.
    "num_gpu": 40,
}

_SYSTEM_PROMPT = """\
You are a Mexican Spanish transcreation specialist for a student with autism who has a 2nd-grade education level. \
The student is from Mexico, cannot read or write, and learns entirely through spoken audio.

Your job is NOT to translate literally. Your job is to rewrite the content so it is:
- Clear and natural when spoken aloud in Mexican Spanish
- Understandable to a 2nd-grade student with no academic vocabulary
- Appropriate for a student with autism: concrete, literal, no figures of speech, no idioms

STRICT RULES — follow every one, no exceptions:
1. Language: Mexican Spanish (es_MX) only — not neutral Latin American, not Castilian
2. Register: tú throughout — informal, student-facing
3. Vocabulary ceiling: 2nd-grade level. Use the simplest possible word that preserves meaning.
   - "anfitrión" not "huésped" (too formal)
   - "lleva" not "escolta"
   - "personas" not "clientela"
4. Sentence structure: subject → verb → object. One idea per sentence.
5. Maximum sentence length: 10 words. Short is better than long.
6. NO subordinate clauses. NO "que" clauses. NO "cuando/si/aunque" structures.
7. NO comma-separated lists. Each item in the English bullets becomes its own sentence.
8. NO idioms, NO metaphors, NO figures of speech.
9. Repeat the key vocabulary term (the Spanish term) at least once inside the body sentences.
10. Concrete and literal only. Do not add information that was not in the original.

OUTPUT FORMAT — you must return valid JSON, nothing else:
{
  "term_es": "<the Spanish vocabulary term, 1-3 words>",
  "sentences_es": [
    "<sentence 1>",
    "<sentence 2>",
    "<sentence 3>"
  ]
}

EXAMPLE INPUT:
Term: Host
Bullets:
- A person who welcomes guests
- Takes reservations
- Greets customers and shows them to their tables

CORRECT OUTPUT:
{
  "term_es": "Anfitrión",
  "sentences_es": [
    "El anfitrión trabaja en el restaurante.",
    "El anfitrión saluda a las personas.",
    "El anfitrión los lleva a su mesa."
  ]
}

WRONG OUTPUT (do not do this):
{
  "term_es": "Anfitrión",
  "sentences_es": [
    "El anfitrión da la bienvenida a los huéspedes, toma las reservaciones y los guía a sus mesas."
  ]
}
Why wrong: one long sentence, lists read aloud, "huéspedes" is too formal.
"""

PROFILE = profiles.register(profiles.Profile(
    key="slide_autism_grade2",
    label="Slide transcreation (2nd-grade, autism)",
    system_prompt=_SYSTEM_PROMPT,
    options=_OPTIONS,
    required_keys=("term_es", "sentences_es"),
))


def _build_user_message(slide: dict) -> str:
    lines = [f"Term: {slide['title']}"]
    content_lines = slide.get("bullets") or slide.get("paragraphs") or []
    if content_lines:
        lines.append("Bullets:")
        for item in content_lines:
            lines.append(f"- {item}")
    return "\n".join(lines)


def translate_slide(slide: dict) -> dict:
    """
    Translate a single content slide using Qwen2.5 32B via Ollama.

    Returns {"term_es": str, "sentences_es": list[str]}. Results are cached by
    content hash to avoid re-translating unchanged slides.
    """
    return core.translate_cached(
        system_prompt=PROFILE.system_prompt,
        user_message=_build_user_message(slide),
        options=PROFILE.options,
        required_keys=PROFILE.required_keys,
    )


def is_cached(slide: dict) -> bool:
    """Return True if this slide's translation is already in the shared cache."""
    return core.is_cached(PROFILE.system_prompt, _build_user_message(slide))
