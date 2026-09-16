"""Reverse-proxy header and path handling.

Both cases below are real bugs this rail shipped and then found by actually loading the app
through the proxy rather than by reading the code. Neither raised anything: one returned HTTP 200
full of binary, the other returned a clean 404 from Next.js that looked like a missing
application. They are regression tests, not hypotheticals.
"""
from openmaic_app.api import proxy
from openmaic_app.config import settings


# --- path -------------------------------------------------------------------------------------

def test_upstream_path_restores_the_public_prefix():
    """The gateway strips /openmaic and the router is mounted at /api/app, so the handler only
    sees the tail. Next.js built with a basePath serves AT the prefix, so it has to go back on."""
    assert proxy.upstream_path("_next/static/chunk.js") == "/openmaic/api/app/_next/static/chunk.js"


def test_root_has_no_trailing_slash():
    """The redirect-loop case. Next serves the root AT the prefix and 308s `/prefix/` to
    `/prefix`; FastAPI would then 307 back, with `/openmaic` already stripped from its Location.
    Forwarding the root without the slash is what breaks the cycle."""
    assert proxy.upstream_path("") == "/openmaic/api/app"
    assert proxy.upstream_path("/") == "/openmaic/api/app"


def test_upstream_path_does_not_double_the_separator():
    assert "//" not in proxy.upstream_path("/api/health").replace("://", "")


def test_upstream_path_follows_the_configured_prefix(monkeypatch):
    """The prefix is config, not a literal, because it has to equal the NEXT_BASE_PATH the image
    was built with — and that is a build argument set somewhere else entirely."""
    monkeypatch.setattr(settings, "public_prefix", "/elsewhere/app")
    assert proxy.upstream_path("x") == "/elsewhere/app/x"


# --- headers ----------------------------------------------------------------------------------

def test_content_encoding_is_relayed_not_stripped():
    """The body is relayed with aiter_raw(), i.e. still gzipped. Dropping the header hands the
    browser compressed bytes labelled as text — a 200 rendering as binary garbage."""
    assert "content-encoding" not in proxy._HOP_BY_HOP


def test_content_length_is_stripped():
    """StreamingResponse re-frames the body, so a relayed length can disagree with what is sent."""
    assert "content-length" in proxy._HOP_BY_HOP


def test_genuine_hop_by_hop_headers_are_stripped():
    for h in ("connection", "keep-alive", "transfer-encoding", "upgrade", "te", "trailer"):
        assert h in proxy._HOP_BY_HOP


def test_hop_by_hop_set_is_lowercase():
    """Lookups are done on `k.lower()`; a capitalised entry here would never match and the header
    would be relayed while the test suite still looked green."""
    assert all(h == h.lower() for h in proxy._HOP_BY_HOP)
