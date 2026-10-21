"""What co-worker's routes that name a file answer for a path the request sends: a NUL names nothing.

`/api/doc/{doc_path:path}` and the item routes (GET and PATCH `/api/inbox/{item_id}`, through
`_safe_item_path`) join what the request sends onto the inbox and resolve it. A NUL (`%00`, which
the server decodes into the path) reached `Path.resolve()`, which raises ValueError on one under
Python 3.11, the image's (python:3.11-slim), and each route answered 500. Measured on the old code
in that image: every doc path holding a NUL before its `.md` suffix, and every item id holding one,
anywhere in it. Python 3.13 and later resolve such a path on Windows (gh-106242), so on this box the
routes already answered 404 and never showed it. A raw NUL byte never reaches the app: the server's
HTTP parser refuses the request line with 400 (h11 and httptools alike, measured).

Each NUL path is sent on the running Python and again under `_resolve_as_on_311()`, which makes
`Path.resolve()` raise as 3.11's does for a path holding a NUL and resolves every other path for
real, so these fail on the old code on every Python. The real document and item are the control:
the stand-in still serves them.
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from co_worker_app import main as mainmod
from co_worker_app.config import settings
from co_worker_app.main import app

HDR = {"X-Platform-User": "owner-one", "X-Platform-Admin": "1"}
PYTHONS = ["this one", "3.11"]


@pytest.fixture(autouse=True)
def _no_standalone(monkeypatch):
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """An inbox holding one item and one document, so a 404 is about the path and a 200 is real."""
    inbox = tmp_path / "inbox"
    (inbox / "notes").mkdir(parents=True)
    (inbox / "item-1.json").write_text(
        json.dumps({"title": "a thing", "source": "email", "period": "2026W34"}), encoding="utf-8")
    (inbox / "notes" / "week.md").write_text("# week", encoding="utf-8")
    monkeypatch.setattr(settings, "inbox_dir", str(inbox))
    monkeypatch.setattr(mainmod, "_parse_cache", None)
    return TestClient(app, raise_server_exceptions=False)


def _resolve_as_on_311():
    """Path.resolve() as Python 3.11 has it for a path holding a NUL: it raises ValueError, on
    Linux ("embedded null byte") and on Windows ("embedded null character") alike (both
    measured). 3.13 and later resolve such a path on Windows, so a test run there would not meet
    what the image's 3.11 does. Every other path is resolved for real. iep's test_sharing.py has
    the same stand-in; the rails import nothing of each other's."""
    real = Path.resolve

    def resolve(self, strict=False):
        if "\x00" in str(self):
            raise ValueError("embedded null byte")
        return real(self, strict=strict)
    return mock.patch.object(Path, "resolve", resolve)


def _on(python: str):
    return _resolve_as_on_311() if python == "3.11" else contextlib.nullcontext()


@pytest.mark.parametrize("sent", ["a%00b.md", "%00.md", "%00notes/week.md", "notes%00/week.md",
                                  "notes/%00week.md", "notes/week%00.md",
                                  "notes%2F%00%2Fweek.md"])
@pytest.mark.parametrize("python", PYTHONS)
def test_a_nul_in_a_doc_path_names_no_document(client, python, sent):
    """Wherever it is before the suffix, a NUL is a 404 once the caller is identified: an
    unidentified caller still gets the 401 first."""
    with _on(python):
        r = client.get(f"/api/doc/{sent}", headers=HDR)
        assert r.status_code == 404, r.text
        assert r.json() == {"detail": "document not found"}
        assert client.get(f"/api/doc/{sent}").status_code == 401


@pytest.mark.parametrize("method", ["GET", "PATCH"])
@pytest.mark.parametrize("sent", ["item-1%00", "%00item-1", "it%00em-1", "%00"])
@pytest.mark.parametrize("python", PYTHONS)
def test_a_nul_in_an_item_id_names_no_item(client, python, sent, method):
    with _on(python):
        r = client.request(method, f"/api/inbox/{sent}", headers=HDR,
                           json={"status": "done"} if method == "PATCH" else None)
        assert r.status_code == 404, r.text
        assert r.json() == {"detail": "item not found"}


@pytest.mark.parametrize("python", PYTHONS)
def test_the_document_and_the_item_are_still_served_and_the_string_checks_keep_their_answers(
        client, python):
    """The control. Under the stand-in the real document and item are served and the item's
    triage is written. The checks that read the string alone still answer first, as before: a
    NUL after the suffix is not markdown (400), and an id with a backslash is invalid (400)."""
    with _on(python):
        doc = client.get("/api/doc/notes/week.md", headers=HDR)
        assert doc.status_code == 200 and doc.json()["content"] == "# week", doc.text
        item = client.get("/api/inbox/item-1", headers=HDR)
        assert item.status_code == 200 and item.json()["_id"] == "item-1", item.text
        patched = client.patch("/api/inbox/item-1", json={"status": "done"}, headers=HDR)
        assert patched.json() == {"_id": "item-1", "_status": "done"}, patched.text
        assert client.get("/api/doc/notes/week.md%00", headers=HDR).status_code == 400
        assert client.get("/api/inbox/a%5C%00b", headers=HDR).status_code == 400
