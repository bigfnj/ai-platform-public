"""Control-plane token dependency (audit E1): open when unset, else Bearer/X-Broker-Token required."""
import hashlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.main import require_token


def _req(path: str, headers: dict, token: str, named: list | None = None,
         method: str = "GET", seen: dict | None = None):
    """A request stub. `named` is the tokens.json contents; `seen` is the in-memory last-seen
    map, exposed so a test can assert the stamp is written rather than trusting it."""
    return SimpleNamespace(
        url=SimpleNamespace(path=path),
        headers=headers,
        method=method,
        app=SimpleNamespace(state=SimpleNamespace(
            settings=SimpleNamespace(
                auth_token=token, tokens=lambda: list(named or []),
                # The STORE existing is what turns enforcement on; `named=None` means no store
                # at all (open), `named=[]` means a store that is empty (nobody authorised).
                tokens_configured=lambda: named is not None),
            token_seen=seen if seen is not None else {})),
    )


def _named(label: str, scope: str, plain: str) -> dict:
    return {"id": label, "label": label, "scope": scope, "prefix": plain[:11],
            "hash": hashlib.sha256(plain.encode("utf-8")).hexdigest(), "created": "now"}


def test_open_when_token_unset():
    require_token(_req("/v1/status", {}, ""))  # no token configured => allowed


def test_healthz_always_open():
    require_token(_req("/healthz", {}, "secret"))  # liveness exempt even when enforcing


def test_missing_token_401():
    with pytest.raises(HTTPException) as e:
        require_token(_req("/v1/status", {}, "secret"))
    assert e.value.status_code == 401


def test_wrong_token_401():
    with pytest.raises(HTTPException):
        require_token(_req("/v1/chat", {"authorization": "Bearer nope"}, "secret"))


def test_valid_bearer_ok():
    require_token(_req("/v1/status", {"authorization": "Bearer secret"}, "secret"))


def test_valid_bearer_case_insensitive_scheme():
    require_token(_req("/v1/status", {"authorization": "bearer secret"}, "secret"))


def test_valid_x_broker_token_ok():
    require_token(_req("/v1/embed", {"x-broker-token": "secret"}, "secret"))


# --- named, revocable, scoped tokens ----------------------------------------------------------
# The shared secret is one credential for every host: to stop trusting one machine you rotate it
# and redeploy every rail. A named token is per-host and revocable on its own.

def test_a_named_token_is_accepted_when_no_shared_secret_exists():
    t = "bt_studio"
    require_token(_req("/v1/status", {"authorization": f"Bearer {t}"}, "",
                       named=[_named("studio", "inference", t)]))


def test_a_named_token_is_accepted_alongside_the_shared_secret():
    """Both must work at once. Every rail sends the shared secret and the gateway needs it to
    reach /v1/tokens at all, so named tokens are additive, never a replacement."""
    t = "bt_studio"
    rows = [_named("studio", "inference", t)]
    require_token(_req("/v1/status", {"authorization": "Bearer shared"}, "shared", named=rows))
    require_token(_req("/v1/status", {"authorization": f"Bearer {t}"}, "shared", named=rows))


def test_a_revoked_token_is_refused():
    """Revocation is just absence from the hot-read list, which is why it needs no restart."""
    t = "bt_gone"
    with pytest.raises(HTTPException) as e:
        require_token(_req("/v1/status", {"authorization": f"Bearer {t}"}, "", named=[]))
    assert e.value.status_code == 401


def test_a_token_that_is_a_prefix_of_a_live_one_is_refused():
    t = "bt_studio_desktop"
    with pytest.raises(HTTPException):
        require_token(_req("/v1/status", {"authorization": "Bearer bt_studio"}, "",
                           named=[_named("studio", "full", t)]))


def test_inference_scope_cannot_repoint_a_role():
    """The reason scopes exist. PUT /v1/roles changes the model every user of that rail gets."""
    t = "bt_remote"
    with pytest.raises(HTTPException) as e:
        require_token(_req("/v1/roles/chat", {"authorization": f"Bearer {t}"}, "",
                           named=[_named("remote", "inference", t)], method="PUT"))
    assert e.value.status_code == 403
    assert "inference" in e.value.detail


def test_inference_scope_cannot_unload_a_model():
    """/v1/unload can evict the model somebody is mid-conversation with."""
    t = "bt_remote"
    with pytest.raises(HTTPException) as e:
        require_token(_req("/v1/unload", {"authorization": f"Bearer {t}"}, "",
                           named=[_named("remote", "inference", t)], method="POST"))
    assert e.value.status_code == 403


def test_inference_scope_cannot_read_or_mint_tokens():
    """A token that can mint tokens is a token that can escalate itself to full."""
    t = "bt_remote"
    rows = [_named("remote", "inference", t)]
    for method in ("GET", "POST"):
        with pytest.raises(HTTPException) as e:
            require_token(_req("/v1/tokens", {"authorization": f"Bearer {t}"}, "",
                               named=rows, method=method))
        assert e.value.status_code == 403


def test_inference_scope_CAN_do_the_thing_it_is_for():
    """The other direction, and the one that would make the feature useless if wrong: a remote
    workstation must be able to run inference and read state."""
    t = "bt_remote"
    rows = [_named("remote", "inference", t)]
    h = {"authorization": f"Bearer {t}"}
    require_token(_req("/v1/chat", h, "", named=rows, method="POST"))
    require_token(_req("/v1/chat/stream", h, "", named=rows, method="POST"))
    require_token(_req("/v1/embed", h, "", named=rows, method="POST"))
    require_token(_req("/openai/v1/chat/completions", h, "", named=rows, method="POST"))
    require_token(_req("/v1/status", h, "", named=rows))
    require_token(_req("/v1/roles", h, "", named=rows))          # GET is read-only
    require_token(_req("/v1/models", h, "", named=rows))


def test_full_scope_may_do_everything():
    t = "bt_admin"
    rows = [_named("admin", "full", t)]
    h = {"authorization": f"Bearer {t}"}
    require_token(_req("/v1/roles/chat", h, "", named=rows, method="PUT"))
    require_token(_req("/v1/unload", h, "", named=rows, method="POST"))
    require_token(_req("/v1/tokens", h, "", named=rows, method="POST"))


def test_the_shared_secret_is_full_scope():
    """It has to be: the gateway holds it and manages named tokens with it."""
    require_token(_req("/v1/roles/chat", {"authorization": "Bearer shared"}, "shared",
                       method="PUT"))


def test_a_named_token_stamps_last_seen():
    """Not decoration: it is how the revoke workflow answers 'is this workstation still using
    it'. A stamp that is never written would make the column a permanent 'never'."""
    t = "bt_studio"
    seen: dict = {}
    require_token(_req("/v1/status", {"authorization": f"Bearer {t}"}, "",
                       named=[_named("studio", "inference", t)], seen=seen))
    assert set(seen) == {"studio"} and seen["studio"] > 0


def test_named_tokens_alone_are_enough_to_close_the_broker():
    """With no shared secret set but a named token present, the broker must ENFORCE. Otherwise
    creating the first token would leave it wide open and look like it had been secured."""
    with pytest.raises(HTTPException) as e:
        require_token(_req("/v1/status", {}, "", named=[_named("x", "full", "bt_x")]))
    assert e.value.status_code == 401


def test_an_EMPTY_store_still_closes_the_broker():
    """The footgun this closes, and the reason enforcement keys on the store EXISTING rather
    than on it having rows: revoke the last named token on a box with no shared secret and an
    emptiness test would silently reopen the broker to anyone. "I revoked everything" must mean
    "nobody is authorised", not "authorisation is off"."""
    with pytest.raises(HTTPException) as e:
        require_token(_req("/v1/status", {}, "", named=[]))
    assert e.value.status_code == 401


def test_no_store_and_no_secret_is_still_open():
    """The other side of the same line: a box that has configured NOTHING stays open, which is
    the documented dev / staged-rollout path. `named=None` models the file not existing."""
    require_token(_req("/v1/status", {}, "", named=None))
