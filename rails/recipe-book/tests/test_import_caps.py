"""File import is bounded: count, bytes per file, .docx inflation, and the text sent onward.

Recorded 2026-09-11 and re-verified by the 2026-10-08 audit: /api/recipes/extract/files looped
f.file.read() over an unbounded list, so a 40 MB file was read whole before its 422, and a
large text file went to the assistant as a single prompt (the URL import already cut at
16,000 characters). Every refusal here also asserts the assistant was never asked.
"""
from __future__ import annotations

import io
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from recipe_book import broker, extraction
from recipe_book.api.routers import authoring as authrouter

HDR = {"X-Platform-User": "bob", "X-Platform-Admin": "0"}


@pytest.fixture()
def asked(monkeypatch):
    seen: list[str] = []

    def fake_chat_json(model, messages, **_kw):
        seen.append(messages[-1]["content"])
        return {"title": "Soup", "ingredients": ["1 onion"], "instructions": ["Cook."]}

    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    monkeypatch.setattr(broker, "chat_json", fake_chat_json)
    return seen


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(authrouter.router)
    return TestClient(app)


def _post(client, files):
    return client.post("/api/recipes/extract/files", headers=HDR,
                       files=[("files", f) for f in files])


def test_too_many_files_is_413(client, asked, monkeypatch):
    monkeypatch.setattr(extraction, "MAX_FILES", 2)
    r = _post(client, [(f"r{i}.txt", b"1 onion, chopped. Cook it.") for i in range(3)])
    assert r.status_code == 413
    assert asked == []


def test_a_file_over_the_byte_cap_is_413(client, asked, monkeypatch):
    monkeypatch.setattr(extraction, "MAX_FILE_BYTES", 1024)
    r = _post(client, [("big.txt", b"x" * 2048)])
    assert r.status_code == 413 and "larger than" in r.json()["detail"]
    assert asked == []


def _docx_bomb(xml_bytes: int) -> bytes:
    para = "<w:p><w:r><w:t>" + "one onion " * 8 + "</w:t></w:r></w:p>"
    body = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"><w:body>' + para * (xml_bytes // len(para))
            + "</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("word/document.xml", body)
    return buf.getvalue()


def test_a_docx_that_inflates_past_the_budget_is_413(client, asked, monkeypatch):
    monkeypatch.setattr(extraction, "_MAX_DOCX_XML", 1024 * 1024)
    data = _docx_bomb(2 * 1024 * 1024)
    assert len(data) < 100_000
    r = _post(client, [("bomb.docx", data)])
    assert r.status_code == 413 and "unpacks" in r.json()["detail"]
    assert asked == []


def test_the_text_sent_to_the_assistant_is_capped_like_the_url_import(client, asked):
    long_text = ("1 onion, chopped. " * 5000).encode()           # ~90,000 characters
    r = _post(client, [("long.txt", long_text)])
    assert r.status_code == 200, r.text
    sent = asked[-1].split("RECIPE TEXT:\n", 1)[1]
    assert len(sent) == extraction.MAX_TEXT_CHARS


def test_an_ordinary_import_still_works(client, asked):
    r = _post(client, [("soup.txt", b"Onion soup\n1 onion\nCook it.")])
    assert r.status_code == 200 and r.json()["draft"]["title"] == "Soup"
