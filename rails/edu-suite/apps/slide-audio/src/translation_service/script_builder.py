_PAUSE_SECONDS = 1.5


def build_script(slide: dict, translation: dict) -> list[dict]:
    """
    Build the ordered list of audio segments for one content slide.

    Each segment is a dict with:
      {"lang": "en"|"es"|"pause", "text": str, "duration": float|None}

    Order: English term → English sentences → 1.5s pause → Spanish term → Spanish sentences.

    English sentences are sourced from the slide's bullets or paragraphs.
    Spanish sentences come from the translation dict returned by translator.translate_slide().
    """
    segments = []

    # English term (the slide title)
    segments.append({"lang": "en", "type": "term", "text": slide["title"], "duration": None})

    # English body — bullets preferred, fall back to paragraphs
    english_lines = slide.get("bullets") or slide.get("paragraphs") or []
    for line in english_lines:
        # Each bullet is already a phrase; wrap it into a full sentence if it isn't one
        text = line.strip()
        if text and not text.endswith((".", "!", "?")):
            text = text + "."
        segments.append({"lang": "en", "type": "sentence", "text": text, "duration": None})

    # Silence between English and Spanish
    segments.append({"lang": "pause", "type": "pause", "text": "", "duration": _PAUSE_SECONDS})

    # Spanish term
    segments.append({"lang": "es", "type": "term", "text": translation["term_es"], "duration": None})

    # Spanish sentences
    for sentence in translation["sentences_es"]:
        segments.append({"lang": "es", "type": "sentence", "text": sentence.strip(), "duration": None})

    return segments


def format_script_preview(slide: dict, segments: list[dict]) -> str:
    """
    Return a human-readable preview of the script for dry-run review.
    """
    lines = [f"=== Slide {slide['slide_number']}: {slide['title']} (Week {slide['week']}) ==="]
    for seg in segments:
        if seg["lang"] == "pause":
            lines.append(f"  [pause {seg['duration']}s]")
        else:
            lines.append(f"  [{seg['lang'].upper()}] {seg['text']}")
    return "\n".join(lines)
