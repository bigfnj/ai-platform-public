"""SEIS Present-Levels PDF extractor (edu-suite IEP workflow).

The SEIS-generated PDFs have a CORRUPTED text layer (broken ToUnicode CMap: the
ti/tt/ft/tf ligatures map to a shifting set of ASCII chars — '%','!','#','@','(',
')','.' … — several colliding with real punctuation like 80%, (e.g., dates). So
we ignore the text layer and OCR the rendered pages (fitz render @300dpi ->
tesseract), which reads the real glyphs and yields clean text, then split on the
SEIS section headings into the 8 present-levels fields.

Validated on 14 real present-levels samples (all 8 sections extract cleanly).

Runtime deps (imported lazily so importing this module stays cheap): PyMuPDF
(fitz) for rendering + tesseract on PATH for OCR. In the dashboard container add
`tesseract-ocr` to the image (see deploy notes); on the broker/host box the
DevToolbox provides both.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unicodedata

# Output keys for the 8 narrative present-levels sections, in form order.
SECTION_KEYS = [
    "strengths_preferences_interests",
    "parent_input_concerns",
    "preacademic_academic_functional",
    "communication_development",
    "gross_fine_motor",
    "social_emotional_behavioral",
    "vocational",
    "adaptive_daily_living",
]

# Section headings in document order -> output key ("_"-prefixed keys are captured
# but are not part of the 8 narrative fields). Regexes are line-anchored and
# tolerant of OCR punctuation/space variance, and consume the FULL heading so no
# heading fragment bleeds into the section body.
_HEADINGS: list[tuple[str, str]] = [
    ("strengths_preferences_interests", r"(?m)^\s*Strengths\W+Preferences\W+Interests"),
    ("parent_input_concerns",           r"(?m)^\s*Parent\s+input\s+and\s+concerns(?:\s+relevant\s+to\s+educational\s+progress)?"),
    ("_assessment",                     r"(?m)^\s*Smarter\s+Balanced\s+Assessment\s+Consortium(?:\s*\(SBAC\))?"),
    ("preacademic_academic_functional", r"(?m)^\s*Preacademic\W+Academic\W+Functional\s+Skills"),
    ("communication_development",       r"(?m)^\s*Communication\s+Development"),
    ("gross_fine_motor",                r"(?m)^\s*Gross\W+Fine\s+Motor\s+Development"),
    ("social_emotional_behavioral",     r"(?m)^\s*Social\s+Emotional\W+Behavioral"),
    ("vocational",                      r"(?m)^\s*Vocational\b"),
    ("adaptive_daily_living",           r"(?m)^\s*Adaptive\W+Daily\s+Living\s+Skills"),
    ("_health",                         r"(?m)^\s*Health\s*$"),
    ("_ihp",                            r"(?m)^\s*Does\s+this\s+student\s+have\s+an\s+Individual\s+Health\s+Plan"),
    ("_areas_of_need",                  r"(?m)^\s*For\s+student\s+to\s+receive\s+educational\s+benefit,?\s*goals\s+will\s+be\s+written\s+to\s+address\s+the\s+following\s+areas\s+of\s+need:?"),
]

_BOILERPLATE = re.compile(
    r"(?im)^\s*(EL\s+DORADO\s+COUNTY\s+CHARTER\s+SELPA"
    r"|PRESENT\s+LEVELS\s+OF\s+ACADEMIC\s+ACHIEVEMENT"
    r"|Page\s+_*\s*of\b.*"
    r"|Student\s+Name:.*|Birthdate:.*|IEP\s+Date:.*)\s*$"
)


def _tesseract() -> str:
    return (shutil.which("tesseract")
            or os.getenv("TESSERACT_CMD")
            or r"C:\Users\Admin\AppData\Local\DevToolbox\native\bin\tesseract.cmd")


def ocr_pdf(path: str, dpi: int = 300) -> str:
    """Render each page and OCR it; return NFKC-normalized concatenated text."""
    import fitz  # PyMuPDF, imported lazily

    tess = _tesseract()
    doc = fitz.open(path)
    out: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, page in enumerate(doc):
            png = os.path.join(tmp, f"pg{i}.png")
            page.get_pixmap(dpi=dpi).save(png)
            r = subprocess.run(f'"{tess}" "{png}" stdout --psm 4', shell=True,
                               capture_output=True, text=True, encoding="utf-8")
            out.append(r.stdout or "")
    return unicodedata.normalize("NFKC", "\n".join(out))


def _clean(text: str) -> str:
    text = _BOILERPLATE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _header(text: str) -> dict[str, str]:
    m = re.search(r"Student Name:\s*(.+?)\s+Birthdate:\s*(.+?)\s+IEP Date:\s*(\S+)", text)
    if m:
        return {"student_name": m.group(1).strip(),
                "birthdate": m.group(2).strip(),
                "iep_date": m.group(3).strip()}

    def grab(label: str) -> str:
        mm = re.search(rf"{label}:\s*(.+)", text)
        return mm.group(1).strip() if mm else ""

    return {"student_name": grab("Student Name"),
            "birthdate": grab("Birthdate"), "iep_date": grab("IEP Date")}


def extract(path: str, *, dpi: int = 300) -> dict:
    """Extract the present-levels form to a dict: header, the 8 narrative
    sections, plus the raw assessment block / health / areas-of-need."""
    text = ocr_pdf(path, dpi=dpi)
    header = _header(text)

    found: list[tuple[int, int, str]] = []
    for key, pat in _HEADINGS:
        m = re.search(pat, text)
        if m:
            found.append((m.start(), m.end(), key))
    found.sort()

    sliced: dict[str, str] = {}
    for idx, (_, end, key) in enumerate(found):
        nxt = found[idx + 1][0] if idx + 1 < len(found) else len(text)
        sliced[key] = _clean(text[end:nxt])

    warnings = [f"heading not found: {k}" for k, _ in _HEADINGS if k not in sliced]
    return {
        "source": os.path.basename(path),
        "header": header,
        "sections": {k: sliced.get(k, "") for k in SECTION_KEYS},
        "assessment_data": sliced.get("_assessment", ""),
        "health": sliced.get("_health", ""),
        "areas_of_need": sliced.get("_areas_of_need", ""),
        "warnings": warnings,
    }
