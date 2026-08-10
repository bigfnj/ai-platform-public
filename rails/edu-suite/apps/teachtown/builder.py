"""Draft a TeachTown interactive unit from uploaded worksheet PDFs, using qwen.

The draft is reviewable/editable: it produces the same `unit` shape the
interactive site consumes, so it can be built directly or edited first.

  draft_unit(files, unit_key, unit_name, progress) -> unit dict
  full_data(unit_key, unit) -> the data.json the site loads
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Callable

_ROOT = Path(__file__).resolve().parents[2]  # apps/teachtown/builder.py -> edu-suite
sys.path.insert(0, str(_ROOT / "packages" / "edu-media-core" / "src"))

from edu_media_core import pdf as core_pdf  # noqa: E402
from edu_media_core import broker_media as core_translate  # noqa: E402 (drafting via broker)

# The interactive site's fixed subjects/colors and subject filter.
META = {
    "ELA": ["📚", "#ff6b6b"],
    "Math": ["🔢", "#5b8def"],
    "Science": ["🔬", "#40b983"],
    "Social Studies": ["🌎", "#a56de2"],
}

_SUBJECT_RULES = [
    ("Math", ("math", "warm up", "warmup", "warm-up", "area", "coordinate", "geometry")),
    ("Science", ("science", "food and water", "nutrient", "molecule", "energy", "matter")),
    ("Social Studies", ("social", "imperialism", "nationalism", "unif", "history", "geography", "civics")),
    ("ELA", ("ela", "reading", "comp", "context", "suffix", "hyperbole", "vocab", "writing", "story", "grammar")),
]

_Progress = Callable[[str], None]

# Lesson-plan files (named "* Lesson Plan.pdf") are the VOCABULARY source — the
# words + kid-friendly definitions students must learn. They do NOT become worksheets.
# Works from the lesson plan's text, or (image-only lesson plan) an attached picture.
_VOCAB_PROMPT = """\
You extract the vocabulary list from a teacher's LESSON PLAN: each term AND the
definition GIVEN IN THE PLAN, transcribed EXACTLY as written. Do NOT rephrase, shorten,
simplify, or invent definitions — copy the lesson plan's own wording verbatim. (These
English definitions get translated to Spanish later; your job here is faithful
extraction, not writing definitions.) The lesson plan is given as text, and/or an
attached image if it is picture-based — use whichever is provided.

Lesson plans are long (learning objectives, materials, procedures). The words + their
definitions live in a VOCABULARY section — often titled "Introducing the Vocabulary",
"Vocabulary", "Definition and Picture Cards", or a word→definition list. FIND that
section and extract from it; ignore the objectives/materials/procedure boilerplate.

Return ONLY valid JSON:
{"vocab": [{"word": "<term, as written>", "def": "<its definition, exactly as written>"}]}

Extract every vocabulary word the plan defines (up to 12), in order. Do not add words
that are not in the plan, and do not change the given definitions.
"""

# Every other file becomes an INTERACTIVE WORKSHEET — one short activity per file. The
# model also classifies the SUBJECT from the content (filenames are unreliable), and can
# read an attached image when the worksheet is picture-based with little/no text.
_MISSION_PROMPT = """\
You turn a special-education worksheet into ONE interactive activity. The worksheet is
given as text, and/or an attached image if it is picture-based — use whichever is provided.
Keep it simple, concrete, and classroom-appropriate.

Return ONLY valid JSON:
{"subject": "<one of: ELA, Math, Science, Social Studies>",
 "questions": <how many separate items the student must answer; 0 if none>,
 "mission": {"title": "<short name>", "prompt": "<one clear instruction>"},
 "activity": <the interactive exercise, see below>}

Choose "subject" as EXACTLY one of: ELA, Math, Science, Social Studies — from the content
(e.g. World War / civics -> Social Studies; the water cycle -> Science).

"activity": pick the ONE "kind" that best fits the worksheet and fill ONLY that kind's
fields, copying wording from the worksheet:
- "match"     -> {"kind":"match","pairs":[{"left":"<term/label>","right":"<its match>"}]}
                 (2-8 pairs; e.g. a term with its definition, or a label with its picture name)
- "drag-drop" -> {"kind":"drag-drop","items":["<item>", ...],
                  "targets":[{"label":"<slot>","answer":"<the item that belongs>"}]}
                 (every answer must be one of the items)
- "highlight" -> {"kind":"highlight","questions":[{"prompt":"<q>",
                  "options":["<a>","<b>","<c>","<d>"],"answer":"<correct option, exactly as written>"}]}
- "fill-in"   -> {"kind":"fill-in","questions":[{"prompt":"<q>","answer":"<expected answer, or \\"\\" if open-ended>"}]}
- "worksheet" -> {"kind":"worksheet"}   (LAST RESORT ONLY — see below)

Base everything strictly on the worksheet, but PREFER the simplest interactive kind that
fits its content: a picture sheet with labels/parts is usually "match" or "drag-drop";
a diagram to name is "match"; a question sheet is "highlight" or "fill-in". If you can see
ANY terms, labels, or questions on the sheet, build one of the four interactive kinds from
them. Use "worksheet" ONLY when the sheet has no answerable content at all (e.g. a blank
drawing/coloring page). Do not fall back to "worksheet" just because you are unsure.
"""

_LEARN_PROMPT = """\
Write ONE short, simple sentence summarizing what students practice this week,
given these activity titles. Return ONLY JSON: {"learn": "<one sentence>"}
"""


def _subject(name: str) -> str:
    low = name.lower()
    for label, keys in _SUBJECT_RULES:
        if any(k in low for k in keys):
            return label
    return "ELA"  # default so it's always a valid site subject


def is_lesson_plan(name: str) -> bool:
    """Files named like 'ELA Lesson Plan.pdf' are the vocabulary source (word +
    definition), not an interactive worksheet. Matched on the filename."""
    return bool(re.search(r"lesson\s*plan", name, re.IGNORECASE))


_WEEK_RE = re.compile(r"week\s*0*(\d+)", re.IGNORECASE)


def _rel(f: Path, input_root: Path | None) -> Path:
    if input_root:
        try:
            return f.relative_to(input_root)
        except ValueError:
            pass
    return Path(f.name)


def rel_worksheet_name(f: Path, input_root: Path | None) -> str:
    """Stable per-worksheet key: the file's path *below* the uploaded master
    folder (e.g. ``Week 1/reading.pdf``), or just the basename for a flat upload.
    Used identically at draft (mission ref) and build (image manifest) so a unit's
    missions always resolve to their worksheet — and two same-named files in
    different week folders don't collide."""
    parts = _rel(f, input_root).parts
    if len(parts) > 1:  # drop the master folder segment
        parts = parts[1:]
    return "/".join(parts)


def week_of(f: Path, input_root: Path | None) -> int:
    """Prefer an explicit ``Week N`` *folder* in the path (how teachers organize
    a unit); fall back to ``week N`` in the filename; otherwise return 0, the
    "Overview" bucket where top-level / non-week 'core' material lands (the site
    renders week 0 as an Overview section, not Week 0)."""
    for seg in _rel(f, input_root).parts[:-1]:  # folders only, not the filename
        m = _WEEK_RE.search(seg)
        if m:
            return int(m.group(1))
    m = _WEEK_RE.search(f.name)
    return int(m.group(1)) if m else 0


def master_folder(files: list[Path], input_root: Path | None) -> str | None:
    """The uploaded top-level folder name (the unit), if the files came in as a
    folder rather than loose files."""
    for f in files:
        parts = _rel(f, input_root).parts
        if len(parts) > 1:
            return parts[0]
    return None


# Below this many words we treat a PDF as image-based and hand the model the rendered
# page instead of its (missing/garbled) text — our drafting model is vision-capable.
_MIN_WORDS = 8


def _with_guidance(system: str, guidance: str) -> str:
    # The teacher's validated, in-scope guidance (from /api/interpret). Applied as far
    # as the source allows; it never overrides "base it on the source content".
    if guidance:
        system += f"\n\nTeacher's additional guidance (follow it where the source allows): {guidance}"
    return system


def _page_image(f: Path) -> list[str] | None:
    """The first page as a base64 PNG for a vision call, or None if it can't render.
    Rendered wide (1600px) so small diagram/map labels stay legible to the vision model —
    a 1024px page loses fine print, which starved picture-based worksheet drafting."""
    try:
        return [core_pdf.render_page_b64(str(f), width=1600)]
    except Exception:
        return None


def _body(text: str, kind: str, cap: int = 3000) -> str:
    t = (text or "").strip()
    return t[:cap] if t else f"(the {kind} is picture-based; read the attached image)"


def _vocab_draft(name: str, text: str, images: list[str] | None, guidance: str) -> dict:
    # Lesson plans are long and the vocabulary section sits well past the preamble, so
    # send much more text with a context window big enough to hold it (default 3000/4096
    # only saw objectives/materials and produced junk).
    return core_translate.chat_json(
        _with_guidance(_VOCAB_PROMPT, guidance),
        f"Lesson plan file: {name}\n{_body(text, 'lesson plan', cap=24000)}",
        options={"temperature": 0.2, "num_ctx": 16384}, images=images)


def _mission_draft(name: str, text: str, images: list[str] | None, guidance: str) -> dict:
    return core_translate.chat_json(
        _with_guidance(_MISSION_PROMPT, guidance),
        f"Worksheet file: {name}\n{_body(text, 'worksheet')}",
        options={"temperature": 0.3, "num_ctx": 4096}, images=images)


def _cs(x) -> str:
    return str(x).strip() if x is not None else ""


def _norm(s: str) -> str:
    """Loose key for matching an answer to an option/item: casefold, collapse whitespace,
    strip surrounding quotes/punctuation. Lets a semantically-correct answer that differs
    only by case, spacing, or a trailing period still match — instead of the whole activity
    being thrown out and downgraded to a picture worksheet."""
    return re.sub(r"\s+", " ", s).strip().strip("\"'.,:;!?").casefold()


def _valid_activity(act: object) -> dict:
    """Coerce the model's `activity` into a safe, known shape; fall back to the plain
    picture-worksheet ('worksheet') on anything malformed. Keeps the frontend simple:
    it only ever sees a validated kind + fields, or nothing (annotate-on-image)."""
    if not isinstance(act, dict):
        return {"kind": "worksheet"}
    kind = act.get("kind")
    if kind == "match":
        pairs = [{"left": _cs(p.get("left")), "right": _cs(p.get("right"))}
                 for p in (act.get("pairs") or []) if isinstance(p, dict)]
        pairs = [p for p in pairs if p["left"] and p["right"]]
        return {"kind": "match", "pairs": pairs[:8]} if len(pairs) >= 2 else {"kind": "worksheet"}
    if kind == "drag-drop":
        items = [_cs(i) for i in (act.get("items") or []) if _cs(i)]
        by_norm = {_norm(i): i for i in items}  # loose answer->canonical-item lookup
        targets = []
        for t in (act.get("targets") or []):
            if not isinstance(t, dict):
                continue
            label, canon = _cs(t.get("label")), by_norm.get(_norm(_cs(t.get("answer"))))
            if label and canon:  # snap the answer to the exact item string the UI expects
                targets.append({"label": label, "answer": canon})
        return ({"kind": "drag-drop", "items": items[:10], "targets": targets[:10]}
                if items and targets else {"kind": "worksheet"})
    if kind in ("highlight", "fill-in"):
        qs = []
        for q in (act.get("questions") or []):
            if not isinstance(q, dict) or not _cs(q.get("prompt")):
                continue
            prompt = _cs(q.get("prompt"))
            if kind == "highlight":
                opts = [_cs(o) for o in (q.get("options") or []) if _cs(o)]
                canon = {_norm(o): o for o in opts}.get(_norm(_cs(q.get("answer"))))
                if len(opts) >= 2 and canon:  # snap to the exact option string
                    qs.append({"prompt": prompt, "options": opts[:6], "answer": canon})
            else:  # fill-in
                qs.append({"prompt": prompt, "answer": _cs(q.get("answer"))})
        return {"kind": kind, "questions": qs[:12]} if qs else {"kind": "worksheet"}
    return {"kind": "worksheet"}


def _apply_mission(d: dict, *, subj: str, title: str) -> tuple:
    """Fold a mission-draft dict into concrete worksheet fields (with fallbacks), returning
    (subject, title, prompt, mtype, options, nq, activity). Isolated so the caller can run
    it on the first (text) draft and again on an image-retry draft with identical handling."""
    prompt, mtype, options, nq = "Complete the worksheet.", "type", [], 0
    m = d.get("mission") or {}
    if d.get("subject") in META:
        subj = d["subject"]  # content-based subject beats the filename guess
    q = d.get("questions")
    if isinstance(q, (int, float)) and 0 <= int(q) <= 50:
        nq = int(q)  # how many answer boxes the picture worksheet needs
    if m.get("title"):
        title = str(m["title"])[:60]
    if m.get("prompt"):
        prompt = str(m["prompt"])
    if m.get("type") in ("choice", "type", "sort"):
        mtype = m["type"]
    options = m.get("options") or []
    return subj, title, prompt, mtype, options, nq, _valid_activity(d.get("activity"))


def draft_unit(files: list[Path], unit_key: str, unit_name: str,
               input_root: Path | None = None,
               guidance: str = "",
               progress: _Progress | None = None) -> dict:
    p = progress or (lambda m: None)
    weeks: dict[int, dict] = {}
    missions: list[list] = []
    activities: dict[str, dict] = {}   # worksheetRef -> validated interactive activity
    skipped: list[str] = []
    if guidance:
        p(f"applying your instructions: {guidance}")

    for f in files:
        name = f.name
        rel = rel_worksheet_name(f, input_root)
        fallback_subj, wk = _subject(name), week_of(f, input_root)
        p(f"reading {rel}")
        try:
            slides = core_pdf.read_slides(str(f))
            text = "\n".join(s["raw_text"] for s in slides)
        except Exception as e:
            text = ""
            p(f"⚠ could not read text from {rel}: {e}")
        # Little/no text -> treat as an image worksheet: hand the vision model the page.
        images = _page_image(f) if len(text.split()) < _MIN_WORDS else None
        via = " · image" if images else ""
        entry = weeks.setdefault(wk, {"vocab": [], "titles": []})

        if is_lesson_plan(name):
            # Lesson plans are the vocabulary source for their subject (named by subject),
            # not a worksheet. Need either text or a rendered image to read from.
            if not text.strip() and not images:
                p(f"⚠ {rel}: no readable text or image — skipped")
                skipped.append(rel)
                continue
            p(f"reading vocabulary from {rel} (Week {wk}, {fallback_subj}{via})")
            try:
                d = _vocab_draft(name, text, images, guidance)
            except Exception as e:
                p(f"⚠ could not read vocabulary from {rel}: {e}")
                skipped.append(rel)
                continue
            for v in (d.get("vocab") or [])[:8]:
                w, dfn = v.get("word"), v.get("def")
                if w and dfn:
                    entry["vocab"].append([w, dfn, fallback_subj])  # tagged by subject
        else:
            # Every other file becomes an interactive worksheet — and is NEVER dropped.
            # The model infers the subject from content and can read an image; if drafting
            # fails, we still include it as a picture worksheet the student annotates.
            p(f"building interactive worksheet for {rel} (Week {wk}{via})")
            base_title = Path(name).stem[:60]
            subj, title = fallback_subj, base_title
            prompt, mtype, options, nq = "Complete the worksheet.", "type", [], 0
            act = {"kind": "worksheet"}
            try:
                subj, title, prompt, mtype, options, nq, act = _apply_mission(
                    _mission_draft(name, text, images, guidance),
                    subj=fallback_subj, title=base_title)
            except Exception as e:
                p(f"⚠ couldn't auto-build {rel}; including it as a picture worksheet: {e}")
            # Escalate: a "worksheet" result from a TEXT-ONLY pass almost always means the
            # sheet is picture-based (map/diagram) and the model never saw it. Retry once
            # with the rendered page so the vision model can read the labels/parts it needs
            # to build a real activity — the exact case (e.g. "Causes of World War I") that
            # used to silently degrade to annotate-on-image.
            if act.get("kind") == "worksheet" and images is None:
                retry_imgs = _page_image(f)
                if retry_imgs:
                    p(f"  · {rel}: no activity from text — retrying with the page image")
                    try:
                        subj, title, prompt, mtype, options, nq, act = _apply_mission(
                            _mission_draft(name, text, retry_imgs, guidance),
                            subj=fallback_subj, title=base_title)
                    except Exception as e:
                        p(f"⚠ image retry failed for {rel}; keeping picture worksheet: {e}")
            ref = f"{unit_key}/{rel}"
            missions.append([wk, subj, title, prompt, mtype, options, ref, nq])
            entry["titles"].append(title)
            if act.get("kind") != "worksheet":
                activities[ref] = act  # only real interactions; else annotate-on-image fallback

    has_vocab = any(w["vocab"] for w in weeks.values())
    if not missions and not has_vocab:
        tried = len(files)
        raise ValueError(
            f"No usable content found: tried {tried} PDF(s), skipped {len(skipped)}"
            + (f" ({', '.join(skipped[:8])}{'…' if len(skipped) > 8 else ''})" if skipped else "")
            + ". Check the folder has openable PDFs (lesson plans for vocabulary, "
            "worksheets for activities)."
        )
    if skipped:
        p(f"note: {len(skipped)} file(s) skipped, {len(missions)} worksheet(s) drafted")

    week_info: dict[str, dict] = {}
    for wk in sorted(weeks):
        titles = weeks[wk]["titles"]
        if wk == 0:  # the Overview bucket — a fixed summary, no per-week LLM call
            learn = "Overview and core materials for the whole unit."
        elif titles:
            try:
                learn = core_translate.chat_json(
                    _LEARN_PROMPT, "Activities: " + ", ".join(titles),
                    options={"temperature": 0.3, "num_ctx": 1024}).get("learn", "")
            except Exception:
                learn = "This week you will practice new skills."
        else:  # vocabulary-only week (lesson plans but no worksheets)
            learn = "Key vocabulary for this part of the unit."
        seen, vocab = set(), []
        for w, dfn, subj in weeks[wk]["vocab"]:
            if w.lower() in seen:
                continue
            seen.add(w.lower())
            vocab.append([w, dfn, subj])
        week_info[str(wk)] = {"learn": learn, "v": vocab}

    return {
        "label": unit_name,
        "hero": {"h1": unit_name,
                 "p": f"Explore {unit_name} with reading, math, science, and social studies."},
        "weekInfo": week_info,
        "missions": missions,
        "activities": activities,
    }


def full_data(unit_key: str, unit: dict) -> dict:
    return {
        "brand": {"title": "✦ TeachTown Adventures", "tagline": "Learn • Play • Grow"},
        "meta": META,
        "vocabIcons": {},
        "units": {unit_key: unit},
    }
