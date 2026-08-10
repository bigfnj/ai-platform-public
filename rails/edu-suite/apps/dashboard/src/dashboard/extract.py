"""Minimal text extraction for uploaded documents (PDF / TXT / MD / CSV / DOCX)."""
from __future__ import annotations

import re
from pathlib import Path


def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".txt", ".md", ".csv"):
        return path.read_text(encoding="utf-8", errors="replace")
    if ext == ".pdf":
        import pdfplumber
        parts = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                if t.strip():
                    parts.append(t)
        return "\n\n".join(parts)
    if ext == ".docx":
        try:
            import docx  # python-docx, optional
            return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
        except Exception:
            return ""
    return ""


_SENT = re.compile(r"(?<=[.!?])\s+")


def to_chunks(text: str, max_chars: int = 600) -> list[str]:
    """Split text into translation-sized chunks: by paragraph, then by sentence
    if a paragraph is too long. Empty/whitespace chunks are dropped."""
    chunks: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            chunks.append(para)
            continue
        buf = ""
        for sent in _SENT.split(para):
            if not sent.strip():
                continue
            if len(buf) + len(sent) + 1 > max_chars and buf:
                chunks.append(buf.strip())
                buf = sent
            else:
                buf = (buf + " " + sent) if buf else sent
        if buf.strip():
            chunks.append(buf.strip())
    return chunks
