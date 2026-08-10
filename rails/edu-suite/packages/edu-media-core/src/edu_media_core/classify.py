"""Classify extracted slides as header/content/empty/duplicate and assign weeks."""
import re

_WEEK_PATTERN = re.compile(r"^week\s+(\d+)$", re.IGNORECASE)

# Slide titles that are section markers, not vocabulary content
_SECTION_HEADERS = {
    "language arts", "math", "science", "social studies",
    "5 senses", "food service industry",
}


def classify_slides(slides: list[dict]) -> list[dict]:
    """
    Classify each slide as 'header' (skip) or 'content' (process).
    Also assigns a week number and deduplicates exact title+content repeats
    within a week (PDF sometimes contains duplicate pages).

    Adds keys to each slide dict:
      "type": "header" | "content" | "empty" | "duplicate"
      "week": int | None
    """
    current_week = None
    seen: set[tuple] = set()
    classified = []

    for slide in slides:
        slide = dict(slide)  # don't mutate the input
        slide_type = _classify_one(slide)
        slide["type"] = slide_type

        week_match = _WEEK_PATTERN.match(slide["title"])
        if week_match:
            current_week = int(week_match.group(1))

        slide["week"] = current_week if slide_type == "content" else None

        if slide_type == "content":
            key = (current_week, slide["title"].lower(), slide["raw_text"].strip())
            if key in seen:
                slide["type"] = "duplicate"
                slide["week"] = None
            else:
                seen.add(key)

        classified.append(slide)

    return classified


def _classify_one(slide: dict) -> str:
    title = slide["title"].strip()

    if not title:
        return "empty"

    if title.lower() in _SECTION_HEADERS:
        return "header"

    if _WEEK_PATTERN.match(title):
        return "header"

    has_content = bool(slide["bullets"] or slide["paragraphs"])
    return "content" if has_content else "header"
