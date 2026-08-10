"""Phase 2 recipe import: turn an uploaded file, a pasted image, or a URL into text
and/or images for the assistant to distill. Torch-free; vision runs on the broker's
multimodal model. PDFs use their text layer when present, else render to images.
"""
from __future__ import annotations

import base64
import io
import ipaddress
import re
import socket
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlparse

import httpx

_IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
_MAX_DIM = 1600
_MAX_IMAGES = 6


def is_image(filename: str) -> bool:
    return PurePosixPath(filename or "").suffix.lower() in _IMG_EXT


def image_to_b64(data: bytes) -> str | None:
    """Downscale, flatten to RGB, re-encode JPEG, return base64 — bounds the vision payload."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGB")
        if max(im.size) > _MAX_DIM:
            im.thumbnail((_MAX_DIM, _MAX_DIM))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def _pdf(data: bytes) -> tuple[str, list[str]]:
    """(text, images): text layer if present; otherwise render pages to images (scanned PDF)."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=data, filetype="pdf")
        text = "\n".join(page.get_text("text") for page in doc).strip()
        images: list[str] = []
        if len(text) < 40:  # image-only / scanned
            for page in doc:
                b = image_to_b64(page.get_pixmap(dpi=150).tobytes("png"))
                if b:
                    images.append(b)
                if len(images) >= _MAX_IMAGES:
                    break
            text = ""
        doc.close()
        return text, images
    except Exception:
        return "", []


def _docx(data: bytes) -> str:
    try:
        import docx
        d = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in d.paragraphs if p.text.strip())
    except Exception:
        return ""


def extract_file(filename: str, data: bytes) -> dict:
    """One file -> {'text': str, 'images': [b64]}."""
    ext = PurePosixPath(filename or "").suffix.lower()
    if ext in _IMG_EXT:
        b = image_to_b64(data)
        return {"text": "", "images": [b] if b else []}
    if ext == ".pdf":
        text, images = _pdf(data)
        return {"text": text, "images": images}
    if ext == ".docx":
        return {"text": _docx(data), "images": []}
    return {"text": data.decode("utf-8", "replace"), "images": []}  # txt / md / other text


def _public_host(host: str) -> bool:
    """True only if every IP the host resolves to is publicly routable. Blocks SSRF to loopback,
    private, link-local (incl. the 169.254 cloud-metadata endpoint), reserved and multicast ranges."""
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return False
    return bool(infos)


def _guarded_fetch(url: str, max_redirects: int = 4) -> str:
    """GET ``url`` following redirects manually, re-checking scheme + host publicness on every hop
    so a public URL can't 30x-redirect into an internal address. Returns the response body."""
    with httpx.Client(follow_redirects=False, timeout=20,
                      headers={"user-agent": _UA, "accept-language": "en-US,en;q=0.9"}) as c:
        for _ in range(max_redirects + 1):
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                raise ValueError("only http(s) URLs can be imported")
            if not _public_host(parsed.hostname or ""):
                raise ValueError("refusing to fetch a non-public address")
            resp = c.get(url)
            if resp.is_redirect and resp.headers.get("location"):
                url = urljoin(url, resp.headers["location"])
                continue
            resp.raise_for_status()
            return resp.text
    raise ValueError("too many redirects")


def extract_url(url: str) -> str:
    """Fetch a recipe page and strip it to clean article text (blog cruft removed)."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    html = _guarded_fetch(url)
    text = ""
    try:
        import trafilatura
        text = trafilatura.extract(html, include_comments=False, include_tables=True,
                                   favor_recall=True) or ""
    except Exception:
        text = ""
    if len(text) < 60:  # fallback: crude tag strip
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    return text.strip()[:16000]
