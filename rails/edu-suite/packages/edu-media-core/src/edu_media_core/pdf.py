"""PDF → slides: pdfplumber text extraction with a PyMuPDF + tesseract OCR
fallback for image-only pages, plus line-unwrapping. One page == one slide."""
import base64
import io
import re
from pathlib import Path

import pdfplumber
import fitz  # PyMuPDF
import pytesseract
from PIL import Image

# All bullet-like prefixes recognised as list items
_BULLET_RE = re.compile(r"^(?:[●•\-\*]|\d+[.\)]\s|\([a-zA-Z]\)\s*)\s*")

# Render at this DPI for OCR accuracy
_OCR_DPI = 200


def _ocr_page(fitz_page) -> str:
    """Render a pymupdf page to an image and run tesseract OCR on it."""
    mat = fitz.Matrix(_OCR_DPI / 72, _OCR_DPI / 72)
    pix = fitz_page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img, lang="eng")


def _dominant_size(chars) -> float | None:
    """The font size covering the most characters on a page — i.e. the body text size."""
    counts: dict[float, int] = {}
    for c in chars:
        s = c.get("size")
        if s:
            k = round(s, 1)
            counts[k] = counts.get(k, 0) + 1
    return max(counts, key=counts.get) if counts else None


def _drop_small_glyphs(page, ratio: float = 0.7):
    """Filter out glyphs much smaller than the page's dominant (body) text size. These are
    picture-symbol captions/labels (e.g. a 9pt "RIP" over a tombstone icon) and fine print —
    not body a reader should hear. LARGER text (titles/headings) is kept; only characters with
    a known, clearly-smaller size are dropped. Returns the page unchanged when it has no
    embedded text (image-only/OCR pages carry no font metadata, so there's nothing to judge)."""
    body = _dominant_size(page.chars)
    if not body:
        return page
    thr = body * ratio
    return page.filter(lambda o: o.get("object_type") != "char"
                       or o.get("size") is None or o.get("size") >= thr)


def read_slides(pdf_path: str, *, drop_small_text: bool = False) -> list[dict]:
    """
    Extract all slides from a PDF. Each page = one slide.

    For pages where pdfplumber returns no text (image-only slides), falls back
    to tesseract OCR via pymupdf page rendering.

    ``drop_small_text`` (opt-in) filters out sub-body-size glyphs before extraction — picture
    symbol captions/labels and fine print — for readers that should voice only the body text.
    It no-ops on OCR pages (no font metadata). Default False keeps the original behaviour for
    every other caller.

    Returns a list of dicts:
      {
        "slide_number": int,       # 1-based page number
        "title": str,              # first non-empty line
        "bullets": list[str],      # list-item lines, prefix stripped
        "paragraphs": list[str],   # non-list lines after the title, wrap-joined
        "raw_text": str,           # original extracted text
      }
    """
    slides = []
    fitz_doc = fitz.open(pdf_path)
    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        ocr_count = 0
        for i, page in enumerate(pdf.pages, 1):
            # dedupe_chars collapses double-struck glyphs — PDFs that fake bold/outline by
            # drawing each character twice otherwise extract as "GGrreeaatt EExxppeeccttaaiioonnss".
            page = page.dedupe_chars(tolerance=1)
            if drop_small_text:
                page = _drop_small_glyphs(page)
            raw = page.extract_text() or ""
            if not raw.strip():
                if ocr_count == 0:
                    print(f"  PDF has no embedded text — running OCR ({total} pages)...", flush=True)
                ocr_count += 1
                print(f"  [OCR] page {i}/{total}", flush=True)
                raw = _ocr_page(fitz_doc[i - 1])
            slides.append(_parse_page(i, raw))
    fitz_doc.close()
    return slides


def render_pdf_pages(pdf_path, out_dir, *, width: int = 1200, quality: int = 72) -> list[Path]:
    """Render each page of a PDF to a compressed JPG in out_dir (page-1.jpg, ...).

    Used to make worksheets viewable fully offline at a fraction of the PDF size
    (~150KB/page at width 1200, q72). Returns the list of written paths.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    doc = fitz.open(str(pdf_path))
    try:
        for i, page in enumerate(doc, 1):
            zoom = width / page.rect.width
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            out = out_dir / f"page-{i}.jpg"
            img.save(str(out), "JPEG", quality=quality, optimize=True)
            paths.append(out)
    finally:
        doc.close()
    return paths


def get_toc(pdf_path) -> list[tuple[int, str, int]]:
    """The PDF's embedded outline as ``(level, title, page_1based)`` entries, or ``[]``
    when the document has no outline. A thin wrapper over PyMuPDF so callers (e.g. the
    book reader's chapter detection) don't import fitz just to read the table of contents."""
    doc = fitz.open(str(pdf_path))
    try:
        return [(int(lvl), str(title), int(pg)) for lvl, title, pg in doc.get_toc()]
    except Exception:
        return []
    finally:
        doc.close()


def render_page_b64(pdf_path, page_index: int = 0, *, width: int = 1024) -> str:
    """Render ONE page of a PDF to a base64-encoded PNG string — for handing an
    image-only worksheet to a vision model. Mirrors render_pdf_pages' zoom logic."""
    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_index]
        zoom = (width / page.rect.width) if page.rect.width else 1.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        return base64.b64encode(pix.tobytes("png")).decode("ascii")
    finally:
        doc.close()


def _slide_slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (title or "").lower()).strip("_")[:30]


def render_slides(pdf_path, slides: list[dict], out_dir, *,
                  scale: float = 2.0, thumb_scale: float = 0.22) -> dict[int, dict]:
    """Render the given slide pages to a full-size JPEG + a thumbnail under out_dir/images/.

    Each ``slide`` is a dict with ``slide_number`` (1-based) and ``title``. Returns
    ``{slide_number: {"image": rel, "thumb": rel}}`` (paths relative to out_dir). Idempotent:
    skips pages whose images already exist. (Moved here from slide-audio's slide_renderer.)
    """
    out = Path(out_dir)
    img_dir = out / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    result: dict[int, dict] = {}
    doc = fitz.open(str(pdf_path))
    try:
        for slide in slides:
            page_idx = slide["slide_number"] - 1  # fitz is 0-indexed
            if page_idx < 0 or page_idx >= len(doc):
                continue
            stem = f"slide_{slide['slide_number']:02d}_{_slide_slug(slide['title'])}"
            full_path = img_dir / f"{stem}.jpg"
            thumb_path = img_dir / f"{stem}_thumb.jpg"
            need_full, need_thumb = not full_path.exists(), not thumb_path.exists()
            if need_full or need_thumb:
                page = doc[page_idx]
                if need_full:
                    page.get_pixmap(matrix=fitz.Matrix(scale, scale)).save(str(full_path))
                if need_thumb:
                    page.get_pixmap(matrix=fitz.Matrix(thumb_scale, thumb_scale)).save(str(thumb_path))
            result[slide["slide_number"]] = {
                "image": f"images/{stem}.jpg",
                "thumb": f"images/{stem}_thumb.jpg",
            }
    finally:
        doc.close()
    return result


def _is_bullet(line: str) -> bool:
    return bool(_BULLET_RE.match(line))


def _strip_bullet(line: str) -> str:
    return _BULLET_RE.sub("", line).strip()


def _join_wrapped(lines: list[str]) -> list[str]:
    """
    Join consecutive lines where the previous line does not end a sentence.
    pdfplumber wraps long slide text mid-sentence, producing fragments like:
      "Needs are things you must have to be safe, healthy,"
      "and okay."
    These become a single line after joining.
    """
    if not lines:
        return lines
    out = []
    buf = lines[0]
    for line in lines[1:]:
        # Continue buffering if the current buffer doesn't end a sentence
        if not buf.rstrip().endswith((".", "!", "?", ":")):
            buf = buf.rstrip() + " " + line.lstrip()
        else:
            out.append(buf)
            buf = line
    out.append(buf)
    return out


def _parse_page(slide_number: int, raw_text: str) -> dict:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

    if not lines:
        return {
            "slide_number": slide_number,
            "title": "",
            "bullets": [],
            "paragraphs": [],
            "raw_text": raw_text,
        }

    title = lines[0]
    raw_bullets = []
    raw_paragraphs = []

    for line in lines[1:]:
        if _is_bullet(line):
            raw_bullets.append(_strip_bullet(line))
        else:
            raw_paragraphs.append(line)

    # Only paragraphs get wrap-joining. pdfplumber soft-wraps long paragraph text
    # mid-sentence, which we rejoin. Bullets are already discrete list items — a wrapped
    # bullet's continuation has no marker so it lands in paragraphs, not here — so joining
    # bullets only fused genuinely-distinct bullets (e.g. "welcomes guests" + "takes
    # reservations"). Leave them as parsed.
    bullets = raw_bullets
    paragraphs = _join_wrapped(raw_paragraphs)

    return {
        "slide_number": slide_number,
        "title": title,
        "bullets": bullets,
        "paragraphs": paragraphs,
        "raw_text": raw_text,
    }
