"""What a non-admin receives from GET /api/platform/status, beyond the cli block.

The broker's /v1/status names the absolute path of every media worker interpreter, the Ollama
version, model expiry and job start times, and whatever it grows next. The route serves every
logged-in user, so all of that used to reach every browser. Like test_cli_view.py, the
endpoint tests serialise the WHOLE body and search it for markers, rather than checking that
one key went missing: a leak through any key, present or future, fails.

The other direction matters as much. A filter that starves the widget looks like a privacy fix
and breaks the GPU meter for every plain user, so the exact non-admin shape is pinned, and a
top-level field ModelWidget.tsx reads but this filter does not admit fails a test.

Run tests/mutate_status_view.py after changing status_view.py or the status route.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from platform_core import BrokerError
from platform_gateway_app import main as m
from platform_gateway_app import status_view as sv
from platform_gateway_app.auth import create_session, hash_password
from platform_gateway_app.models import User

OWNER, NOBODY = "owner-one", "bob"

MEDIA_PY = "D:/venvs/media-marker/Scripts/python.exe"
TTS_PY = "D:/venvs/tts-marker/Scripts/python.exe"
KOKORO_PY = "D:/venvs/kokoro-marker/Scripts/python.exe"
WHISPER_PY = "D:/venvs/whisper-marker/Scripts/python.exe"
OLLAMA_VERSION = "0.12.3-version-marker"
EXPIRES = "2026-10-07T19:00:00-expiry-marker"
JOB_SINCE = (1759860000.125, 1759860001.375)
BROKER_DOWN = "broker GET /v1/status unreachable: host-marker.internal:11500"

FALLBACK = {"model": "qwen2.5:3b", "requested": "@vision", "original": "llava:13b",
            "reason": "disabled", "category": "vision"}

# The broker's real /v1/status shape (services/broker/app/broker.py, Broker.status), keys in
# its order. `cli` sits BEFORE `media` there, which is what lets the order test below tell
# "kept its place" from "moved to the end".
BROKER_STATUS = {
    "ollama_reachable": True,
    "ollama_version": OLLAMA_VERSION,
    "loaded": [{"name": "mistral-small3.2:24b", "class": "heavy", "size_vram": 17_000_000_000,
                "expires_at": EXPIRES}],
    "heavy_loaded": ["mistral-small3.2:24b"],
    "gpu": {"total_mib": 24564, "used_mib": 17800, "free_mib": 6764,
            "gpu_name": "NVIDIA GeForce RTX 4090"},
    "queue": {"active": 1, "waiting": 1},
    "fallbacks": {"count": 2, "last": dict(FALLBACK)},
    "jobs": [
        {"seq": 7, "model": "mistral-small3.2:24b", "source": "iep-goals", "state": "active",
         "since": JOB_SINCE[0], "fallback": None},
        {"seq": 8, "model": "qwen2.5:3b", "source": None, "state": "waiting",
         "since": JOB_SINCE[1], "fallback": dict(FALLBACK)},
    ],
    "cli": {"backend": "claude", "ready": True, "owners": [OWNER], "roles": ["@iep"],
            "active": [], "last_by_role": {}},
    "media": {
        "enabled": True, "active": {"op": "tts", "model": "xtts"},
        "image_python": MEDIA_PY, "image_python_exists": True,
        "speech_python": TTS_PY, "speech_python_exists": True, "speech_isolated": True,
        "kokoro_python": KOKORO_PY, "kokoro_python_exists": True,
        "kokoro_model_exists": True, "kokoro_voices_exist": True,
        "whisper_python": WHISPER_PY, "whisper_python_exists": True,
    },
}

# Exactly what web/src/ModelWidget.tsx draws for a user who is neither admin nor owner.
WIDGET_VIEW = {
    "ollama_reachable": True,
    "loaded": [{"name": "mistral-small3.2:24b", "class": "heavy", "size_vram": 17_000_000_000}],
    "heavy_loaded": ["mistral-small3.2:24b"],
    "gpu": {"total_mib": 24564, "used_mib": 17800, "free_mib": 6764,
            "gpu_name": "NVIDIA GeForce RTX 4090"},
    "queue": {"active": 1, "waiting": 1},
    "fallbacks": {"count": 2, "last": dict(FALLBACK)},
    "jobs": [
        {"seq": 7, "model": "mistral-small3.2:24b", "source": "iep-goals", "state": "active",
         "fallback": None},
        {"seq": 8, "model": "qwen2.5:3b", "source": None, "state": "waiting",
         "fallback": dict(FALLBACK)},
    ],
}

# Strings that are an admin's to see and nobody else's.
OPERATOR_ONLY = [MEDIA_PY, TTS_PY, KOKORO_PY, WHISPER_PY, OLLAMA_VERSION, EXPIRES,
                 *(repr(s) for s in JOB_SINCE), '"media"', '"ollama_version"', '"expires_at"',
                 "_python", "Scripts"]


def _dump(obj) -> str:
    return json.dumps(obj, sort_keys=True)


# --- status_view, the pure rule ---------------------------------------------------------------


def test_a_non_admin_gets_exactly_what_the_widget_draws():
    assert sv.status_view(BROKER_STATUS, NOBODY, False) == WIDGET_VIEW


def test_an_owner_gets_the_same_plus_their_cli_view():
    view = sv.status_view(BROKER_STATUS, OWNER, False)
    assert {k: v for k, v in view.items() if k != "cli"} == WIDGET_VIEW
    assert view["cli"]["for_you"] is True
    assert list(view) == [k for k in BROKER_STATUS if k in view]


@pytest.mark.parametrize("who,admin", [(OWNER, False), ("admin-one", True)])
def test_cli_keeps_its_place_in_the_brokers_order(who, admin):
    """In the real status cli is followed only by `media`, which a non-admin never gets, so
    there it would be last either way. Here it sits mid-way, so a view that appended it at the
    end would fail."""
    st = {"ollama_reachable": True, "cli": copy.deepcopy(BROKER_STATUS["cli"]), "jobs": []}
    assert list(sv.status_view(st, who, admin)) == ["ollama_reachable", "cli", "jobs"]


def test_an_admin_gets_the_whole_status_as_a_copy():
    view = sv.status_view(BROKER_STATUS, "admin-one", True)
    assert view == BROKER_STATUS
    assert list(view) == list(BROKER_STATUS)
    view["media"]["image_python"] = "changed"
    view["jobs"].append({"seq": 99})
    assert BROKER_STATUS["media"]["image_python"] == MEDIA_PY
    assert len(BROKER_STATUS["jobs"]) == 2


@pytest.mark.parametrize("who", [NOBODY, OWNER])
def test_no_operator_field_reaches_a_non_admin(who):
    text = _dump(sv.status_view(BROKER_STATUS, who, False))
    leaked = [s for s in OPERATOR_ONLY if s in text]
    assert leaked == [], f"{who!r} was shown {leaked}"


def test_a_field_the_broker_adds_reaches_admins_only():
    """The allowlist's reason to exist: the broker is still growing this status, and a new field
    must be admitted on purpose, at every level, not reach every browser the day it ships."""
    st = copy.deepcopy(BROKER_STATUS)
    st["upstreams"] = [{"name": "offsite", "url": "http://new-marker:11500"}]
    st["loaded"][0]["digest_path"] = "D:/new-marker/blobs"
    st["gpu"]["driver_path"] = "C:/new-marker/nvidia-smi.exe"
    st["queue"]["by_rail"] = "new-marker"
    st["jobs"][0]["requester"] = "new-marker-user"
    st["jobs"][1]["fallback"]["detail"] = "new-marker detail"
    st["fallbacks"]["history"] = "new-marker"
    st["fallbacks"]["last"]["detail"] = "new-marker detail"

    assert "new-marker" not in _dump(sv.status_view(st, NOBODY, False))
    assert sv.status_view(st, NOBODY, False) == WIDGET_VIEW
    assert _dump(sv.status_view(st, "admin-one", True)).count("new-marker") == 8


def test_an_allowed_name_cannot_carry_a_nested_object():
    """Leaves are admitted as scalars only, so an allowlisted KEY is not a hole a future broker
    could push a whole object through."""
    st = copy.deepcopy(BROKER_STATUS)
    st["loaded"][0]["name"] = {"path": MEDIA_PY}
    st["gpu"]["gpu_name"] = [MEDIA_PY]
    st["jobs"][0]["model"] = {"python": MEDIA_PY}
    st["fallbacks"]["count"] = {"python": MEDIA_PY}
    st["fallbacks"]["last"]["reason"] = [MEDIA_PY]
    st["queue"]["active"] = {"python": MEDIA_PY}
    view = sv.status_view(st, NOBODY, False)
    assert MEDIA_PY not in _dump(view)
    assert "name" not in view["loaded"][0]
    assert "gpu_name" not in view["gpu"]
    assert "model" not in view["jobs"][0]
    assert "count" not in view["fallbacks"]
    assert "reason" not in view["fallbacks"]["last"]
    assert "active" not in view["queue"]


@pytest.mark.parametrize("flag", [1, "yes", "true", [True], None])
def test_only_a_literal_true_is_admin(flag):
    """Fails closed: something merely truthy gets the widget view, not the operator one."""
    assert sv.status_view(BROKER_STATUS, NOBODY, flag) == WIDGET_VIEW


@pytest.mark.parametrize("status,expected", [
    (None, {}),
    ("ok", {}),
    ([BROKER_STATUS], {}),
    ({}, {}),
    ({"ollama_reachable": "yes", "loaded": "x", "heavy_loaded": [1, "a", None], "gpu": [1],
      "queue": None, "fallbacks": "x", "jobs": [None, 1, "x", {"seq": 1, "fallback": "x"}]},
     {"ollama_reachable": False, "loaded": [], "heavy_loaded": ["a"], "gpu": None,
      "queue": {}, "fallbacks": {}, "jobs": [{"seq": 1, "fallback": None}]}),
    ({"loaded": [None, {"name": "m", "class": "embed"}], "fallbacks": {"count": 1, "last": "x"},
      "gpu": None},
     {"loaded": [{"name": "m", "class": "embed"}], "fallbacks": {"count": 1, "last": None},
      "gpu": None}),
])
def test_malformed_status_never_raises(status, expected):
    """A half-upgraded broker degrades to a smaller answer, never to a 500 that would take the
    GPU meter down for everyone."""
    assert sv.status_view(status, NOBODY, False) == expected


def test_every_status_field_the_widget_reads_is_admitted():
    """The starve-the-widget direction. If ModelWidget.tsx starts reading `status.something`
    and nobody admits it here, every plain user silently gets `undefined` for it while admins
    (who get everything) see the feature work. `broker_reachable` is the gateway's own key."""
    widget = Path(__file__).resolve().parents[4] / "web" / "src" / "ModelWidget.tsx"
    reads = set(re.findall(r"\bstatus\.(\w+)", widget.read_text(encoding="utf-8")))
    # A regex that matched nothing would pass the subset check below vacuously.
    assert {"loaded", "jobs", "gpu", "queue"} <= reads, f"ModelWidget.tsx parse found {reads}"
    admitted = set(sv._SHOWN) | {"cli", "broker_reachable"}
    assert reads <= admitted, f"the widget reads {sorted(reads - admitted)}, never sent to non-admins"


# --- the route, over HTTP, with a real session cookie ------------------------------------------


class _Broker:
    def __init__(self, status=None, fail: Exception | None = None):
        self._status, self.fail = status, fail

    async def status(self):
        if self.fail:
            raise self.fail
        return copy.deepcopy(self._status)


@pytest.fixture()
def gateway(db, monkeypatch):
    """The real app and its real auth dependency, minus the lifespan. Returns login(name)."""
    monkeypatch.setattr(m.app.state, "db", db, raising=False)
    monkeypatch.setattr(m.app.state, "settings",
                        SimpleNamespace(session_cookie="platform_session",
                                        root_asset_owner=lambda _path: None),
                        raising=False)
    monkeypatch.setattr(m.app.state, "broker", _Broker(BROKER_STATUS), raising=False)

    def login(name: str, admin: bool = False, superadmin: bool = False) -> TestClient:
        with db.session_ctx() as s:
            u = User(username=name, password_hash=hash_password("x"),
                     is_admin=admin or superadmin, is_superadmin=superadmin)
            s.add(u)
            s.commit()
            token = create_session(s, u, ttl_hours=1).token
        client = TestClient(m.app)
        client.cookies.set("platform_session", token)
        return client

    return login


def _get(client: TestClient) -> tuple[dict, str]:
    r = client.get("/api/platform/status")
    assert r.status_code == 200, r.text
    return r.json(), r.text


@pytest.mark.parametrize("who", [NOBODY, OWNER])
def test_no_machine_path_reaches_a_non_admin_browser(gateway, who):
    """The leak this module exists for, checked on the bytes the browser gets."""
    body, text = _get(gateway(who))
    leaked = [s for s in OPERATOR_ONLY if s in text]
    assert leaked == [], f"{who!r}'s status carried {leaked}"
    assert {k: v for k, v in body.items() if k != "cli"} == {"broker_reachable": True,
                                                             **WIDGET_VIEW}


def test_an_admin_browser_gets_the_brokers_status_unchanged(gateway):
    """The other half: filtering must not starve the people who operate the broker."""
    body, text = _get(gateway("admin-one", admin=True))
    assert body == {"broker_reachable": True, **BROKER_STATUS}
    assert MEDIA_PY in text and OLLAMA_VERSION in text


def test_a_superadmin_counts_as_an_admin(gateway):
    body, _ = _get(gateway("root-one", superadmin=True))
    assert body["media"]["whisper_python"] == WHISPER_PY


def test_the_broker_down_detail_reaches_admins_only(gateway, monkeypatch):
    """The broker client's error text is an operator's clue. A plain user still gets every key
    the widget needs to draw "Broker offline" and an empty meter."""
    monkeypatch.setattr(m.app.state, "broker", _Broker(fail=BrokerError(BROKER_DOWN)),
                        raising=False)
    body, text = _get(gateway(NOBODY))
    assert "host-marker" not in text
    assert body == {"broker_reachable": False, **m._EMPTY_STATUS}
    body, _ = _get(gateway("admin-one", admin=True))
    assert body == {"broker_reachable": False, "detail": BROKER_DOWN, **m._EMPTY_STATUS}
