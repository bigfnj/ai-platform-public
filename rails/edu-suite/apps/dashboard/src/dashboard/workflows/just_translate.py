"""Just Translate workflow: uploaded books -> an offline bilingual (EN/es_MX) READER.

Each uploaded PDF becomes a "book". Every page is rendered to an image (text AND pictures
kept intact — the real page, not harvested text), its text extracted (with an OCR fallback
for scanned pages) and translated to Mexican Spanish, and per-page EN + ES audio synthesized
with sentence offsets for read-along. Chapters come from the PDF's embedded outline, falling
back to text-marker detection, then a single model pass over the page openings.

The bundle is a self-contained static reader (`index.html` with the data inlined + `<book>/`
folders of page images and audio), so it works offline from the downloaded zip — no server.

Audio is part of a translation, not an option (this matches the original translation-service,
which always produced bilingual audio).
"""
from __future__ import annotations

import collections
import json
import re
import wave
from pathlib import Path

from edu_media_core.jobs import JobContext, Step

from edu_media_core import broker_media as core, profiles  # translate + audio + chat via broker
from edu_media_core import pdf as core_pdf                  # page render + text + outline
from ..extract import extract_text, to_chunks
from . import Workflow, register

_PROMPT = """\
You translate English text into clear Mexican Spanish (es_MX) for a special-education
classroom. Preserve the meaning faithfully. Use simple, natural, concrete language and
the informal tú register. Do not add or omit information.

Return ONLY valid JSON: {"es": "<the Spanish translation of the text>"}
"""
_OPTS = {"temperature": 0.2, "num_ctx": 2048}

PROFILE = profiles.register(profiles.Profile(
    key="translate_special_ed",
    label="Document translation (special-ed es_MX)",
    system_prompt=_PROMPT,
    options=_OPTS,
    required_keys=("es",),
))


def _as_text(v) -> str:
    """Coerce the model's ``es`` field to a string. JSON-mode LLMs sometimes return
    it as an object/array (e.g. splitting multi-line input into keyed parts) instead
    of one string; flatten those in order so translate, audio, and render all get text."""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return "\n".join(_as_text(x) for x in v.values())
    if isinstance(v, (list, tuple)):
        return "\n".join(_as_text(x) for x in v)
    return str(v)


_SENT_SPLIT = re.compile(r'(?<=[.!?])\s+')


def _sentences(text: str) -> list[str]:
    """Split into sentences for the read-along highlight. Simple splitter (fine for
    classroom text): break after . ! ? and keep the punctuation with the sentence."""
    text = re.sub(r'\s+', ' ', (text or '').strip())
    return [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()] if text else []


def _concat_wavs(paths: list[Path], out_path: Path, gap_s: float = 0.25) -> list[tuple[float, float]]:
    """Concatenate WAV clips into one track (a short silence between each) and return
    each clip's [start, end] offset, so the player can highlight the current sentence."""
    params = None
    silence = b""
    datas: list[bytes] = []
    segs: list[tuple[float, float]] = []
    t = 0.0
    for p in paths:
        with wave.open(str(p), 'rb') as r:
            pr = r.getparams()
            n = r.getnframes()
            data = r.readframes(n)
        if params is None:
            params = pr
            silence = b"\x00" * (int(gap_s * pr.framerate) * pr.nchannels * pr.sampwidth)
        dur = n / pr.framerate
        segs.append((round(t, 3), round(t + dur, 3)))
        datas.append(data)
        t += dur + gap_s
    with wave.open(str(out_path), 'wb') as w:
        w.setparams(params)
        for i, data in enumerate(datas):
            w.writeframes(data)
            if i < len(datas) - 1:
                w.writeframes(silence)
    return segs


# --- 1. ingest: render every page + pull its text -----------------------------------------

def _boiler_key(line: str) -> str:
    """Order- and number-insensitive key for matching a running header/footer across pages.
    Lowercase alphabetic words only (drop page numbers, punctuation, ® etc.), sorted — so a
    footer that flips order and swaps its page number by page parity ("© … World War I 1"
    vs "2 The Causes of World War I © …") maps to a single key."""
    return " ".join(sorted(re.findall(r"[a-z]+", line.lower())))


def _strip_boilerplate(pages: list[dict]) -> None:
    """Remove running headers/footers — a line that repeats (allowing for a changing page
    number and flipped order) on most pages: a top banner, a copyright line, a running title.
    It's page furniture, not book content, so it must not be translated or read. Only the top
    and bottom lines of each page are candidates, so body text and a one-off title/heading
    (e.g. the page-1 book title, which sits below the header) are never touched. Mutates en_text."""
    n = len(pages)
    if n < 3:  # too few pages to tell a running header from real content
        return
    linelists = [[ln.strip() for ln in (p.get("en_text") or "").splitlines() if ln.strip()]
                 for p in pages]
    freq = collections.Counter()
    for lines in linelists:
        for ln in set(lines[:2] + lines[-2:]):  # only header/footer zones are candidates
            k = _boiler_key(ln)
            if k:
                freq[k] += 1
    thresh = max(3, round(0.6 * n))
    boiler = {k for k, c in freq.items() if c >= thresh}
    if not boiler:
        return
    for p, lines in zip(pages, linelists):
        lines = list(lines)
        while lines and _boiler_key(lines[0]) in boiler:   # peel repeating top lines
            lines.pop(0)
        while lines and _boiler_key(lines[-1]) in boiler:  # peel repeating bottom lines
            lines.pop()
        p["en_text"] = "\n".join(lines)


def _ingest(ctx: JobContext) -> None:
    out: Path = ctx.state["output_dir"]
    books: list[dict] = []
    for di, f in enumerate(ctx.state["input_files"]):
        bid = f"b{di}"
        book_dir = out / bid
        title = Path(f.name).stem
        ctx.progress(f"reading {f.name}")
        pages: list[dict] = []
        toc: list = []
        if f.suffix.lower() == ".pdf":
            # drop_small_text: skip sub-body-size glyphs (picture-symbol captions like a 9pt
            # "RIP" over a tombstone icon) so they aren't translated or read aloud.
            slides = core_pdf.read_slides(str(f), drop_small_text=True)  # per-page text (+ OCR fallback)
            img_map = core_pdf.render_slides(str(f), slides, book_dir)  # full + thumb per page
            toc = core_pdf.get_toc(str(f))
            for s in slides:
                n = s["slide_number"]
                im = img_map.get(n, {})
                pages.append({
                    "n": n,
                    "image": f"{bid}/{im['image']}" if im.get("image") else None,
                    "thumb": f"{bid}/{im['thumb']}" if im.get("thumb") else None,
                    "en_text": (s.get("raw_text") or "").strip(),
                })
        else:
            # Non-PDF (docx/txt/…): no page images to render — the whole doc is one page.
            pages.append({"n": 1, "image": None, "thumb": None, "en_text": extract_text(f).strip()})
        _strip_boilerplate(pages)  # drop running header/footer/copyright before anything reads it
        books.append({"id": bid, "title": title, "dir": book_dir, "toc": toc,
                      "pages": pages, "chapters": []})
        ctx.progress(f"{title}: {len(pages)} page(s)")
    ctx.state["books"] = books
    total_pages = sum(len(b["pages"]) for b in books)
    ctx.stages[-1].message = f"{len(books)} book(s), {total_pages} page(s)"
    if total_pages == 0:
        raise ValueError("no readable pages in the uploaded file(s)")


# --- 2. chapters: PDF outline -> text markers -> model pass -------------------------------

_CHAP_RE = re.compile(r'^\s*(chapter|chap\.|unit|lesson|part|section)\s+(\d{1,3}|[ivxlcdm]{1,6})\b', re.I)

_CH_AI_PROMPT = """\
You are given the opening text of each page of a document. Identify where CHAPTERS or major
SECTIONS begin. Use the document's own chapter/section titles when they appear in the text.
Return ONLY valid JSON: {"chapters":[{"title":"<short title>","page":<page number>}]}
List each chapter once, in reading order, with the page number it starts on. If the document
has no real chapters, return a single chapter starting at page 1 with a short topic title.
"""


def _dedup_sorted(chs: list[dict]) -> list[dict]:
    seen, out = set(), []
    for c in sorted(chs, key=lambda c: c["page"]):
        if c["page"] in seen:
            continue
        seen.add(c["page"])
        out.append(c)
    return out


def _ensure_start(chs: list[dict]) -> list[dict]:
    """Guarantee a chapter that owns page 0 so every page belongs to a chapter."""
    chs = _dedup_sorted(chs)
    if not chs or chs[0]["page"] > 0:
        chs = [{"title": "Beginning", "page": 0}] + chs
    return chs


def _chapters_from_toc(toc: list, n_pages: int) -> list[dict]:
    chs = []
    for lvl, title, pg in toc:
        if lvl == 1:
            page = max(0, min(n_pages - 1, pg - 1))  # fitz TOC pages are 1-based
            chs.append({"title": (title or "").strip()[:80] or f"Page {page + 1}", "page": page})
    return _dedup_sorted(chs)


def _chapters_from_markers(pages: list[dict]) -> list[dict]:
    hits: list[tuple[int, str]] = []
    for i, p in enumerate(pages):
        first = next((ln.strip() for ln in (p["en_text"] or "").splitlines() if ln.strip()), "")
        if first and len(first) <= 60 and _CHAP_RE.match(first):
            hits.append((i, first[:80]))
    if not hits:
        return []
    # Reject a REPEATING HEADER masquerading as a chapter marker: a "Unit 76 …" banner
    # printed at the top of every page matches on every page and yields one bogus chapter
    # per page. Real chapters are sparse and distinctly titled. If the marker carries a
    # single title, or fires on most pages, it's a running header — bail to the next tier.
    titles = {t.casefold() for _, t in hits}
    if len(titles) <= 1 or len(hits) >= max(3, 0.8 * len(pages)):
        return []
    # Collapse consecutive same-title runs (a per-chapter running header) to first appearance.
    chs, seen = [], set()
    for i, t in hits:
        key = t.casefold()
        if key in seen:
            continue
        seen.add(key)
        chs.append({"title": t, "page": i})
    return _dedup_sorted(chs)


def _chapters_from_ai(book: dict, ctx: JobContext) -> list[dict]:
    pages = book["pages"]
    if not any(p["en_text"] for p in pages):
        return []
    ctx.progress(f"  {book['title']}: detecting chapters with the model")
    digest = "\n".join(
        f"[p{p['n']}] " + " ".join((p["en_text"] or "").split())[:160] for p in pages)
    d = core.chat_json(_CH_AI_PROMPT, f"Document: {book['title']}\n\n{digest}",
                       options={"temperature": 0.1, "num_ctx": 8192})
    chs = []
    for c in (d.get("chapters") or []):
        if not isinstance(c, dict):
            continue
        try:
            pg = int(c.get("page"))
        except (TypeError, ValueError):
            continue
        page = max(0, min(len(pages) - 1, pg - 1))
        chs.append({"title": str(c.get("title") or f"Page {page + 1}").strip()[:80], "page": page})
    return _dedup_sorted(chs)


def _detect_chapters(book: dict, ctx: JobContext) -> list[dict]:
    n_pages = len(book["pages"])
    chs = _chapters_from_toc(book["toc"], n_pages)
    if chs:
        ctx.progress(f"  {book['title']}: {len(chs)} chapter(s) from the PDF outline")
        return _ensure_start(chs)
    chs = _chapters_from_markers(book["pages"])
    if chs:
        ctx.progress(f"  {book['title']}: {len(chs)} chapter(s) from headings")
        return _ensure_start(chs)
    try:
        chs = _chapters_from_ai(book, ctx)
    except Exception as e:  # a chapter pass must never sink the job
        ctx.progress(f"  {book['title']}: model chapter pass failed ({e})")
        chs = []
    if chs:
        return _ensure_start(chs)
    return [{"title": "Full book", "page": 0}]


def _chapters(ctx: JobContext) -> None:
    for b in ctx.state["books"]:
        b["chapters"] = _detect_chapters(b, ctx)
    ctx.stages[-1].message = ", ".join(f"{b['title']}: {len(b['chapters'])} ch" for b in ctx.state["books"])


# --- 3. translate: per page (so text aligns to the page you're viewing) -------------------

_PAGENUM_RE = re.compile(r"^\d{1,4}$")


def _clean_page_text(raw: str) -> str:
    """Prepare a page's extracted text for translation + read-along:
    - drop standalone page-number lines (a footer "1" would otherwise be read aloud);
    - on a chapter-opening page, peel the heading block (leading lines with no sentence
      punctuation) into its own sentence, so "Chapter 1 / A Criminal in the Graveyard"
      isn't glued onto the first body sentence "My name is Pip.".
    Runs AFTER chapter detection, so the rail titles (taken from the raw first line) stay
    short. Body soft-wraps are left for the sentence splitter to rejoin."""
    lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
    lines = [ln for ln in lines if not _PAGENUM_RE.match(ln)]
    if not lines:
        return ""
    if _CHAP_RE.match(lines[0]):  # a real chapter-opening page
        head = []
        while len(lines) > 1 and not re.search(r"[.!?]", lines[0]):
            head.append(lines.pop(0))
        if head:
            lines.insert(0, " ".join(head).rstrip(" .") + ".")
    return "\n".join(lines)


def _translate(ctx: JobContext) -> None:
    # Clean each page's text for reading (footer page numbers dropped; a chapter heading
    # split off as its own sentence). Done here — after chapter detection — so the rail
    # titles stay short; both translate and audio then consume the cleaned en_text.
    for b in ctx.state["books"]:
        for p in b["pages"]:
            p["en_text"] = _clean_page_text(p["en_text"])
    guidance = (ctx.state["params"].get("guidance") or "").strip()
    system = PROFILE.system_prompt + (
        f"\n\nADDITIONAL TEACHER GUIDANCE (apply to the translation): {guidance}" if guidance else "")
    if guidance:
        ctx.progress(f"applying your instructions: {guidance}")
    total = sum(1 for b in ctx.state["books"] for p in b["pages"] if p["en_text"])
    done = 0
    for b in ctx.state["books"]:
        for p in b["pages"]:
            if not p["en_text"]:
                p["es_text"] = ""
                continue
            parts = []
            for ch in to_chunks(p["en_text"]):
                r = core.translate_cached(
                    system_prompt=system, user_message=ch,
                    options=PROFILE.options, required_keys=PROFILE.required_keys)
                parts.append(_as_text(r["es"]))
            p["es_text"] = "\n".join(parts)
            done += 1
            ctx.progress(f"{b['title']}: translated page {p['n']} ({done}/{total})")
    ctx.stages[-1].message = f"{total} page(s) translated"


# --- 4. audio: whole book upfront, one EN + one ES track per page -------------------------

def _audio(ctx: JobContext) -> None:
    """Synthesize per-page EN + ES audio for every book. All sentence clips across all
    books/pages/languages are queued into ONE synthesize_wavs call (it sub-batches), then
    concatenated per page/language into a single track whose sentence offsets drive the
    read-along highlight."""
    work = ctx.state["work_dir"] / "clips"
    work.mkdir(parents=True, exist_ok=True)

    items: list[dict] = []
    out_paths: list[Path] = []
    for bi, b in enumerate(ctx.state["books"]):
        (b["dir"] / "audio").mkdir(parents=True, exist_ok=True)
        for p in b["pages"]:
            p["_clips"] = {}
            for lang, text in (("en", p.get("en_text")), ("es", p.get("es_text"))):
                sents = _sentences(text or "")
                clips = []
                for si, s in enumerate(sents):
                    cp = work / f"b{bi}_p{p['n']}_{lang}_{si}.wav"
                    items.append({"lang": lang, "text": s})
                    out_paths.append(cp)
                    clips.append(cp)
                p["_clips"][lang] = {"sents": sents, "paths": clips}

    if items:
        ctx.progress(f"synthesizing {len(items)} sentence clip(s) across {len(ctx.state['books'])} book(s)")
        core.synthesize_wavs(
            items, out_paths,
            on_progress=lambda d, t: ctx.progress(f"synthesized {d}/{t} clip(s)"))

    tracks = 0
    for b in ctx.state["books"]:
        adir = b["dir"] / "audio"
        for p in b["pages"]:
            for lang in ("en", "es"):
                info = p["_clips"].get(lang, {"sents": [], "paths": []})
                sents, clips = info["sents"], info["paths"]
                if not clips:
                    p[f"{lang}_audio"], p[f"{lang}_segs"] = None, []
                    continue
                track = adir / f"p{p['n']}.{lang}.wav"
                segs = _concat_wavs(clips, track)
                p[f"{lang}_audio"] = f"{b['id']}/audio/{track.name}"
                p[f"{lang}_segs"] = [{"start": st, "end": en, "text": sents[i]}
                                     for i, (st, en) in enumerate(segs)]
                tracks += 1
            p.pop("_clips", None)
    ctx.stages[-1].message = f"{len(items)} clip(s) → {tracks} page track(s)"


# --- 5. render: data.json + a self-contained reader (index.html) --------------------------

def _segs_from_text(text: str) -> list[dict]:
    """Sentences with no timing — so a page that has text but no audio still shows a
    read panel (just without the moving highlight)."""
    return [{"start": None, "end": None, "text": s} for s in _sentences(text or "")]


def _render(ctx: JobContext) -> None:
    out: Path = ctx.state["output_dir"]
    data = {"title": ctx.state["name"], "books": []}
    for b in ctx.state["books"]:
        es_txt = "\n\n".join((p.get("es_text") or "") for p in b["pages"]).strip()
        if es_txt:  # per-book plain-text Spanish, as before
            (out / f"{b['title']}.es.txt").write_text(es_txt, encoding="utf-8")
        pages = []
        for p in b["pages"]:
            pages.append({
                "n": p["n"], "image": p.get("image"), "thumb": p.get("thumb"),
                "en": {"audio": p.get("en_audio"), "segs": p.get("en_segs") or _segs_from_text(p.get("en_text"))},
                "es": {"audio": p.get("es_audio"), "segs": p.get("es_segs") or _segs_from_text(p.get("es_text"))},
            })
        data["books"].append({"id": b["id"], "title": b["title"],
                              "chapters": b["chapters"], "pages": pages})
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (out / "index.html").write_text(
        _READER_HTML.replace("/*__BOOK_DATA__*/null", json.dumps(data, ensure_ascii=False)),
        encoding="utf-8")
    ctx.stages[-1].message = f"{len(data['books'])} book(s)"


def _build(ctx: JobContext) -> list[Step]:
    return [
        Step("ingest", "Render pages & read text", _ingest),
        Step("chapters", "Detect chapters", _chapters),
        # No required_model: the broker owns model residency; translate/audio/chat call it.
        Step("translate", "Translate to Spanish", _translate),
        Step("audio", "Generate EN/ES audio", _audio),
        Step("render", "Build reader", _render),
    ]


register(Workflow(
    key="just_translate",
    label="Just Translate",
    description="Turn uploaded books into an offline bilingual reader: the real page on view, "
                "chapter & page navigation, English/Spanish read-aloud with the spoken sentence "
                "highlighted.",
    build=_build,
))


# --- the reader (a self-contained static app; book data is inlined at render time) --------

_READER_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bilingual Reader</title>
<style>
:root{--bg:#f4efe6;--ink:#2b2b2b;--card:#fff;--line:#e2d9c8;--accent:#8a6d3b;
      --en:#ffe08a;--es:#bfe3ff;--rail:#efe7d7}
*{box-sizing:border-box}
body{margin:0;font-family:"Iowan Old Style","Palatino Linotype",Georgia,serif;
     background:var(--bg);color:var(--ink)}
header{display:flex;flex-wrap:wrap;align-items:center;gap:16px;padding:12px 20px;
       background:#fbf8f1;border-bottom:1px solid var(--line)}
header h1{font-size:20px;margin:0}
.books{display:flex;gap:8px;flex-wrap:wrap}
.book-tab{font:inherit;font-size:14px;padding:6px 14px;border:1px solid var(--line);
          background:#fff;border-radius:20px;cursor:pointer}
.book-tab.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.layout{display:grid;grid-template-columns:250px minmax(320px,1.2fr) minmax(300px,1fr);
        gap:0;height:calc(100vh - 58px)}
.rail{background:var(--rail);border-right:1px solid var(--line);overflow:auto;padding:14px}
.rail h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:#8a7a5c;margin:6px 2px 8px}
.chapters{display:flex;flex-direction:column;gap:4px;margin-bottom:18px}
.chapter{font:inherit;font-size:16px;text-align:left;padding:9px 11px;border:0;border-radius:9px;
         background:transparent;cursor:pointer;color:#3a3226}
.chapter:hover{background:#e6dcc7}
.chapter.active{background:var(--accent);color:#fff;font-weight:600}
.thumbs{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.thumb{border:2px solid transparent;border-radius:8px;background:#fff;padding:4px;cursor:pointer;
       display:flex;flex-direction:column;align-items:center;gap:2px}
.thumb img{width:100%;height:auto;border-radius:4px;display:block}
.thumb span{font-size:11px;color:#6b6151}
.thumb.active{border-color:var(--accent)}
.center{display:flex;flex-direction:column;align-items:center;padding:14px 18px;overflow:auto}
.pager{display:flex;align-items:center;gap:14px;margin-bottom:10px}
.pager button{font:inherit;font-size:20px;width:40px;height:36px;border:1px solid var(--line);
              background:#fff;border-radius:8px;cursor:pointer;line-height:1}
.pager #page-info{font-size:14px;color:#6b6151;min-width:120px;text-align:center}
.page-wrap{background:#fff;border:1px solid var(--line);border-radius:10px;padding:10px;
           box-shadow:0 6px 20px rgba(90,70,30,.10);max-width:100%}
.page-wrap img{max-width:100%;max-height:64vh;display:block;border-radius:4px}
.page-wrap.noimg{padding:40px;color:#9a8f79;font-style:italic}
.readback{display:flex;gap:12px;margin-top:14px}
.rb{font:inherit;font-size:16px;display:flex;align-items:center;gap:8px;padding:10px 20px;
    border:1px solid var(--accent);background:#fff;color:var(--accent);border-radius:24px;cursor:pointer}
.rb .ico{font-size:15px}
.rb.playing{background:var(--accent);color:#fff}
.rb:disabled{opacity:.4;cursor:not-allowed}
.panel{border-left:1px solid var(--line);overflow:auto;padding:16px 18px;background:#fbf8f1;
       display:flex;flex-direction:column;gap:16px}
.tpanel h3{margin:0 0 6px;font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:#8a7a5c}
.ptext{font-size:19px;line-height:1.75;background:#fff;border:1px solid var(--line);
       border-radius:12px;padding:14px 16px}
.ptext .empty{color:#9a8f79}
.seg{border-radius:5px;padding:1px 0;transition:background .12s}
.seg.active-en{background:var(--en)}
.seg.active-es{background:var(--es)}
@media(max-width:900px){.layout{grid-template-columns:1fr;height:auto}
  .rail,.panel{border:0;border-top:1px solid var(--line)}}
</style></head>
<body>
<header><h1 id="title"></h1><div id="books" class="books"></div></header>
<div class="layout">
  <aside class="rail">
    <h2>Chapters</h2><div id="chapters" class="chapters"></div>
    <h2>Pages</h2><div id="thumbs" class="thumbs"></div>
  </aside>
  <main class="center">
    <div class="pager"><button id="prev" title="Previous page">&lsaquo;</button>
      <span id="page-info"></span><button id="next" title="Next page">&rsaquo;</button></div>
    <div id="page-wrap" class="page-wrap"><img id="page-img" alt="book page"></div>
    <div class="readback">
      <button id="play-en" class="rb"><span class="ico">&#9654;</span> <span class="lbl">English</span></button>
      <button id="play-es" class="rb"><span class="ico">&#9654;</span> <span class="lbl">Espa&ntilde;ol</span></button>
    </div>
  </main>
  <section class="panel">
    <div class="tpanel"><h3>English</h3><div id="en-text" class="ptext"></div></div>
    <div class="tpanel"><h3>Espa&ntilde;ol</h3><div id="es-text" class="ptext"></div></div>
  </section>
</div>
<script>
(function(){
  var D = /*__BOOK_DATA__*/null || {title:"Reader",books:[]};
  var state = {book:0,page:0};
  var audio = {en:new Audio(), es:new Audio()};
  var $ = function(id){return document.getElementById(id);};
  var elBooks=$("books"),elCh=$("chapters"),elTh=$("thumbs"),elImg=$("page-img"),
      elWrap=$("page-wrap"),elInfo=$("page-info"),elEn=$("en-text"),elEs=$("es-text"),
      btn={en:$("play-en"),es:$("play-es")};

  function book(){return D.books[state.book];}
  function page(){return book().pages[state.page];}
  function esc(t){return (t||"").replace(/[&<>"]/g,function(c){
    return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c];});}

  function spans(segs,lang){
    if(!segs.length) return '<em class="empty">'+(lang==="en"?"No text on this page.":"Sin texto en esta página.")+'</em>';
    return segs.map(function(s,i){
      var timed=(s.start!=null)?(' data-start="'+s.start+'" data-end="'+s.end+'"'):'';
      return '<span class="seg" data-lang="'+lang+'" data-i="'+i+'"'+timed+'>'+esc(s.text)+' </span>';
    }).join("");
  }

  function renderBooks(){
    elBooks.innerHTML="";
    D.books.forEach(function(b,i){
      var t=document.createElement("button");
      t.className="book-tab"+(i===state.book?" active":"");
      t.textContent=b.title; t.onclick=function(){setBook(i);};
      elBooks.appendChild(t);
    });
    elBooks.style.display=D.books.length>1?"flex":"none";
  }
  function renderChapters(){
    elCh.innerHTML="";
    (book().chapters||[]).forEach(function(c){
      var it=document.createElement("button");
      it.className="chapter"; it.textContent=c.title;
      it.onclick=function(){setPage(c.page);};
      elCh.appendChild(it);
    });
  }
  function renderThumbs(){
    elTh.innerHTML="";
    book().pages.forEach(function(p,i){
      var t=document.createElement("button"); t.className="thumb";
      if(p.thumb){var im=document.createElement("img");im.src=p.thumb;im.loading="lazy";t.appendChild(im);}
      var s=document.createElement("span");s.textContent=p.n;t.appendChild(s);
      t.onclick=function(){setPage(i);};
      elTh.appendChild(t);
    });
  }
  function highlightNav(){
    var chs=book().chapters||[],active=0;
    chs.forEach(function(c,ci){if(c.page<=state.page)active=ci;});
    Array.prototype.forEach.call(elCh.children,function(el,ci){el.classList.toggle("active",ci===active);});
    Array.prototype.forEach.call(elTh.children,function(el,ci){
      var on=ci===state.page; el.classList.toggle("active",on);
      if(on) el.scrollIntoView({block:"nearest"});
    });
  }

  function resetBtns(){["en","es"].forEach(function(l){
    btn[l].classList.remove("playing"); btn[l].querySelector(".ico").innerHTML="&#9654;";});}
  function clearSegs(){Array.prototype.forEach.call(
    document.querySelectorAll(".seg.active-en,.seg.active-es"),
    function(s){s.classList.remove("active-en");s.classList.remove("active-es");});}
  function stopAll(){["en","es"].forEach(function(l){audio[l].pause();audio[l].currentTime=0;});
    resetBtns();clearSegs();}

  function setBook(i){state.book=i;renderBooks();renderChapters();renderThumbs();setPage(0);}
  function setPage(pi){
    var b=book(); pi=Math.max(0,Math.min(b.pages.length-1,pi)); state.page=pi; stopAll();
    var p=page();
    if(p.image){elImg.src=p.image;elImg.style.display="";elWrap.classList.remove("noimg");elWrap.textContent="";elWrap.appendChild(elImg);}
    else{elImg.removeAttribute("src");elWrap.classList.add("noimg");elWrap.textContent="This page has no image.";}
    elInfo.textContent="Page "+p.n+" / "+b.pages[b.pages.length-1].n;
    elEn.innerHTML=spans(p.en.segs,"en"); elEs.innerHTML=spans(p.es.segs,"es");
    audio.en.src=p.en.audio||""; audio.es.src=p.es.audio||"";
    btn.en.disabled=!p.en.audio; btn.es.disabled=!p.es.audio;
    highlightNav();
  }
  function play(lang){var other=lang==="en"?"es":"en";audio[other].pause();
    var a=audio[lang]; if(a.paused)a.play(); else a.pause();}

  ["en","es"].forEach(function(lang){
    var a=audio[lang]; a._cur=-2;
    a.addEventListener("play",function(){resetBtns();btn[lang].classList.add("playing");
      btn[lang].querySelector(".ico").innerHTML="&#10073;&#10073;";});
    a.addEventListener("pause",function(){btn[lang].classList.remove("playing");
      btn[lang].querySelector(".ico").innerHTML="&#9654;";});
    a.addEventListener("timeupdate",function(){
      if(a.paused)return;  // only follow the reading during playback (browsers fire timeupdate at t=0 on load)
      var segs=Array.prototype.slice.call(document.querySelectorAll('.seg[data-lang="'+lang+'"]'));
      var t=a.currentTime,cur=-1;
      for(var i=0;i<segs.length;i++){var s=segs[i]; if(s.dataset.start==null)continue;
        if(t>=parseFloat(s.dataset.start)&&t<parseFloat(s.dataset.end)){cur=i;break;}}
      if(cur===a._cur)return; a._cur=cur;
      segs.forEach(function(s){s.classList.remove("active-"+lang);});
      if(cur>=0){segs[cur].classList.add("active-"+lang);
        segs[cur].scrollIntoView({block:"center",behavior:"smooth"});}
    });
    a.addEventListener("ended",function(){a._cur=-2;clearSegs();resetBtns();});
  });

  btn.en.onclick=function(){play("en");}; btn.es.onclick=function(){play("es");};
  $("prev").onclick=function(){setPage(state.page-1);};
  $("next").onclick=function(){setPage(state.page+1);};
  document.addEventListener("keydown",function(e){
    if(e.key==="ArrowRight")setPage(state.page+1);
    else if(e.key==="ArrowLeft")setPage(state.page-1);});

  $("title").textContent=D.title||"Bilingual Reader";
  renderBooks(); setBook(0);
})();
</script>
</body></html>
"""
