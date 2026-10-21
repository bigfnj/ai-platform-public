"""The weekly NVIDIA cloud-model check: ``POST /api/maintenance/nim-check``.

NVIDIA retired this rail's cloud default twice with the key still valid. The first went 16 days
unnoticed (until a user pressed the toggle), the second 9 (until someone ran the deep smoke by
hand). This route is the scheduled version of the toggle's probe: the platform scheduler fires
it weekly, and what it answers is what Admin > Schedule shows.

So the contract under test is the ANSWER: a working model is a 200 with the model and latency;
a retired one is a non-2xx whose detail is nim.ModelUnavailable's sentence (the scheduler keeps
the detail; see apps/platform/backend/tests/test_scheduler_nim_check.py); no key is a 200 skip,
because a deployment that chose local-only generation must not get a red row every week. It
spends money, so it is admin-only and shares the toggle probe's rate limit.

Hermetic: NVIDIA is a fake client or a stubbed probe. ``no_outbound`` (via the ``api`` fixture)
fails any test that would really leave the process.
Run tests/mutate_nim_unavailable.py after changing this route or its scheduler entry.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from ai_playground import nim
from ai_playground.api import app as appmod

ROUTE = "/api/maintenance/nim-check"
MODEL = "vendor/some-instruct-model"
ADMIN = {"X-Platform-User": "owner-one", "X-Platform-Admin": "1"}
USER = {"X-Platform-User": "bob"}
# What the platform scheduler sends (scheduler.SYSTEM_HEADERS).
SYSTEM = {"X-Platform-User": "system", "X-Platform-Admin": "1"}
# tests -> ai-playground -> rails -> the repo root.
SCHEDULER_TASKS = (Path(__file__).resolve().parents[3] / "apps" / "platform" / "backend"
                   / "platform_gateway_app" / "scheduler_tasks.py")

GONE = {"type": "about:blank", "title": "Gone", "status": 410,
        "detail": f"The model '{MODEL}' has reached its end of life on 2026-09-28T08:00:00Z "
                  "and is no longer available."}


def _gone() -> openai.APIStatusError:
    request = httpx.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions")
    return openai.APIStatusError("Error code: 410", body=GONE,
                                 response=httpx.Response(410, request=request, json=GONE))


class _Client:
    """Stands in for AsyncOpenAI. `exc` set: every completion raises it; else it answers."""

    def __init__(self, exc: Exception | None, calls: list) -> None:
        self.exc, self.calls = exc, calls
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def __aenter__(self) -> "_Client":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return SimpleNamespace(choices=[])


@pytest.fixture
def check(api, monkeypatch):
    """A client over the real app with NVIDIA faked at the SDK, so the real nim.probe() and its
    error translation run. Returns (client, install, calls): install(exc) sets what NVIDIA does.
    The process-wide probe limiter is reset either side, so ordering cannot leak allowance."""
    calls: list[dict] = []

    def install(exc: Exception | None = None, *, key: str = "test-key") -> None:
        monkeypatch.setattr(nim, "API_KEY", key)
        monkeypatch.setattr(nim, "CHAT_MODEL", MODEL)
        monkeypatch.setattr(nim, "AsyncOpenAI", lambda **kwargs: _Client(exc, calls))

    appmod._NIM_PROBE_LIMIT.reset()
    try:
        yield TestClient(api), install, calls
    finally:
        appmod._NIM_PROBE_LIMIT.reset()


def test_a_working_model_answers_ok_with_the_model_and_latency(check):
    client, install, calls = check
    install()
    resp = client.post(ROUTE, headers=SYSTEM)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["model"] == MODEL
    assert isinstance(body["latency_s"], (int, float)) and body["latency_s"] >= 0
    assert len(calls) == 1 and calls[0]["max_tokens"] == 1, "not the 1-token probe"


def test_a_retired_model_fails_the_run_with_the_plain_message(check):
    """The case this route exists for. A 2xx here would be a green row over a dead model."""
    client, install, _ = check
    install(_gone())
    resp = client.post(ROUTE, headers=SYSTEM)
    assert resp.status_code == 502, resp.text
    detail = resp.json()["detail"]
    assert detail.startswith(f"NVIDIA has retired the cloud model '{MODEL}'"), detail
    assert "AI_PLAYGROUND_NVIDIA_CHAT_MODEL" in detail


def test_no_answer_at_all_is_503_not_a_model_verdict(check):
    """A network failure says nothing about the model, so it must not read as one."""
    client, install, _ = check
    request = httpx.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions")
    install(openai.APIConnectionError(request=request))
    resp = client.post(ROUTE, headers=SYSTEM)
    assert resp.status_code == 503, resp.text
    assert "did not answer" in resp.json()["detail"]


def test_no_key_is_a_skip_and_spends_nothing(check, monkeypatch):
    """No key means this deployment chose local-only generation. A weekly red row there would
    teach the reader to ignore the row on the box where it matters."""
    client, _, _ = check
    monkeypatch.setattr(nim, "API_KEY", "")
    # no_outbound left AsyncOpenAI wired to fail the test if anything builds a client.
    resp = client.post(ROUTE, headers=SYSTEM)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True and "no NVIDIA key" in resp.json()["skipped"]


def test_admin_only_and_a_refusal_spends_nothing(check):
    client, install, calls = check
    install()
    assert client.post(ROUTE, headers=USER).status_code == 403
    assert client.post(ROUTE).status_code == 401
    assert calls == [], "a refused caller still reached NVIDIA"


def test_it_shares_the_toggle_probes_rate_limit(check, monkeypatch):
    """One budget for both doors. Charged before the call, so the refused one spends nothing,
    and the toggle's probe and this route draw on the same per-caller bucket."""
    client, install, calls = check
    install()
    monkeypatch.setattr(appmod._NIM_PROBE_LIMIT, "limit", 2)
    assert client.post("/api/nim/probe", headers=ADMIN).status_code == 200
    assert client.post(ROUTE, headers=ADMIN).status_code == 200
    resp = client.post(ROUTE, headers=ADMIN)
    assert resp.status_code == 429, resp.text
    assert len(calls) == 2, "the call past the limit still reached NVIDIA"


def test_the_platform_scheduler_fires_this_route(api):
    """The registry entry IS the schedule and the Schedule tab's Run now. Read from the
    gateway's file rather than imported: the repo venv's editable gateway points at one
    checkout, and a worktree must test its own copy."""
    if not SCHEDULER_TASKS.is_file():
        pytest.skip(f"{SCHEDULER_TASKS} is not in this tree (rail lifted out of the monorepo)")
    tree = ast.parse(SCHEDULER_TASKS.read_text(encoding="utf-8"))
    tasks = next(ast.literal_eval(n.value) for n in tree.body
                 if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "SCHEDULED_TASKS")
    mine = [t for t in tasks if t["rail"] == "ai-playground" and t["path"] == ROUTE]
    assert len(mine) == 1, [t for t in tasks if t["rail"] == "ai-playground"]
    task = mine[0]
    assert task["method"] == "POST"
    # Synchronous: a fire-and-forget task records "triggered" and never the rail's answer.
    assert not task.get("async")
    assert task["default"]["freq"] == "weekly"
    routes = {(m, r.path) for r in api.routes for m in getattr(r, "methods", ())}
    assert ("POST", ROUTE) in routes
