"""Per-user Google Tasks connection for the shopping list's 'Send to Phone'.

Each user links their OWN Google account through an in-app OAuth consent (a popup): the app
hands back a Google consent URL (``/connect``), Google redirects the browser to ``/callback``
(gateway-fronted, so the verified identity rides along), and we store that user's refresh
token per-owner. ``/status`` drives the UI; ``/disconnect`` forgets the token.
"""
from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from recipe_book import config, db, gtasks
from recipe_book.api import deps

router = APIRouter()


@router.get("/api/gtasks/status")
def gtasks_status(owner_id: int = Depends(deps.owner_id)) -> dict:
    """Whether the feature is available (OAuth app set up) and whether THIS user is connected."""
    if not gtasks.app_configured():
        return {"app_configured": False, "connected": False, "email": None,
                "list_title": config.GTASKS_LIST_TITLE}
    con = db.connect()
    try:
        row = db.gtasks_get(con, owner_id)
    finally:
        con.close()
    return {"app_configured": True, "connected": bool(row),
            "email": row["email"] if row else None,
            "list_title": (row["list_title"] if row and row["list_title"] else config.GTASKS_LIST_TITLE)}


@router.get("/api/gtasks/connect")
def gtasks_connect(owner_id: int = Depends(deps.owner_id)) -> dict:
    """Start a connect: mint a CSRF state bound to this owner, return the Google consent URL."""
    if not gtasks.app_configured():
        raise HTTPException(status_code=501, detail="Google Tasks isn't configured on this server.")
    state = secrets.token_urlsafe(24)
    con = db.connect()
    try:
        db.gtasks_state_create(con, state, owner_id)
    finally:
        con.close()
    return {"url": gtasks.auth_url(state)}


def _close(ok: bool, msg: str) -> HTMLResponse:
    """A tiny page that reports the result to the opener (popup) and closes; if it wasn't a
    popup, it just shows the message."""
    payload = json.dumps({"source": "gtasks", "ok": ok, "msg": msg})
    note = ("Google Tasks connected — you can close this window."
            if ok else "Couldn't connect Google Tasks: " + msg)
    body = ("<!doctype html><meta charset='utf-8'>"
            "<body style=\"font:16px system-ui,sans-serif;padding:2rem;color:#222\">"
            "<script>try{if(window.opener){window.opener.postMessage(" + payload + ",'*');}}"
            "catch(e){}if(window.opener){setTimeout(function(){window.close();},300);}</script>"
            + note + "</body>")
    return HTMLResponse(body)


@router.get("/api/gtasks/callback")
def gtasks_callback(code: str = Query(default=""), state: str = Query(default=""),
                    error: str = Query(default=""),
                    owner_id: int = Depends(deps.owner_id)) -> HTMLResponse:
    """Google redirects the user's browser here after consent (through the gateway, so the
    request carries the verified identity). Verify state, store the token for this owner."""
    if error or not code or not state:
        return _close(False, error or "missing code/state")
    con = db.connect()
    try:
        state_owner = db.gtasks_state_pop(con, state)
    finally:
        con.close()
    # The nonce must exist AND belong to the same authenticated user completing the flow.
    if state_owner is None or state_owner != owner_id:
        return _close(False, "state mismatch — please try connecting again")
    try:
        tok = gtasks.exchange_code(code)
    except gtasks.GTasksError as exc:
        return _close(False, str(exc))
    refresh = tok.get("refresh_token")
    if not refresh:
        return _close(False, "Google returned no refresh token — revoke the app at "
                             "myaccount.google.com/permissions and retry")
    email = gtasks.email_from_id_token(tok.get("id_token", ""))
    con = db.connect()
    try:
        db.gtasks_set(con, owner_id, refresh, email, config.GTASKS_LIST_TITLE)
    finally:
        con.close()
    return _close(True, email or "connected")


@router.post("/api/gtasks/disconnect")
def gtasks_disconnect(owner_id: int = Depends(deps.owner_id)) -> dict:
    con = db.connect()
    try:
        db.gtasks_delete(con, owner_id)
    finally:
        con.close()
    return {"connected": False}
