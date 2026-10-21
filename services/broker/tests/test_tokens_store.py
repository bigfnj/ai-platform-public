"""The token STORE: minting, validation, revocation, and what reaches disk.

The auth path is tested in test_auth_token.py. This file covers the half that no amount of
correct comparison can save you from — a token list you can read off disk.
"""
from __future__ import annotations

import json

import pytest

from app.config import TOKEN_SCOPES, BrokerSettings


def _settings(tmp_path) -> BrokerSettings:
    return BrokerSettings(tokens_file=str(tmp_path / "tokens.json"))


def test_the_plaintext_is_never_written_to_disk(tmp_path):
    """The whole reason a hash is stored. If the plaintext lands in tokens.json then the file
    IS the credential set, and every comparison in require_token is beside the point."""
    s = _settings(tmp_path)
    plain, row = s.add_token("studio-desktop", "inference")
    raw = (tmp_path / "tokens.json").read_text(encoding="utf-8")
    assert plain not in raw
    # The stored prefix is short enough to be useless as a secret: 11 chars of a 46-char token.
    assert row["prefix"] in raw and len(row["prefix"]) < len(plain) / 3
    assert "hash" in row and row["hash"] != plain


def test_a_minted_token_authenticates_and_a_revoked_one_does_not(tmp_path):
    s = _settings(tmp_path)
    plain, row = s.add_token("studio", "inference")
    import hashlib
    digest = hashlib.sha256(plain.encode("utf-8")).hexdigest()
    assert any(r["hash"] == digest for r in s.tokens())
    assert s.revoke_token(row["id"]) is True
    assert not any(r["hash"] == digest for r in s.tokens())


def test_a_duplicate_label_is_refused(tmp_path):
    """The label is the handle you revoke by. Two rows with the same one makes the workflow
    ambiguous at exactly the moment it matters."""
    s = _settings(tmp_path)
    s.add_token("studio", "inference")
    with pytest.raises(ValueError, match="already exists"):
        s.add_token("studio", "full")


def test_an_unknown_scope_is_refused(tmp_path):
    s = _settings(tmp_path)
    with pytest.raises(ValueError, match="scope"):
        s.add_token("studio", "admin")


def test_an_empty_or_overlong_label_is_refused(tmp_path):
    s = _settings(tmp_path)
    for bad in ("", "   ", "x" * 65):
        with pytest.raises(ValueError):
            s.add_token(bad, "inference")


def test_a_label_with_shell_or_json_metacharacters_is_refused(tmp_path):
    """Labels are rendered in a web UI and written into JSON. Charset-guard at the door rather
    than escaping at every use site."""
    s = _settings(tmp_path)
    for bad in ("bad;label", "a\"b", "<script>", "a\nb"):
        with pytest.raises(ValueError):
            s.add_token(bad, "inference")


def test_revoking_something_that_does_not_exist_reports_false(tmp_path):
    assert _settings(tmp_path).revoke_token("nope") is False


def test_a_corrupt_store_fails_closed(tmp_path):
    """tokens() is read on the AUTH path, so a malformed file must mean 'nobody authenticates'
    rather than taking the broker down — and must not mean 'everybody authenticates'."""
    p = tmp_path / "tokens.json"
    for junk in ("{not json", '{"a": 1}', "[]", '[{"id": "x"}]'):
        p.write_text(junk, encoding="utf-8")
        assert BrokerSettings(tokens_file=str(p)).tokens() == []


def test_a_corrupt_store_still_counts_as_CONFIGURED(tmp_path):
    """The distinction that closes the footgun: the store existing is what enforces. A file
    that fails to parse must not read as 'no tokens configured', because that would reopen the
    broker to everyone the moment it got corrupted."""
    p = tmp_path / "tokens.json"
    p.write_text("{not json", encoding="utf-8")
    s = BrokerSettings(tokens_file=str(p))
    assert s.tokens() == [] and s.tokens_configured() is True


def test_rows_missing_required_fields_are_dropped_not_trusted(tmp_path):
    p = tmp_path / "tokens.json"
    p.write_text(json.dumps([
        {"id": "ok", "label": "good", "scope": "inference", "hash": "h", "prefix": "bt_x"},
        {"id": "nohash", "label": "bad", "scope": "inference"},
        {"id": "badscope", "label": "bad", "scope": "root", "hash": "h"},
        "not-a-dict",
    ]), encoding="utf-8")
    ids = [r["id"] for r in BrokerSettings(tokens_file=str(p)).tokens()]
    assert ids == ["ok"]


def test_every_scope_is_mintable(tmp_path):
    s = _settings(tmp_path)
    for i, scope in enumerate(TOKEN_SCOPES):
        _plain, row = s.add_token(f"host-{i}", scope)
        assert row["scope"] == scope


def test_concurrent_mints_do_not_clobber_each_other(tmp_path):
    """Both writers do read-modify-write on the whole store, so without serialisation the
    second write wins and the first token vanishes -- minted, handed to an operator, and not
    actually valid. Eight threads is enough to lose one reliably on an unlocked build."""
    import threading
    s = _settings(tmp_path)
    s.add_token("seed", "inference")            # create the file so all eight race on a write
    errors: list[Exception] = []

    def mint(i: int) -> None:
        try:
            s.add_token(f"host-{i}", "inference")
        except Exception as exc:                # noqa: BLE001 - reported, not swallowed
            errors.append(exc)

    threads = [threading.Thread(target=mint, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"minting raised: {errors}"
    labels = sorted(r["label"] for r in s.tokens())
    assert labels == ["host-0", "host-1", "host-2", "host-3", "host-4", "host-5", "host-6",
                      "host-7", "seed"], f"a mint was lost: {labels}"


def test_a_revoke_is_not_lost_to_a_concurrent_mint(tmp_path):
    """The one that actually matters. Losing a MINT is inconvenient; losing a REVOKE means a
    credential you believed you had withdrawn is still live."""
    import threading
    s = _settings(tmp_path)
    _p, doomed = s.add_token("decommissioned", "full")
    for i in range(6):
        s.add_token(f"other-{i}", "inference")

    done = threading.Event()

    def mint_loop() -> None:
        i = 0
        while not done.is_set() and i < 20:
            s.add_token(f"new-{i}", "inference")
            i += 1

    t = threading.Thread(target=mint_loop)
    t.start()
    assert s.revoke_token(doomed["id"]) is True
    done.set()
    t.join()

    live = [r["label"] for r in s.tokens()]
    assert "decommissioned" not in live, "the revoke was clobbered by a concurrent mint"


# --- the store is parsed once per version of the file ------------------------------------------
# require_token reads tokens() for every named-token request, from threadpool threads, and each
# read held tokens.json open: one of the openers a mint's or a revoke's atomic replace has to wait
# out (app/fsutil.py). Now a stat decides whether the last parse still holds.

@pytest.fixture()
def opens(monkeypatch):
    """Every open of a tokens.json, counted. Path.read_text and read_bytes both open through
    Path.open, so this counts the store being read whichever way the reader does it."""
    from pathlib import Path
    real = Path.open
    seen: list[str] = []

    def counted(self, *a, **k):
        if self.name == "tokens.json":
            seen.append(str(self))
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "open", counted)
    return seen


def test_an_unchanged_store_is_read_once(tmp_path, opens):
    s = _settings(tmp_path)
    s.add_token("studio", "inference")
    opens.clear()
    for _ in range(20):
        assert [r["label"] for r in s.tokens()] == ["studio"]
    assert len(opens) == 1, f"tokens.json was opened {len(opens)} times for 20 reads"


def test_a_named_token_request_does_not_open_the_store_each_time(tmp_path, opens):
    """The path the cache is for: require_token, once per request."""
    from types import SimpleNamespace

    from app.main import require_token
    s = _settings(tmp_path)
    plain, _row = s.add_token("studio", "inference")
    opens.clear()
    app = SimpleNamespace(state=SimpleNamespace(settings=s, token_seen={}))
    for _ in range(10):
        req = SimpleNamespace(url=SimpleNamespace(path="/v1/status"), method="GET",
                              headers={"authorization": f"Bearer {plain}"},
                              state=SimpleNamespace(), app=app)
        require_token(req)
        assert req.state.broker_scope == "inference"
    assert len(opens) <= 1


def test_a_file_replaced_by_someone_else_is_read_again(tmp_path):
    """A hand edit, or another broker process: the replace puts a NEW file in place. Here it is
    the same size and carries the same write time, so the file's identity is all that differs:
    the case a size-and-mtime check would serve stale."""
    import os
    s = _settings(tmp_path)
    s.add_token("studio", "inference")
    assert [r["label"] for r in s.tokens()] == ["studio"]
    p = tmp_path / "tokens.json"
    st = p.stat()
    edited = tmp_path / "edited.json"
    edited.write_bytes(p.read_bytes().replace(b'"studio"', b'"other1"'))
    os.utime(edited, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(edited, p)
    assert (p.stat().st_size, p.stat().st_mtime_ns) == (st.st_size, st.st_mtime_ns)
    assert [r["label"] for r in s.tokens()] == ["other1"]


def test_this_brokers_own_revoke_lands_even_where_the_file_looks_unchanged(tmp_path, monkeypatch):
    """A file system whose ids and times do not move (FAT's 2 s mtime, a share reporting inode 0)
    would let the old parse stand after a revoke. The writer drops the cache itself, so a revoke
    made here is on the very next request whatever the stat says."""
    from app import config

    def frozen(path):
        if not path.exists():
            raise FileNotFoundError(path)
        return (0, 0, 0, 0)

    monkeypatch.setattr(config, "_file_signature", frozen)
    s = _settings(tmp_path)
    _plain, row = s.add_token("doomed", "full")
    assert [r["label"] for r in s.tokens()] == ["doomed"]
    assert s.revoke_token(row["id"]) is True
    assert s.tokens() == [], "the revoke did not land: the cached parse was served"


def test_a_read_that_fails_is_not_remembered(tmp_path, monkeypatch):
    """A sharing violation for a moment must not lock every named token out until the file next
    changes: a failed read is answered [] and the next request reads again."""
    from pathlib import Path
    _settings(tmp_path).add_token("studio", "inference")
    s = _settings(tmp_path)                     # a fresh broker: nothing parsed yet
    real = Path.open
    fail = {"once": True}

    def flaky(self, *a, **k):
        if self.name == "tokens.json" and fail.pop("once", False):
            raise PermissionError(13, "held", str(self))
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "open", flaky)
    assert s.tokens() == []
    assert [r["label"] for r in s.tokens()] == ["studio"]


def test_what_tokens_returns_is_a_copy(tmp_path):
    s = _settings(tmp_path)
    s.add_token("studio", "inference")
    rows = s.tokens()
    rows.append({"id": "planted", "label": "planted", "scope": "full", "hash": "h"})
    rows[0]["scope"] = "full"
    again = s.tokens()
    assert [r["label"] for r in again] == ["studio"] and again[0]["scope"] == "inference"


def test_a_deleted_store_reads_as_empty_at_once(tmp_path):
    s = _settings(tmp_path)
    s.add_token("studio", "inference")
    assert s.tokens()
    (tmp_path / "tokens.json").unlink()
    assert s.tokens() == []
