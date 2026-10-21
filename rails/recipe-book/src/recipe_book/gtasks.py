"""Google Tasks client for the shopping list's per-user 'Send to Phone' button.

Google Keep has no public API for consumer accounts, so we push the shopping list into
**Google Tasks** instead — a checkable list that surfaces in the Google Tasks app, the
Calendar side-panel, and Gmail on the phone.

**Per-user, not shared.** A single OAuth *app* (``config.GTASKS_CLIENT_ID/SECRET`` +
``GTASKS_REDIRECT_URI``) fronts an in-app consent flow; each user connects their OWN Google
account, and that user's refresh token is stored per-owner in the DB. This module is the thin
REST layer: build the consent URL, exchange the auth code, refresh access tokens (cached per
refresh token), and insert tasks. No Google client libraries.
"""
from __future__ import annotations

import base64
import binascii
import json
import threading
import time
import urllib.parse

import httpx

from recipe_book import config

_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_API = "https://tasks.googleapis.com/tasks/v1"
# 'openid email' lets us label the connection with the account's address; 'tasks' is the write.
_SCOPES = "openid email https://www.googleapis.com/auth/tasks"
_TIMEOUT = 20.0

# access-token cache keyed by refresh token: {refresh_token: (access_token, expiry_epoch)}.
# `_lock` guards the two dicts and is NEVER held across a network call. It used to be held for
# the whole refresh round trip (20 s timeout), so one user's stalled refresh blocked every other
# user's microsecond cache hit and parked a threadpool worker each. Now each refresh token has
# its own lock, which keeps a refresh single-flight per connection and nothing more.
_lock = threading.Lock()
_cache: dict[str, tuple[str, float]] = {}
_refreshing: dict[str, threading.Lock] = {}


class GTasksError(RuntimeError):
    """A Google Tasks call failed (auth, network, or API error) — surfaced to the caller.

    `revoked` is True only when Google itself answered that the refresh token is dead: an OAuth
    error body whose `error` is `invalid_grant`. That is the one answer that justifies forgetting
    the user's connection. The caller used to look for the substring "invalid_grant" in a message
    this module put on EVERY non-200 refresh, so a Google 503, a 429 or a bad client secret on
    the server disconnected every user who pressed Send to Phone.
    """

    def __init__(self, message: str, *, revoked: bool = False) -> None:
        super().__init__(message)
        self.revoked = revoked


def _oauth_error(resp) -> str:
    """The OAuth `error` code from a token-endpoint answer, or "" when there is none to read."""
    try:
        body = resp.json()
    except ValueError:
        return ""
    code = body.get("error") if isinstance(body, dict) else None
    return code if isinstance(code, str) else ""


def app_configured() -> bool:
    return config.gtasks_configured()


def auth_url(state: str) -> str:
    """The Google consent URL to send a connecting user to (web flow, offline access)."""
    return _AUTH_URI + "?" + urllib.parse.urlencode({
        "client_id": config.GTASKS_CLIENT_ID,
        "redirect_uri": config.GTASKS_REDIRECT_URI,
        "response_type": "code",
        "scope": _SCOPES,
        "access_type": "offline",
        "prompt": "consent",       # force a refresh_token even on re-consent
        "include_granted_scopes": "true",
        "state": state,
    })


def exchange_code(code: str) -> dict:
    """Exchange an authorization code for tokens (called from the OAuth callback)."""
    try:
        resp = httpx.post(_TOKEN_URL, data={
            "client_id": config.GTASKS_CLIENT_ID,
            "client_secret": config.GTASKS_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": config.GTASKS_REDIRECT_URI,
        }, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        raise GTasksError(f"could not reach Google to exchange the code: {exc}") from exc
    if resp.status_code != 200:
        raise GTasksError(f"token exchange rejected ({resp.status_code}): {resp.text[:200]}")
    return resp.json()


def email_from_id_token(id_token: str) -> str | None:
    """Best-effort read of the account email from a Google id_token (JWT). The token came
    straight from Google over TLS, so we read the payload for display without verifying it."""
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)  # restore base64 padding
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data.get("email")
    except (IndexError, ValueError, binascii.Error):
        return None


def _cached(refresh_token: str) -> str | None:
    with _lock:
        hit = _cache.get(refresh_token)
    if hit and time.time() < hit[1] - 60:
        return hit[0]
    return None


def _access_token(refresh_token: str) -> str:
    """A valid access token for a given refresh token, refreshed + cached as needed."""
    tok = _cached(refresh_token)
    if tok:
        return tok
    with _lock:
        per_token = _refreshing.setdefault(refresh_token, threading.Lock())
    with per_token:
        tok = _cached(refresh_token)          # refreshed by another request while this one waited
        if tok:
            return tok
        try:
            resp = httpx.post(_TOKEN_URL, data={
                "client_id": config.GTASKS_CLIENT_ID,
                "client_secret": config.GTASKS_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }, timeout=_TIMEOUT)
        except httpx.HTTPError as exc:
            raise GTasksError(f"could not reach Google to refresh the token: {exc}") from exc
        if resp.status_code != 200:
            code = _oauth_error(resp)
            if code == "invalid_grant":
                # The user revoked access or the token expired (e.g. a still-in-'Testing' OAuth
                # app). The caller drops the stored token and re-prompts connect.
                with _lock:
                    _refreshing.pop(refresh_token, None)
                raise GTasksError(f"Google says this connection is no longer valid "
                                  f"({resp.status_code} invalid_grant)", revoked=True)
            # Anything else (5xx, 429, invalid_client from a bad server secret) says nothing
            # about the user's grant, so the connection is kept and the next press retries.
            raise GTasksError(f"token refresh failed ({resp.status_code}"
                              f"{' ' + code if code else ''}): {resp.text[:200]}")
        data = resp.json()
        tok = data["access_token"]
        with _lock:
            _cache[refresh_token] = (tok, time.time() + int(data.get("expires_in", 3600)))
        return tok


def _auth(refresh_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_access_token(refresh_token)}"}


def _list_id(refresh_token: str, title: str) -> str:
    """The id of the task list named ``title`` in this account, creating it if absent."""
    try:
        resp = httpx.get(f"{_API}/users/@me/lists", headers=_auth(refresh_token),
                         params={"maxResults": 100}, timeout=_TIMEOUT)
        resp.raise_for_status()
        for tl in resp.json().get("items", []):
            if tl.get("title", "").strip().lower() == title.strip().lower():
                return tl["id"]
        created = httpx.post(f"{_API}/users/@me/lists", headers=_auth(refresh_token),
                             json={"title": title}, timeout=_TIMEOUT)
        created.raise_for_status()
        return created.json()["id"]
    except httpx.HTTPStatusError as exc:
        raise GTasksError(f"Google Tasks API error ({exc.response.status_code}): "
                          f"{exc.response.text[:200]}") from exc
    except httpx.HTTPError as exc:
        raise GTasksError(f"could not reach Google Tasks: {exc}") from exc


def push(refresh_token: str, labels: list[str], list_title: str) -> dict:
    """Insert each label as a task in ``list_title`` for the account behind ``refresh_token``.
    Returns ``{sent, list_title}``. Inserts in reverse so the shopping-list order is preserved
    (a freshly-inserted task goes to the top). Blank labels are skipped.
    """
    clean = [s.strip() for s in labels if s and s.strip()]
    if not clean:
        return {"sent": 0, "list_title": list_title}
    lid = _list_id(refresh_token, list_title)
    sent = 0
    try:
        for title in reversed(clean):
            r = httpx.post(f"{_API}/lists/{lid}/tasks", headers=_auth(refresh_token),
                           json={"title": title[:1024]}, timeout=_TIMEOUT)
            r.raise_for_status()
            sent += 1
    except httpx.HTTPStatusError as exc:
        raise GTasksError(f"Google Tasks rejected an item ({exc.response.status_code}): "
                          f"{exc.response.text[:200]}") from exc
    except httpx.HTTPError as exc:
        raise GTasksError(f"lost the connection to Google Tasks after {sent} item(s): {exc}") from exc
    return {"sent": sent, "list_title": list_title}
