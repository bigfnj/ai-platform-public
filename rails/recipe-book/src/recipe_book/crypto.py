"""Encrypt secrets (Google refresh tokens) at rest.

A Fernet key from ``RECIPE_BOOK_TOKEN_KEY`` (a urlsafe-base64 32-byte key, kept in
``deploy/.env`` — NOT on the data volume) encrypts the per-user Google refresh tokens stored
in the DB. Reads tolerate legacy PLAINTEXT values (pre-encryption rows), so turning the key on
is seamless: existing rows keep working, ``db.init_db`` runs a one-time migration to encrypt
them, and every subsequent write stores ciphertext. With no key set the behaviour is unchanged
(plaintext) — encryption is opt-in via the env key.

Rotating/losing the key makes stored tokens undecryptable; affected users simply re-connect.
"""
from __future__ import annotations

from recipe_book import config


def _fernet():
    """A Fernet built from the configured key, or None when encryption is off. Cheap to build,
    so it is not cached (keeps it trivially testable when the env key changes)."""
    key = (config.TOKEN_KEY or "").strip()
    if not key:
        return None
    from cryptography.fernet import Fernet
    return Fernet(key.encode())


def enabled() -> bool:
    return _fernet() is not None


def encrypt(plaintext: str | None) -> str | None:
    """Fernet ciphertext (urlsafe-base64 text) for a secret, or the value unchanged when no key
    is configured (or the value is None)."""
    f = _fernet()
    if f is None or plaintext is None:
        return plaintext
    return f.encrypt(plaintext.encode()).decode()


def decrypt(stored: str | None) -> str | None:
    """Plaintext for a stored value. Tolerates a legacy plaintext value (decrypt fails -> return
    it unchanged) so pre-encryption rows keep working. No key -> return unchanged."""
    f = _fernet()
    if f is None or stored is None:
        return stored
    from cryptography.fernet import InvalidToken
    try:
        return f.decrypt(stored.encode()).decode()
    except (InvalidToken, ValueError):
        return stored


def is_ciphertext(stored: str | None) -> bool:
    """True if ``stored`` decrypts under the current key (i.e. it is already encrypted). Used by
    the one-time migration to skip rows that are already ciphertext."""
    f = _fernet()
    if f is None or not stored:
        return False
    from cryptography.fernet import InvalidToken
    try:
        f.decrypt(stored.encode())
        return True
    except (InvalidToken, ValueError):
        return False
