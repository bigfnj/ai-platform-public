"""Corpus chunking, embedding, and index management.

Ported from tools/snu-course-build.py (ai-notes). Runs synchronously — call from
a ThreadPoolExecutor so the FastAPI event loop stays unblocked during long index runs.
"""
from __future__ import annotations

import pathlib
import re
from typing import Callable

import duckdb
import numpy as np
import yaml

from . import broker as _broker

EMBED_BATCH = 32
CHUNK_CHARS = 1500
CHUNK_MIN = 200

FM_RE = re.compile(r'\A---\r?\n(.*?)\r?\n---\r?\n', re.S)
HEADING_RE = re.compile(r'^(#{1,6})\s+(.*\S)\s*$')
HTML_RE = re.compile(
    r'</?(?:table|thead|tbody|tfoot|tr|td|th|div|span|p|br)\b[^>]*>', re.I)
ESCAPE_RE = re.compile(r'\\([\\`*_{}\[\]()#+\-.!|<>~])')
OMITTED_RE = re.compile(r'^\s*\[Omitted [^\]]*\]\s*$', re.M)
BLANKS_RE = re.compile(r'\n{3,}')

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    path VARCHAR, title VARCHAR, topic_type VARCHAR, breadcrumb VARCHAR,
    canonical_url VARCHAR, release VARCHAR, heading VARCHAR, text VARCHAR,
    vec BLOB);
CREATE TABLE IF NOT EXISTS meta (k VARCHAR PRIMARY KEY, v VARCHAR);
"""
COLS = ['path', 'title', 'topic_type', 'breadcrumb', 'canonical_url',
        'release', 'heading', 'text']


# --- parsing -------------------------------------------------------------------

def _split_frontmatter(raw: str) -> tuple[dict, str]:
    m = FM_RE.match(raw)
    if not m:
        return {}, raw
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), raw[m.end():]


def _clean(body: str) -> str:
    body = HTML_RE.sub(' ', body)
    body = OMITTED_RE.sub('', body)
    body = ESCAPE_RE.sub(r'\1', body)
    return BLANKS_RE.sub('\n\n', body)


def _sections(body: str) -> list[tuple[str, str]]:
    stack: list[str] = []
    buf: list[str] = []
    out: list[tuple[str, str]] = []

    def flush() -> None:
        text = '\n'.join(buf).strip()
        if text:
            out.append((' > '.join(stack), text))
        buf.clear()

    for line in body.splitlines():
        m = HEADING_RE.match(line)
        if m:
            flush()
            depth = len(m.group(1))
            del stack[depth - 1:]
            stack.append(m.group(2))
        else:
            buf.append(line)
    flush()
    return out


def _pack(text: str) -> list[str]:
    if len(text) <= CHUNK_CHARS:
        return [text]
    out: list[str] = []
    cur = ''
    for para in re.split(r'\n\s*\n', text):
        if cur and len(cur) + len(para) + 2 > CHUNK_CHARS:
            out.append(cur)
            cur = para
        else:
            cur = (cur + '\n\n' + para) if cur else para
    if cur.strip():
        out.append(cur)
    return out


def _chunk_file(path: pathlib.Path, root: pathlib.Path) -> list[dict]:
    raw = path.read_text(encoding='utf-8', errors='replace')
    meta, body = _split_frontmatter(raw)
    crumb = meta.get('breadcrumb') or []
    if isinstance(crumb, list):
        crumb = ' > '.join(str(c) for c in crumb)
    base = {
        'path': str(path.relative_to(root)).replace('\\', '/'),
        'title': str(meta.get('title') or path.stem),
        'topic_type': str(meta.get('topic_type') or ''),
        'breadcrumb': str(crumb or ''),
        'canonical_url': str(meta.get('canonical_url') or ''),
        'release': str(meta.get('release') or ''),
    }
    rows = []
    for heading, text in _sections(_clean(body)):
        for piece in _pack(text):
            if len(piece.strip()) < CHUNK_MIN:
                continue
            rows.append(dict(base, heading=heading, text=piece.strip()))
    return rows


def _embed_text(row: dict) -> str:
    head = ' > '.join(p for p in (row['title'], row['breadcrumb'], row['heading']) if p)
    return (head + '\n\n' + row['text']) if head else row['text']


# --- index store ---------------------------------------------------------------

def _connect(db_path: str) -> duckdb.DuckDBPyConnection:
    p = pathlib.Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(p))
    con.execute(SCHEMA)
    return con


def index_stats(db_path: str) -> dict:
    """Return current index metadata without loading vectors."""
    p = pathlib.Path(db_path)
    if not p.exists():
        return {"present": False, "chunks": 0, "corpus": "", "embed_role": ""}
    try:
        con = duckdb.connect(str(p), read_only=True)
        chunks = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        meta = {r[0]: r[1] for r in con.execute("SELECT k, v FROM meta").fetchall()}
        con.close()
        return {
            "present": chunks > 0,
            "chunks": chunks,
            "corpus": meta.get("corpus", ""),
            "embed_role": meta.get("embed_role", ""),
        }
    except Exception:
        return {"present": False, "chunks": 0, "corpus": "", "embed_role": ""}


def load_matrix(db_path: str) -> tuple[list[dict], np.ndarray]:
    con = _connect(db_path)
    rows = con.execute('SELECT %s, vec FROM chunks' % ', '.join(COLS)).fetchall()
    if not rows:
        raise ValueError("index is empty — run index first")
    mat = np.stack([np.frombuffer(bytes(r[-1]), dtype=np.float32) for r in rows])
    mat /= (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
    return [dict(zip(COLS, r[:-1])) for r in rows], mat


# --- indexing job (runs in thread pool) ----------------------------------------

def run_index(
    corpus_path: str,
    db_path: str,
    embed_role: str,
    resume: bool = False,
    limit: int = 0,
    progress: Callable[[str], None] | None = None,
) -> dict:
    root = pathlib.Path(corpus_path).resolve()
    files = sorted(root.rglob('*.md'))
    if limit:
        files = files[:limit]
    if not files:
        raise ValueError(f"no .md files found under {root}")

    con = _connect(db_path)
    done: set[str] = set()
    if resume:
        done = {r[0] for r in con.execute('SELECT DISTINCT path FROM chunks').fetchall()}
    else:
        con.execute('DELETE FROM chunks')

    con.execute("INSERT OR REPLACE INTO meta VALUES ('embed_role', ?)", [embed_role])
    con.execute("INSERT OR REPLACE INTO meta VALUES ('corpus', ?)", [str(root)])

    pending: list[dict] = []
    total = 0
    skipped = 0

    def flush() -> None:
        nonlocal total
        if not pending:
            return
        vectors = _broker.embed_sync([_embed_text(r) for r in pending], embed_role)
        con.executemany(
            'INSERT INTO chunks VALUES (%s)' % ', '.join(['?'] * (len(COLS) + 1)),
            [[r[c] for c in COLS] + [np.asarray(v, dtype=np.float32).tobytes()]
             for r, v in zip(pending, vectors)])
        total += len(pending)
        pending.clear()

    for i, f in enumerate(files, 1):
        rel = str(f.relative_to(root)).replace('\\', '/')
        if rel in done:
            skipped += 1
            continue
        con.execute('DELETE FROM chunks WHERE path = ?', [rel])
        for row in _chunk_file(f, root):
            pending.append(row)
            if len(pending) >= EMBED_BATCH:
                flush()
        if i % 100 == 0 and progress:
            progress(f"{i}/{len(files)} files processed, {total} chunks so far")

    flush()
    summary = f"Done: {total} chunks from {len(files) - skipped} files ({skipped} skipped via resume)"
    if progress:
        progress(summary)
    return {"chunks": total, "files": len(files) - skipped, "skipped": skipped}
