"""Ingest the SME knowledge base into the chunk index.

Each immediate subfolder of ``seed/knowledge-base`` is one collection. Ingest is
fingerprinted: a collection is re-embedded only when its markdown actually changed, so a
container restart is free rather than a full re-embed of the corpus.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from smb_partner import broker, config, rag, store

log = logging.getLogger("smb_partner.ingest")

_FINGERPRINT_KEY = "seed_fingerprints"


def _label(name: str) -> str:
    return name.replace("-", " ").replace("_", " ").title()


def _fingerprint(folder: Path) -> str:
    """Content hash of every markdown file in a collection (path + bytes)."""
    h = hashlib.sha256()
    for path in sorted(folder.rglob("*.md")):
        if path.name.startswith("_") or path.name.upper() == "README.MD":
            continue
        h.update(path.relative_to(folder).as_posix().encode("utf-8"))
        h.update(path.read_bytes())
    return h.hexdigest()


def _load_fingerprints() -> dict[str, str]:
    try:
        return json.loads(store.get_meta(_FINGERPRINT_KEY, "{}"))
    except json.JSONDecodeError:
        return {}


def ingest_seed(*, force: bool = False) -> dict:
    """Ingest every collection whose content changed. Returns a per-collection report.

    Never raises on a single collection's failure: an unreachable embedder or one malformed
    folder must not stop the rail from booting with the corpus it already has indexed.
    """
    root = config.SEED_KB_DIR
    if not root.is_dir():
        log.warning("seed knowledge base not found at %s", root)
        return {"root": str(root), "found": False, "collections": []}

    prints = _load_fingerprints()
    report: list[dict] = []
    # Seed collections currently in the index, so the two removal cases below can be detected:
    # a folder that is now empty, and a folder that has been deleted outright.
    indexed = {c["name"] for c in store.collections() if c.get("origin") == "seed"}
    seen: set[str] = set()
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        name = folder.name
        seen.add(name)
        try:
            fp = _fingerprint(folder)
        except OSError as exc:
            report.append({"collection": name, "status": "error", "detail": str(exc)})
            continue
        if not force and prints.get(name) == fp:
            report.append({"collection": name, "status": "unchanged"})
            continue
        rows = rag.load_collection(folder, name)
        if not rows:
            # A collection that has *become* empty must be cleared, not skipped. Emptying a folder
            # used to leave its old chunks indexed forever: placeholder scaffolding deleted from
            # discovery/, objection-handling/, solution-plays/ and customer-stories/ kept being
            # retrieved and cited for weeks of edits afterwards, because nothing ever removed it.
            removed = store.delete_collection(name) if name in indexed else 0
            report.append({"collection": name, "status": "empty", "removed": removed})
            prints[name] = fp
            continue
        try:
            vectors = rag.embed_texts([r["text"] for r in rows])
        except broker.BrokerError as exc:
            log.warning("embedding %s failed: %s", name, exc)
            report.append({"collection": name, "status": "error", "detail": str(exc)})
            continue
        count = store.replace_collection(name, _label(name), "seed", rows, vectors)
        prints[name] = fp
        report.append({"collection": name, "status": "ingested", "chunks": count})
        log.info("ingested %s: %d chunks", name, count)

    # A collection whose folder was deleted outright is never iterated above, so its chunks would
    # otherwise survive indefinitely. Retiring `partner-programs/` hit exactly this.
    for orphan in sorted(indexed - seen):
        removed = store.delete_collection(orphan)
        prints.pop(orphan, None)
        report.append({"collection": orphan, "status": "removed", "removed": removed})
        log.info("removed %s: folder no longer present (%s chunks)", orphan, removed)

    store.set_meta(_FINGERPRINT_KEY, json.dumps(prints))
    return {"root": str(root), "found": True, "collections": report}


def ingest_upload(name: str, text: str, *, source: str) -> int:
    """Index an ad-hoc document a partner uploaded, as its own 'upload' collection."""
    rows = rag.chunk_markdown(text, source=source, collection=name)
    if not rows:
        return 0
    vectors = rag.embed_texts([r["text"] for r in rows])
    return store.replace_collection(name, _label(name), "upload", rows, vectors)
