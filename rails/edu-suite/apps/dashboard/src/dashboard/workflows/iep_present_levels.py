"""IEP Present-Levels: upload a SEIS Present-Levels PDF -> OCR-extract the 8
sections -> (review gate) the teacher adds new input per section in a two-column
form -> the local model (qwen3.6, English-only) ELABORATES each section into a
fuller, defensible present-levels narrative -> a printable HTML artifact to paste
into SEIS.

Two passes, mirroring the TeachTown Builder review->finalize pattern:
- First job (upload): the ``extract`` step OCRs the PDF and writes
  output/present_levels.json, then STOPS (review gate). The frontend reads that
  and shows the two-column form.
- Finalize creates a SECOND job whose input/ holds ``filled.json`` (the extracted
  "current" + the teacher's new input per section). ``_provided_filled`` detects
  it and the ``generate`` step runs instead.

English-only by design — IEPs are written in English (bilingual belongs to other
edu-suite tabs). Model work goes through the platform broker (broker_media), which
owns GPU residency; the default is qwen3.6 (override IEP_LLM_MODEL), leaving edu's
global EDU_LLM_MODEL on mistral for the bilingual content tabs.
"""
from __future__ import annotations

import html
import json
import os
from pathlib import Path

from edu_media_core import broker_media, present_levels
from edu_media_core.jobs import JobContext, Step

from . import Workflow, register

IEP_LLM_MODEL = os.getenv("IEP_LLM_MODEL", "qwen3.6*:27b")

# (key, human label) for the 8 narrative sections, in form order.
SECTIONS: list[tuple[str, str]] = [
    ("strengths_preferences_interests", "Strengths/Preferences/Interests"),
    ("parent_input_concerns", "Parent Input and Concerns"),
    ("preacademic_academic_functional", "Preacademic/Academic/Functional Skills"),
    ("communication_development", "Communication Development"),
    ("gross_fine_motor", "Gross/Fine Motor Development"),
    ("social_emotional_behavioral", "Social Emotional/Behavioral"),
    ("vocational", "Vocational"),
    ("adaptive_daily_living", "Adaptive/Daily Living Skills"),
]
_LABELS = dict(SECTIONS)
_OUT_KEYS = [k for k, _ in SECTIONS] + ["areas_of_need"]

_SYSTEM = """You help a California special-education teacher write the PRESENT LEVELS OF \
ACADEMIC ACHIEVEMENT AND FUNCTIONAL PERFORMANCE (PLAAFP) narrative for an IEP, on the El Dorado \
County Charter SELPA / SEIS form. You ELABORATE the teacher's terse input into fuller, \
professional, defensible narrative. You DRAFT for the IEP team to review, individualize, and \
approve — never a final or legally binding document, and you make no placement/eligibility decisions.

You are given, per section, the CURRENT text (from the prior present levels) and the teacher's \
new INPUT. Produce the elaborated English narrative for each section.

RULES:
- ELABORATE, NEVER FABRICATE. Expand and professionalize the teacher's meaning; add no facts, \
scores, test names, or dates that were not provided.
- PLACEHOLDERS for MISSING data only: where a number/score/date belongs but was not provided, \
insert a bracketed placeholder exactly where it goes — e.g. [X WCPM], [iReady reading grade — date], \
[SS __, %ile __], [__% accuracy], [Vineland ABC SS], [attendance __/__], [date]. NEVER invent the value.
- NEVER bracket data that WAS provided; state it verbatim as fact. Keep EVERY value given — drop none.
- Each section: current functioning + a genuine strength + the need; for academic/functional \
sections, state how the disability affects access to the general curriculum. Note whether it is an \
area of need; a "not an area of need" section still needs an affirmative adequacy statement.
- Behavior that impedes learning -> note that an FBA/BIP should be considered. Transition-age \
students -> connect the Vocational section to postsecondary goals / the transition plan.
- ENGLISH ONLY (no Spanish). For an English Learner, still write in English but note the ELPAC \
level and distinguish language acquisition from disability.
- Keep it tight and readable; no empty filler.

Return ONLY a JSON object with these string keys (each the elaborated narrative for that section):
"strengths_preferences_interests", "parent_input_concerns", "preacademic_academic_functional", \
"communication_development", "gross_fine_motor", "social_emotional_behavioral", "vocational", \
"adaptive_daily_living", and "areas_of_need" (a concise list of the areas flagged as needs)."""


def _as_text(v) -> str:
    """Coerce a section value to display text. The model sometimes returns a JSON array
    (typically for ``areas_of_need``); join it into clean lines instead of leaking a
    Python list repr like "['a', 'b']" into the narrative/copy."""
    if isinstance(v, list):
        return "\n".join(str(x).strip() for x in v if str(x).strip())
    if isinstance(v, dict):
        return "\n".join(f"{k}: {val}" for k, val in v.items())
    return str(v if v is not None else "")


def _provided_filled(ctx: JobContext) -> Path | None:
    for f in ctx.state["input_files"]:
        if f.name.lower() == "filled.json":
            return f
    return None


def _extract(ctx: JobContext) -> None:
    pdfs = [f for f in ctx.state["input_files"] if f.suffix.lower() == ".pdf"]
    if not pdfs:
        raise ValueError("Upload a SEIS Present-Levels PDF to extract.")
    ctx.progress(f"OCR-extracting {pdfs[0].name}…")
    data = present_levels.extract(str(pdfs[0]))
    (ctx.state["output_dir"] / "present_levels.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    filled = sum(1 for k in present_levels.SECTION_KEYS if data["sections"].get(k))
    warn = f" ({len(data['warnings'])} heading warning(s))" if data["warnings"] else ""
    ctx.stages[-1].message = f"{filled}/8 sections extracted{warn} — ready to review"


def _build_user_message(filled: dict) -> str:
    hdr = filled.get("header") or {}
    meta = (filled.get("meta") or "").strip()
    lines = []
    who = ", ".join(v for v in (hdr.get("student_name"), meta) if v)
    if who:
        lines.append(f"Student: {who}")
    lines.append("")
    for key, label in SECTIONS:
        sec = (filled.get("sections") or {}).get(key) or {}
        cur = (sec.get("current") or "").strip() or "(none)"
        inp = (sec.get("input") or "").strip() or "(none)"
        lines.append(f"### {label}")
        lines.append(f"CURRENT: {cur}")
        lines.append(f"TEACHER INPUT: {inp}")
        lines.append("")
    return "\n".join(lines)


def _render_html(name: str, hdr: dict, out: dict) -> str:
    def esc(s: str) -> str:
        return html.escape(str(s or "")).replace("\n", "<br>")

    rows = []
    for key, label in SECTIONS:
        rows.append(f"<section><h2>{html.escape(label)}</h2><p>{esc(out.get(key))}</p></section>")
    rows.append(f"<section class='aon'><h2>Areas of Need</h2><p>{esc(out.get('areas_of_need'))}</p></section>")
    meta = " &nbsp;·&nbsp; ".join(
        f"<b>{html.escape(k)}:</b> {html.escape(str(v))}"
        for k, v in (("Student", hdr.get("student_name")), ("Birthdate", hdr.get("birthdate")),
                     ("IEP Date", hdr.get("iep_date"))) if v)
    return (
        "<!doctype html><meta charset='utf-8'>"
        f"<title>Present Levels — {html.escape(name)}</title>"
        "<style>body{font:15px/1.5 system-ui,Segoe UI,Arial,sans-serif;max-width:820px;"
        "margin:2rem auto;padding:0 1rem;color:#1a1a1a}h1{font-size:1.4rem;margin:.2rem 0}"
        ".meta{color:#555;font-size:.9rem;margin-bottom:1.2rem}h2{font-size:1.05rem;margin:1.2rem 0 .3rem;"
        "border-bottom:1px solid #ddd;padding-bottom:.2rem}.aon h2{color:#7a1f1f}"
        ".note{color:#777;font-size:.8rem;margin-top:2rem;border-top:1px solid #eee;padding-top:.6rem}"
        "@media print{body{margin:0}}</style>"
        f"<h1>Present Levels of Academic Achievement and Functional Performance</h1>"
        f"<div class='meta'>{meta}</div>" + "".join(rows) +
        "<p class='note'>AI-elaborated DRAFT for the IEP team to review, individualize, and approve. "
        "Bracketed placeholders mark data to fill in. Not a final or legally binding document.</p>"
    )


def _generate(ctx: JobContext) -> None:
    src = _provided_filled(ctx)
    filled = json.loads(src.read_text(encoding="utf-8"))
    ctx.progress("Elaborating the 8 sections…")
    out = broker_media.chat_json(
        _SYSTEM, _build_user_message(filled),
        model=IEP_LLM_MODEL, options={"temperature": 0.3, "num_ctx": 16384})
    out = {k: _as_text(out.get(k, "")) for k in _OUT_KEYS}
    name = (filled.get("name") or ctx.state["name"] or "Present Levels").strip()
    hdr = filled.get("header") or {}
    (ctx.state["output_dir"] / "present_levels_final.json").write_text(
        json.dumps({"header": hdr, "name": name, "sections": out}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    (ctx.state["output_dir"] / "index.html").write_text(
        _render_html(name, hdr, out), encoding="utf-8")
    ctx.stages[-1].message = "present-levels narrative generated"


def _build_steps(ctx: JobContext) -> list[Step]:
    if _provided_filled(ctx) is not None:
        return [Step("generate", "Elaborate present-levels narrative", _generate)]
    # First pass: extract only, then stop for the teacher to review/fill the form.
    return [Step("extract", "Extract present-levels sections", _extract)]


register(Workflow(
    key="iep_present_levels",
    label="IEP Present Levels",
    description="Upload a SEIS Present-Levels PDF → extract the 8 sections → add your input → "
                "the local model elaborates a fuller English present-levels narrative to paste into SEIS.",
    build=_build_steps,
))
