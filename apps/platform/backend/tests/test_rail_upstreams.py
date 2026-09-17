"""A rail slot may be pointed at ANOTHER broker, and the gateway has to make that safe to drive.

Why the payload changed shape: the Rails tab used to fetch one flat list of installed models,
because there was only ever one box to install them on. Once a role can carry
``offsite::mistral-small3*:24b`` that list is wrong for any delegated slot — it offers this
card's inventory for a dropdown whose choice will run somewhere else. So ``models`` became a
dict keyed by upstream, and the frontend indexes it by the slot's own upstream.

Why the failure handling is tested as hard as the happy path: a remote broker is, by
construction, a box this one does not control. It will be off, or on a laptop that left the
house. Treating that as an error would mean one dark remote blanks the entire Rails tab —
including every purely local slot — which is a worse outcome than the delegation being
unavailable. Hence: empty list for the box that is down, everything else still renders.

Why the gateway re-validates the upstream name the broker already validates: an unregistered
name saved to roles.json does NOT fail loudly. The broker's delegate_ref() falls back to local
on an unknown name, so the call would quietly run on this card while the panel reported the slot
as off-site. The 400 here is what keeps the panel's claim true.

No live broker: the client is faked, so these run anywhere.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from platform_core import BrokerError
from platform_gateway_app import main as gw
from platform_gateway_app.config import GatewaySettings

# recipe-book is in the default enabled set and is the only rail carrying all three slot kinds:
# chat (@recipe), vision (@recipe-vision) and image (@recipe-icon).
CHAT_ROLE = "recipe"
IMAGE_ROLE = "recipe-icon"

LOCAL_MODELS = [
    {"name": "gemma4:12b", "class": "chat", "parameter_size": "12B", "vision": True},
    {"name": "nomic-embed-text:latest", "class": "embed"},
]
OFFSITE_MODELS = [
    {"name": "mistral-small3.2:24b", "class": "chat", "parameter_size": "24B", "vision": False},
]


class _FakeBroker:
    """Stands in for BrokerClient. Records what was saved and how the reads overlapped."""

    def __init__(self, *, upstreams=None, models=None, roles=None,
                 fail_models=(), fail_upstreams=False):
        self._upstreams = upstreams if upstreams is not None else [
            {"name": "local", "url": None, "reachable": True},
            {"name": "offsite", "url": "http://big-box:11500", "reachable": True,
             "authorized": True},
        ]
        self._models = models if models is not None else {
            "local": LOCAL_MODELS, "offsite": OFFSITE_MODELS,
        }
        self._roles = roles if roles is not None else [
            {"role": CHAT_ROLE, "pattern": "offsite::mistral-small3*:24b", "upstream": "offsite",
             "resolved": "mistral-small3.2:24b", "installed": True, "class": "chat"},
            {"role": "recipe-vision", "pattern": "gemma4*:12b", "upstream": "local",
             "resolved": "gemma4:12b", "installed": True, "class": "chat"},
            {"role": IMAGE_ROLE, "pattern": "flux-schnell", "upstream": "local",
             "resolved": "flux-schnell", "installed": False, "class": None},
        ]
        self._fail_models = set(fail_models)
        self._fail_upstreams = fail_upstreams
        self.saved: list[tuple[str, str]] = []
        self._inflight = 0
        self.max_inflight = 0

    async def roles(self):
        return {"roles": self._roles}

    async def upstreams(self):
        if self._fail_upstreams:
            raise BrokerError("broker GET /v1/upstreams -> 404: Not Found")
        return {"upstreams": self._upstreams}

    async def models(self, upstream=None):
        name = upstream or "local"
        self._inflight += 1
        self.max_inflight = max(self.max_inflight, self._inflight)
        try:
            await asyncio.sleep(0.01)
            if name in self._fail_models:
                raise BrokerError(f"broker GET /v1/models -> 502: unknown upstream {name!r}")
            return {"upstream": name, "models": self._models.get(name, [])}
        finally:
            self._inflight -= 1

    async def disabled(self):
        return []

    async def set_role(self, role, model):
        self.saved.append((role, model))
        return {"role": role, "model": model}


@pytest.fixture()
def broker(monkeypatch):
    """Install a fake broker + real default settings onto the app, with no lifespan run."""
    monkeypatch.delenv("PLATFORM_ENABLED_APPS", raising=False)
    fake = _FakeBroker()
    monkeypatch.setattr(gw.app.state, "broker", fake, raising=False)
    monkeypatch.setattr(gw.app.state, "settings", GatewaySettings(), raising=False)
    return fake


def _payload(disabled=frozenset()):
    return asyncio.run(gw._rails_payload(set(disabled)))


def _slot(payload, rail_id, role):
    for rail in payload["rails"]:
        if rail["id"] == rail_id:
            for s in rail["slots"]:
                if s["role"] == role:
                    return s
    raise AssertionError(f"no slot {role!r} on rail {rail_id!r}")


def _put(role, model, upstream="local"):
    body = gw.RailModelBody(model=model, upstream=upstream)
    return asyncio.run(gw.admin_set_rail_model(role, body, admin=None))


# --- payload shape ----------------------------------------------------------


def test_models_is_keyed_by_upstream_and_upstreams_are_reported(broker):
    payload = _payload()
    assert isinstance(payload["models"], dict)
    assert set(payload["models"]) == {"local", "offsite"}
    assert [u["name"] for u in payload["upstreams"]] == ["local", "offsite"]
    # media stays a flat list — image backends are this box's media worker, not a per-box thing.
    assert payload["media"] and isinstance(payload["media"], list)


def test_each_upstream_offers_its_own_inventory(broker):
    """The point of the whole change: the offsite box's 24b model must NOT appear under local,
    and this card's 12b must not appear as a choice for a slot that will run off-site."""
    payload = _payload()
    assert [m["name"] for m in payload["models"]["local"]] == ["gemma4:12b"]
    assert [m["name"] for m in payload["models"]["offsite"]] == ["mistral-small3.2:24b"]


def test_disabled_models_are_filtered_from_every_upstream(broker):
    """The disabled set is a platform-wide policy, so it cannot apply to local only."""
    payload = _payload(disabled={"mistral-small3.2:24b"})
    assert payload["models"]["offsite"] == []
    assert [m["name"] for m in payload["models"]["local"]] == ["gemma4:12b"]


def test_upstreams_are_read_concurrently(broker):
    """Sequential reads would make the tab cost the SUM of every remote's latency, and a remote
    is exactly the thing that is slow. Two overlapping reads prove the gather is real."""
    _payload()
    assert broker.max_inflight == 2


# --- a slot knows where it runs ---------------------------------------------


def test_a_slot_carries_its_upstream(broker):
    payload = _payload()
    assert _slot(payload, "recipe-book", CHAT_ROLE)["upstream"] == "offsite"
    assert _slot(payload, "recipe-book", "recipe-vision")["upstream"] == "local"


def test_a_slot_defaults_to_local_when_the_broker_reports_no_upstream(broker, monkeypatch):
    """An older broker predates the field entirely. The panel must still render every slot as
    local rather than showing them all as unplaced."""
    monkeypatch.setattr(broker, "_roles", [
        {"role": CHAT_ROLE, "pattern": "gemma4*:12b", "resolved": "gemma4:12b",
         "installed": True, "class": "chat"},
    ])
    assert _slot(_payload(), "recipe-book", CHAT_ROLE)["upstream"] == "local"


# --- a dark remote degrades, it does not fail -------------------------------


def test_an_unreachable_upstream_yields_an_empty_list_not_a_failure(monkeypatch):
    monkeypatch.delenv("PLATFORM_ENABLED_APPS", raising=False)
    fake = _FakeBroker(upstreams=[
        {"name": "local", "url": None, "reachable": True},
        {"name": "offsite", "url": "http://big-box:11500", "reachable": False,
         "authorized": False},
    ])
    monkeypatch.setattr(gw.app.state, "broker", fake, raising=False)
    monkeypatch.setattr(gw.app.state, "settings", GatewaySettings(), raising=False)
    payload = _payload()
    assert payload["models"]["offsite"] == []
    # the whole point: local still renders its choices
    assert [m["name"] for m in payload["models"]["local"]] == ["gemma4:12b"]
    assert fake.max_inflight == 1, "a box reported down should not be dialled at all"


def test_an_unauthorized_upstream_yields_an_empty_list(monkeypatch):
    """Reachable but rejecting our token. Distinguishable from down, same degradation."""
    monkeypatch.delenv("PLATFORM_ENABLED_APPS", raising=False)
    fake = _FakeBroker(upstreams=[
        {"name": "local", "url": None, "reachable": True},
        {"name": "offsite", "url": "http://big-box:11500", "reachable": True,
         "authorized": False},
    ])
    monkeypatch.setattr(gw.app.state, "broker", fake, raising=False)
    monkeypatch.setattr(gw.app.state, "settings", GatewaySettings(), raising=False)
    payload = _payload()
    assert payload["models"]["offsite"] == []
    assert payload["models"]["local"]


def test_a_remote_that_dies_mid_read_still_leaves_the_others_rendering(monkeypatch):
    """Reported healthy, then 502s on the actual read — the race the reachability probe cannot
    close. It must be caught at the per-upstream boundary, not bubble out of the gather."""
    monkeypatch.delenv("PLATFORM_ENABLED_APPS", raising=False)
    fake = _FakeBroker(fail_models={"offsite"})
    monkeypatch.setattr(gw.app.state, "broker", fake, raising=False)
    monkeypatch.setattr(gw.app.state, "settings", GatewaySettings(), raising=False)
    payload = _payload()
    assert payload["models"]["offsite"] == []
    assert [m["name"] for m in payload["models"]["local"]] == ["gemma4:12b"]
    assert payload["rails"], "the rails themselves must still be listed"


def test_a_broker_too_old_to_know_upstreams_reports_local_only(monkeypatch):
    monkeypatch.delenv("PLATFORM_ENABLED_APPS", raising=False)
    fake = _FakeBroker(fail_upstreams=True)
    monkeypatch.setattr(gw.app.state, "broker", fake, raising=False)
    monkeypatch.setattr(gw.app.state, "settings", GatewaySettings(), raising=False)
    payload = _payload()
    assert [u["name"] for u in payload["upstreams"]] == ["local"]
    assert set(payload["models"]) == {"local"}


def test_a_dead_broker_is_still_a_hard_error(monkeypatch):
    """Degradation is per-UPSTREAM. If the broker itself cannot be read the tab has nothing to
    show, and the route's existing 502 must survive rather than rendering an empty panel."""
    monkeypatch.delenv("PLATFORM_ENABLED_APPS", raising=False)
    fake = _FakeBroker()

    async def _dead():
        raise BrokerError("broker GET /v1/roles unreachable")

    monkeypatch.setattr(fake, "roles", _dead)
    monkeypatch.setattr(gw.app.state, "broker", fake, raising=False)
    monkeypatch.setattr(gw.app.state, "settings", GatewaySettings(), raising=False)
    with pytest.raises(BrokerError):
        _payload()


# --- PUT: composing the delegated value -------------------------------------


def test_put_composes_the_upstream_prefix(broker):
    _put(CHAT_ROLE, "mistral-small3*:24b", upstream="offsite")
    assert broker.saved == [(CHAT_ROLE, "offsite::mistral-small3*:24b")]


def test_put_leaves_a_local_choice_unprefixed(broker):
    """A double colon on a local value would make the broker look for an upstream named after
    the model — so local must stay byte-for-byte what it always was."""
    _put(CHAT_ROLE, "gemma4*:12b", upstream="local")
    assert broker.saved == [(CHAT_ROLE, "gemma4*:12b")]


def test_put_defaults_to_local_when_no_upstream_is_sent(broker):
    """The frontend that predates this field still posts {"model": ...} alone."""
    asyncio.run(gw.admin_set_rail_model(CHAT_ROLE, gw.RailModelBody(model="gemma4*:12b"),
                                        admin=None))
    assert broker.saved == [(CHAT_ROLE, "gemma4*:12b")]


def test_put_rejects_an_unknown_upstream(broker):
    with pytest.raises(HTTPException) as exc:
        _put(CHAT_ROLE, "mistral-small3*:24b", upstream="nowhere")
    assert exc.value.status_code == 400
    assert "nowhere" in str(exc.value.detail)
    assert broker.saved == [], "an unregistered name must never reach roles.json"


def test_put_rejects_an_upstream_an_older_broker_cannot_confirm(monkeypatch):
    """No /v1/upstreams means nothing but local is registered, so a delegation cannot be honoured
    and must be refused rather than written as a value that resolves locally."""
    monkeypatch.delenv("PLATFORM_ENABLED_APPS", raising=False)
    fake = _FakeBroker(fail_upstreams=True)
    monkeypatch.setattr(gw.app.state, "broker", fake, raising=False)
    monkeypatch.setattr(gw.app.state, "settings", GatewaySettings(), raising=False)
    with pytest.raises(HTTPException) as exc:
        _put(CHAT_ROLE, "mistral-small3*:24b", upstream="offsite")
    assert exc.value.status_code == 400
    assert fake.saved == []


# --- PUT: the existing guards still hold ------------------------------------


def test_an_image_slot_cannot_be_sent_off_site(broker):
    """A media backend is loaded by THIS box's media worker; the remote broker has no concept of
    'flux-schnell', so the delegation would only fail later, at generation time."""
    with pytest.raises(HTTPException) as exc:
        _put(IMAGE_ROLE, "flux-schnell", upstream="offsite")
    assert exc.value.status_code == 400
    assert broker.saved == []


def test_an_image_slot_still_rejects_a_non_backend_model(broker):
    with pytest.raises(HTTPException) as exc:
        _put(IMAGE_ROLE, "gemma4*:12b")
    assert exc.value.status_code == 400
    assert "image backend" in str(exc.value.detail)
    assert broker.saved == []


def test_an_image_slot_still_accepts_a_local_backend(broker):
    _put(IMAGE_ROLE, "sdxl-turbo")
    assert broker.saved == [(IMAGE_ROLE, "sdxl-turbo")]


def test_a_role_outside_the_rail_slots_is_still_a_404(broker):
    with pytest.raises(HTTPException) as exc:
        _put("chat", "gemma4*:12b", upstream="offsite")
    assert exc.value.status_code == 404
    assert broker.saved == []


def test_an_empty_model_is_still_a_400(broker):
    with pytest.raises(HTTPException) as exc:
        _put(CHAT_ROLE, "   ", upstream="offsite")
    assert exc.value.status_code == 400
    assert broker.saved == []


def test_put_returns_the_refreshed_payload_in_the_new_shape(broker):
    """Apply re-renders from this return value, so it must carry the same keyed shape as the GET
    or the picker would come back empty right after a successful change."""
    out = _put(CHAT_ROLE, "mistral-small3*:24b", upstream="offsite")
    assert isinstance(out["models"], dict)
    assert set(out["models"]) == {"local", "offsite"}
    assert [u["name"] for u in out["upstreams"]] == ["local", "offsite"]
