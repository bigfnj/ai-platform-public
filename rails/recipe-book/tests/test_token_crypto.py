"""Google refresh tokens are encrypted at rest when RECIPE_BOOK_TOKEN_KEY is set — transparently
to callers — and legacy plaintext rows keep working and get migrated once the key is on."""
from __future__ import annotations

from cryptography.fernet import Fernet

from recipe_book import config, crypto, db


def _with_key(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "rb.db"))
    monkeypatch.setattr(config, "TOKEN_KEY", Fernet.generate_key().decode())


def test_crypto_roundtrip_and_plaintext_tolerance(monkeypatch):
    monkeypatch.setattr(config, "TOKEN_KEY", Fernet.generate_key().decode())
    ct = crypto.encrypt("secret-refresh-token")
    assert ct != "secret-refresh-token"          # actually encrypted
    assert crypto.is_ciphertext(ct)
    assert crypto.decrypt(ct) == "secret-refresh-token"
    # A legacy plaintext value is not a valid Fernet token -> returned unchanged.
    assert crypto.decrypt("legacy-plaintext") == "legacy-plaintext"
    assert not crypto.is_ciphertext("legacy-plaintext")


def test_no_key_is_passthrough(monkeypatch):
    monkeypatch.setattr(config, "TOKEN_KEY", "")
    assert not crypto.enabled()
    assert crypto.encrypt("x") == "x"
    assert crypto.decrypt("x") == "x"


def test_gtasks_token_encrypted_at_rest(tmp_path, monkeypatch):
    _with_key(monkeypatch, tmp_path)
    con = db.connect()
    try:
        db.init_db(con)
        db.gtasks_set(con, 1, "refresh-abc", "me@example.com", "Shopping List")
        raw = con.execute("SELECT refresh_token FROM gtasks_tokens WHERE owner_id=1"
                          ).fetchone()["refresh_token"]
        assert raw != "refresh-abc" and crypto.is_ciphertext(raw)   # stored ciphertext
        assert db.gtasks_get(con, 1)["refresh_token"] == "refresh-abc"  # getter decrypts
    finally:
        con.close()


def test_migration_encrypts_legacy_plaintext(tmp_path, monkeypatch):
    _with_key(monkeypatch, tmp_path)
    con = db.connect()
    try:
        db.init_db(con)
        # Pre-encryption row: plaintext written directly, bypassing gtasks_set.
        con.execute("INSERT INTO gtasks_tokens (owner_id, refresh_token, email, list_title, "
                    "connected_at) VALUES (1, 'legacy-plain', 'me@x.com', 'Shopping List', '2026-01-01')")
        con.commit()
        assert db.encrypt_gtasks_tokens_at_rest(con) == 1
        raw = con.execute("SELECT refresh_token FROM gtasks_tokens WHERE owner_id=1"
                          ).fetchone()["refresh_token"]
        assert raw != "legacy-plain" and crypto.is_ciphertext(raw)
        assert db.gtasks_get(con, 1)["refresh_token"] == "legacy-plain"   # still readable
        assert db.encrypt_gtasks_tokens_at_rest(con) == 0                 # idempotent
    finally:
        con.close()
