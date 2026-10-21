"""Eight recorded costs on the broker's hot and startup paths, now bounded.

1. audit_roles fetched the installed list once, then re-fetched it for every glob role through
   a per-role resolve of its own: about 21 identical GET /api/tags on each startup.
2. list_models probed /api/show one model at a time on a cold capability cache, so the first
   listing after a restart waited N serial round trips.
3. The UNGATED media jobs (read-aloud, dictation, image embedding) each spawn a 350-500 MB
   interpreter, and nothing bounded how many ran at once.
4. The roles views (/v1/roles, and its local-only twin) refetched the installed list for every
   role that fell back to a substitute, and read it before probing any remote rather than
   alongside the probes.
5. The Rails tab's one read (/v1/roles?inventory=true) read the installed list twice: once for
   the sweep and again in list_models for the picker's local models.
6. Broker() built Ollama's HTTP client, about half a second on the main box (an SSL context and
   certifi's bundle), whether or not the broker ever asked Ollama anything. Every test that
   builds a Broker paid it: test_cli_routing.py took 139 s for 206 tests, 6.6 s without it.
7. /v1/status spawned nvidia-smi on every poll, after its Ollama reads rather than alongside
   them, looked nvidia-smi up on PATH every time, and read the CLI token file twice.
8. The roles sweep read roles.json and disabled.json once per role: ~51 reads per /v1/roles.

No Ollama, no GPU, no worker: every external is a fake.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import pathlib
import time
from types import SimpleNamespace

import httpx
import pytest

from app import broker as broker_mod
from app import gpu
from app.broker import Broker
from app.config import BrokerSettings
from app.ollama import OllamaClient


class _Ollama:
    def __init__(self, names: list[str], show_delay: float = 0.0):
        self._names = names
        self.tags_calls = 0
        self.show_in_flight = 0
        self.show_max = 0
        self._delay = show_delay

    async def tags(self):
        self.tags_calls += 1
        return [{"name": n, "size": 1, "digest": n} for n in self._names]

    async def show(self, name):
        self.show_in_flight += 1
        self.show_max = max(self.show_max, self.show_in_flight)
        try:
            await asyncio.sleep(self._delay)
            return {"capabilities": ["completion"]}
        finally:
            self.show_in_flight -= 1

    async def aclose(self):
        pass


def _broker(tmp_path, roles: dict, **kw) -> Broker:
    f = tmp_path / "roles.json"
    f.write_text(json.dumps(roles), encoding="utf-8")
    return Broker(BrokerSettings(roles_file=str(f), disabled_file=str(tmp_path / "d.json"),
                                 tokens_file=str(tmp_path / "t.json"), **kw))


def test_audit_roles_reads_the_installed_list_once(tmp_path, monkeypatch):
    async def _no_gpu():
        return None
    monkeypatch.setattr(gpu, "vram", _no_gpu)
    roles = {"chat": "qwen3*:8b", "code": "qwen3*:8b", "vision": "gemma3*:4b",
             "embed": "bge-m3*", "edu": "qwen3*:8b"}
    b = _broker(tmp_path, roles)
    fake = _Ollama(["qwen3:8b", "gemma3:4b", "bge-m3:latest"])
    b.ollama = fake
    asyncio.run(b.audit_roles())
    assert fake.tags_calls == 1, f"audit_roles fetched the installed list {fake.tags_calls} times"


def test_list_models_probes_capabilities_together(tmp_path):
    b = _broker(tmp_path, {})
    fake = _Ollama([f"model-{i}:8b" for i in range(6)], show_delay=0.05)
    b.ollama = fake

    # The per-model probe, standing in for _capabilities' /api/show; what is under test is
    # whether list_models runs those probes together.
    async def caps(name, digest):
        return (await fake.show(name))["capabilities"]
    b._capabilities = caps
    out = asyncio.run(b.list_models())
    assert [m["name"] for m in out] == [f"model-{i}:8b" for i in range(6)]
    assert fake.show_max > 1, "the /api/show probes ran one at a time"


@pytest.mark.parametrize("op", ["embed_image", "tts_light", "transcribe"])
def test_ungated_media_jobs_are_bounded(tmp_path, monkeypatch, op):
    b = _broker(tmp_path, {}, media_enabled=True, light_media_concurrency=2,
                kokoro_python="k.exe", kokoro_model_path="m.onnx", kokoro_voices_path="v.bin")
    state = {"now": 0, "max": 0}

    async def fake_job(*, python_exe, spec, timeout):
        state["now"] += 1
        state["max"] = max(state["max"], state["now"])
        await asyncio.sleep(0.02)
        state["now"] -= 1
        return {"ok": True}

    monkeypatch.setattr(broker_mod.media, "run_media_job", fake_job)

    def call():
        if op == "embed_image":
            return b.embed_image(["QUJD"])
        if op == "tts_light":
            return b.tts_light("hello")
        return b.transcribe("QUJD")

    async def go():
        await asyncio.gather(*(call() for _ in range(6)))

    asyncio.run(go())
    assert state["max"] == 2, f"{state['max']} {op} interpreters ran at once, limit 2"


def test_ungated_media_jobs_never_wait_on_the_gpu_gate(tmp_path, monkeypatch):
    """The bound is their own. Holding the GPU gate must not stall a read-aloud."""
    b = _broker(tmp_path, {}, media_enabled=True)

    async def fake_job(*, python_exe, spec, timeout):
        return {"ok": True}

    monkeypatch.setattr(broker_mod.media, "run_media_job", fake_job)

    async def go():
        async with b.gate.hold(model="x", source="test"):
            return await asyncio.wait_for(b.embed_image(["QUJD"]), 2.0)

    assert asyncio.run(go()) == {"ok": True}


# --- the roles views: one installed-list read per sweep -------------------------------------

def test_roles_views_read_the_installed_list_once_however_many_roles_substitute(tmp_path):
    """GET /v1/roles, as the live box ran it before this fix: the sweep read the installed list, then
    every role that fell back planned its substitute by reading it AGAIN, about 15 reads at
    ~0.35 s each, so the Rails tab and every rail's chips waited 6-7 s for one view. resolve_ref
    now hands the list it holds to _plan_substitute on all three of its substitute paths, and
    nothing else pinned that for the views: only the startup audit was counted.

    The map makes each path substitute: a glob that matches nothing, a name that is not
    installed, and a name that is installed but disabled. DEFAULT_ROLES underneath adds the
    "many": on this two-model inventory most of them fall back too."""
    roles = {"chat": "qwen3*:30b",              # a glob with no installed match
             "code": "qwen2.5-coder:32b",       # a name that is not installed
             "edu": "gemma3:4b"}                # installed, and disabled below
    (tmp_path / "d.json").write_text(json.dumps(["gemma3:4b"]), encoding="utf-8")
    b = _broker(tmp_path, roles, upstreams_file=str(tmp_path / "upstreams.json"))
    fake = _Ollama(["qwen3:8b", "gemma3:4b", "bge-m3:latest"])
    b.ollama = fake
    for view in ("roles_view", "roles_view_local"):
        fake.tags_calls = 0
        rows = {r["role"]: r for r in asyncio.run(getattr(b, view)())}
        reasons = {role: (rows[role]["fallback"] or {}).get("reason") for role in roles}
        assert reasons == {"chat": "not installed", "code": "not installed",
                           "edu": "disabled"}, reasons
        assert sum(r["fallback"] is not None for r in rows.values()) > 3
        assert fake.tags_calls == 1, (
            f"{view} read the installed list {fake.tags_calls} times for one sweep")


def test_the_roles_sweep_reads_the_local_list_while_the_remotes_are_probed(tmp_path):
    """The local list and the remote probes are independent reads, and the view waits for both.
    Read one after the other, a sweep cost their sum; together, the slower of the two."""
    (tmp_path / "upstreams.json").write_text(
        json.dumps({"offsite": {"url": "http://gpu-box.example.internal:11500"}}),
        encoding="utf-8")
    b = _broker(tmp_path, {"chat": "offsite::qwen3*:30b"},
                upstreams_file=str(tmp_path / "upstreams.json"))
    state = {"tags_in_flight": False, "overlap": False}

    class SlowList(_Ollama):
        async def tags(self):
            state["tags_in_flight"] = True
            try:
                await asyncio.sleep(0.05)
                return await super().tags()
            finally:
                state["tags_in_flight"] = False

    class Box:
        """A remote broker that answers; its health probe notes whether the local read is out."""
        name = "offsite"

        async def healthy(self):
            await asyncio.sleep(0.01)
            state["overlap"] = state["overlap"] or state["tags_in_flight"]
            return True

        async def models(self):
            return [{"name": "qwen3:30b", "parameter_size": "30B"}]

        async def status(self):
            return {"loaded": []}

    b.ollama = SlowList(["gemma3:4b"])
    b._upstream = lambda name, registry=None: Box()
    rows = {r["role"]: r for r in asyncio.run(b.roles_view())}
    assert rows["chat"]["installed"] is True and rows["chat"]["upstream"] == "offsite"
    assert state["overlap"], "the local model list was read before any remote was probed"


def test_the_rails_tab_read_lists_this_box_s_models_from_the_sweep_s_one_read(tmp_path):
    """GET /v1/roles?inventory=true is the Rails tab's whole read. The sweep read the installed
    list to resolve the roles, then list_models read it again for the picker's local models:
    two /api/tags per load for one answer. The two now share one read."""
    b = _broker(tmp_path, {"chat": "qwen3*:8b", "edu": "gemma3*"},
                upstreams_file=str(tmp_path / "upstreams.json"))
    fake = _Ollama(["qwen3:8b", "gemma3:4b"])
    b.ollama = fake
    view = asyncio.run(b.roles_inventory())
    assert [m["name"] for m in view["models"]["local"]] == ["qwen3:8b", "gemma3:4b"]
    rows = {r["role"]: r for r in view["roles"]}
    assert rows["chat"]["resolved"] == "qwen3:8b" and rows["edu"]["resolved"] == "gemma3:4b"
    assert fake.tags_calls == 1, (
        f"one inventory read asked Ollama for the installed list {fake.tags_calls} times")


def test_an_unreadable_local_list_still_leaves_the_rails_tab_read_answering(tmp_path):
    """The shared read failing is seen by both halves: no local models for the picker, and every
    local role unresolved, with the view still answering rather than raising."""
    class Down(_Ollama):
        async def tags(self):
            self.tags_calls += 1
            raise RuntimeError("ollama is down")

    b = _broker(tmp_path, {"chat": "qwen3*:8b"},
                upstreams_file=str(tmp_path / "upstreams.json"))
    b.ollama = Down([])
    view = asyncio.run(b.roles_inventory())
    assert view["models"]["local"] == []
    assert {r["role"]: r for r in view["roles"]}["chat"]["installed"] is False
    assert b.ollama.tags_calls == 1


# --- /v1/status and /v1/roles: the work each poll repeats (audit 2026-10-14) ------------------
#
# /v1/status spawned nvidia-smi on every poll (~180 ms; the shell, every rail header and BrokerTray
# poll it ~27 times a minute between them), after its two Ollama reads rather than alongside, and
# cli_state() read the CLI token file twice. /v1/roles' sweep re-read roles.json and disabled.json
# for every role through resolve_ref: about 51 synchronous file reads in the event loop per call.

class _StatusOllama(_Ollama):
    """Answers status()'s two Ollama reads, each taking `delay`, noting while one is out."""

    def __init__(self, delay: float = 0.0, fail: str = ""):
        super().__init__([])
        self._delay, self._fail = delay, fail
        self.in_flight = 0
        self.asked: list[str] = []

    async def _read(self, what: str, value):
        self.asked.append(what)
        self.in_flight += 1
        try:
            await asyncio.sleep(self._delay)
            if what == self._fail or self._fail == "all":
                raise httpx.ConnectError(f"{what}: ollama is down")
            return value
        finally:
            self.in_flight -= 1

    async def version(self):
        return await self._read("version", "0.0.0-test")

    async def ps(self):
        return await self._read("ps", [{"name": "qwen3:8b", "size_vram": 1}])


class _Smi:
    """nvidia-smi as create_subprocess_exec hands it back: answers after `delay`, or never."""

    def __init__(self, delay: float = 0.05, hang: bool = False):
        self.returncode: int | None = None
        self.killed = False
        self._delay, self._hang = delay, hang

    async def communicate(self):
        await asyncio.sleep(3600 if self._hang else self._delay)
        self.returncode = 0
        return b"24576, 1024, 23552, Test GPU\n", b""

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        return self.returncode


@pytest.fixture
def smi(monkeypatch):
    """Every nvidia-smi spawned while the test runs, each a _Smi built by `smi.make`."""
    spawned: list[_Smi] = []

    class Spawner:
        make = staticmethod(lambda: _Smi())

    async def fake_exec(*_a, **_k):
        proc = Spawner.make()
        spawned.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(gpu, "_nvidia_smi", lambda: "nvidia-smi")
    Spawner.spawned = spawned
    return Spawner


def test_five_concurrent_status_polls_spawn_nvidia_smi_once(tmp_path, smi):
    """The backlog's check: five polls at once are one child, and a sixth inside the window is
    answered from it. Each still reports the card."""
    b = _broker(tmp_path, {})
    b.ollama = _StatusOllama()

    async def go():
        burst = await asyncio.gather(*(b.status() for _ in range(5)))
        return burst, await b.status()

    burst, again = asyncio.run(go())
    assert len(smi.spawned) == 1, f"{len(smi.spawned)} nvidia-smi for six polls inside the window"
    assert [s["gpu"]["total_mib"] for s in [*burst, again]] == [24576] * 6


def test_a_poll_after_the_window_reads_the_card_again(tmp_path, smi):
    """Cached for the window, not for ever: VRAM moves as models load."""
    b = _broker(tmp_path, {})
    b.ollama = _StatusOllama()
    b._vram = gpu.VramCache(ttl=0.05)

    async def go():
        await b.status()
        await asyncio.sleep(0.1)
        await b.status()

    asyncio.run(go())
    assert len(smi.spawned) == 2


def test_the_shipped_window_is_shorter_than_any_poller_s_interval():
    """Every poller asks every 5 s or slower, so a 2 s window never shows one a figure older than
    the one it drew last time; a longer window would."""
    assert gpu.VramCache()._ttl == gpu.VRAM_TTL_S == 2.0


def test_a_poll_that_hangs_up_does_not_cancel_the_probe_another_is_waiting_on(tmp_path, smi):
    b = _broker(tmp_path, {})
    b.ollama = _StatusOllama()

    async def go():
        first = asyncio.ensure_future(b.status())
        second = asyncio.ensure_future(b.status())
        await asyncio.sleep(0.01)              # both are waiting on the one probe
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        return await asyncio.wait_for(second, 5)

    st = asyncio.run(go())
    assert st["gpu"] == {"total_mib": 24576, "used_mib": 1024, "free_mib": 23552,
                         "gpu_name": "Test GPU"}
    assert len(smi.spawned) == 1 and not smi.spawned[0].killed


def test_closing_the_broker_reaps_a_probe_nobody_waits_for_any_more(tmp_path, smi):
    """A poll that hung up leaves the shared probe running, on purpose. Shutdown must not leave it
    behind: aclose cancels it, and vram()'s own reap kills the child."""
    b = _broker(tmp_path, {})
    b.ollama = _StatusOllama()
    smi.make = staticmethod(lambda: _Smi(hang=True))

    async def go():
        poll = asyncio.ensure_future(b.status())
        await asyncio.sleep(0.01)
        poll.cancel()
        with pytest.raises(asyncio.CancelledError):
            await poll
        assert not smi.spawned[0].killed, "the probe died with the poll that hung up"
        t0 = time.perf_counter()
        # Bounded, and timed: a close that waited the probe out instead of cancelling it would
        # end only at vram()'s own 10 s ceiling, with the child reaped either way.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(b.aclose(), 2.0)
        # Read here, not after asyncio.run: its own shutdown cancels every task left over, so a
        # broker that never closed the probe would still see the child reaped by then.
        return time.perf_counter() - t0, smi.spawned[0].killed

    took, killed = asyncio.run(go())
    assert killed, "closing the broker left nvidia-smi running"
    assert took < 1.0, f"closing the broker waited {took:.2f} s for a probe it should cancel"


def test_status_reads_ollama_and_the_card_together(tmp_path, monkeypatch):
    """Independent reads; the poll waits for the slowest, not the sum."""
    b = _broker(tmp_path, {})
    b.ollama = _StatusOllama(delay=0.05)
    seen = {"overlap": False}

    async def card():
        await asyncio.sleep(0.01)
        seen["overlap"] = b.ollama.in_flight > 0
        return None

    monkeypatch.setattr(gpu, "vram", card)
    asyncio.run(b.status())
    assert seen["overlap"], "nvidia-smi was read only after Ollama had answered"


@pytest.mark.parametrize("fail,reachable,version", [
    ("all", False, None), ("version", False, None), ("ps", False, "0.0.0-test")])
def test_status_still_answers_whichever_read_fails(tmp_path, smi, fail, reachable, version):
    """status must never raise, and each failure is its own: Ollama down still reports the card,
    and a version that answered is kept when only the resident list failed, as before."""
    b = _broker(tmp_path, {})
    b.ollama = _StatusOllama(fail=fail)
    st = asyncio.run(b.status())
    assert (st["ollama_reachable"], st["ollama_version"], st["loaded"]) == (reachable, version, [])
    assert st["gpu"]["total_mib"] == 24576


def test_a_down_ollama_is_dialled_once_per_poll(tmp_path, smi):
    """Read together, the version and the resident list each dialled a down Ollama, two failed
    connects per poll (a refused connect to 127.0.0.1 takes about 2 s on Windows). The two are
    read one after the other again, the card beside them: a version that fails is the answer for
    both, as it was before the reads went together."""
    b = _broker(tmp_path, {})
    b.ollama = _StatusOllama(fail="all")
    st = asyncio.run(b.status())
    assert st["ollama_reachable"] is False and st["gpu"]["total_mib"] == 24576
    assert b.ollama.asked == ["version"], b.ollama.asked


def test_a_card_read_that_raises_is_reported_as_no_card(tmp_path, monkeypatch):
    b = _broker(tmp_path, {})
    b.ollama = _StatusOllama()

    async def boom():
        raise OSError("the driver went away")

    monkeypatch.setattr(gpu, "vram", boom)
    st = asyncio.run(b.status())
    assert st["gpu"] is None and st["ollama_reachable"] is True


def test_nvidia_smi_is_looked_up_on_path_once(monkeypatch):
    """shutil.which walks every PATH directory times every PATHEXT extension on Windows, and it
    ran on every probe. The path is a filesystem fact; it is looked up once per process. The
    real lookup is counted here, so gpu's cache is cleared before and after."""
    calls: list[str] = []
    spawned: list[_Smi] = []

    def which(name):
        calls.append(name)
        return "nvidia-smi"

    async def fake_exec(*_a, **_k):
        spawned.append(_Smi())
        return spawned[-1]

    async def go():
        await gpu.vram()
        await gpu.vram()

    monkeypatch.setattr(gpu.shutil, "which", which)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    gpu._nvidia_smi.cache_clear()
    try:
        asyncio.run(go())
    finally:
        gpu._nvidia_smi.cache_clear()
    assert len(spawned) == 2
    assert calls == ["nvidia-smi"], f"looked up {len(calls)} times for two probes"


def test_a_status_poll_reads_the_cli_token_file_once(tmp_path, monkeypatch):
    token = tmp_path / "cli-token.txt"
    token.write_text("sk-test", encoding="utf-8")
    b = _broker(tmp_path, {}, cli_oauth_token_file=str(token))
    reads: list[str] = []
    real = pathlib.Path.read_bytes

    def counting(self, *a, **k):
        if self.name == token.name:
            reads.append(self.name)
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_bytes", counting)
    state = b.cli_state()
    assert state["token_problem"] == ""
    assert len(reads) == 1, f"cli_state read the token file {len(reads)} times"


def test_an_unreadable_token_file_is_still_a_blocker_on_status(tmp_path):
    """The one read now feeds both `token_problem` and the blocker list."""
    b = _broker(tmp_path, {}, cli_oauth_token_file=str(tmp_path / "missing-token.txt"))
    state = b.cli_state()
    assert state["token_problem"] and "token_unreadable" in state["blockers"]


@pytest.mark.parametrize("view", ["roles_view", "roles_view_local"])
def test_a_roles_sweep_reads_each_settings_file_once(tmp_path, monkeypatch, view):
    """The backlog's check: at most two settings reads (roles.json, disabled.json) per sweep,
    however many roles there are and however many of them substitute."""
    roles = {"chat": "qwen3*:30b",              # a glob with no installed match
             "code": "qwen2.5-coder:32b",       # a name that is not installed
             "edu": "gemma3:4b"}                # installed, and disabled below
    (tmp_path / "d.json").write_text(json.dumps(["gemma3:4b"]), encoding="utf-8")
    b = _broker(tmp_path, roles, upstreams_file=str(tmp_path / "upstreams.json"))
    b.ollama = _Ollama(["qwen3:8b", "gemma3:4b", "bge-m3:latest"])
    reads: list[str] = []
    real = pathlib.Path.read_text

    def counting(self, *a, **k):
        reads.append(self.name)
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", counting)
    rows = {r["role"]: r for r in asyncio.run(getattr(b, view)())}
    assert {role: (rows[role]["fallback"] or {}).get("reason") for role in roles} == {
        "chat": "not installed", "code": "not installed", "edu": "disabled"}
    assert len(rows) > 10, "DEFAULT_ROLES underneath supplies the many"
    assert sorted(reads) == ["d.json", "roles.json"], f"{len(reads)} settings reads: {reads}"


# --- the reads a status poll, an @role chat and a sweep still repeated (2026-10-14) ------------
#
# cli_state read roles.json twice (the switch file judged against roles(), then a second read for
# its problem; three times when the file named a role roles.json lacks), route() read the
# role map and resolve_ref read it again on every @role chat, and the sweep read upstreams.json
# once per delegated role, once per probe and once more per delegated row.

def _counting_reads(monkeypatch) -> list[str]:
    reads: list[str] = []
    real = pathlib.Path.read_text

    def counting(self, *a, **k):
        reads.append(self.name)
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", counting)
    return reads


@pytest.mark.parametrize("switches", [None, {"chat": True}, {"chat": True, "gone": False}],
                         ids=["env-roles", "switch-file", "switch-file-naming-a-lost-role"])
def test_a_status_poll_reads_roles_json_once(tmp_path, monkeypatch, switches):
    kw = {}
    if switches is not None:
        (tmp_path / "cli-roles.json").write_text(json.dumps(switches), encoding="utf-8")
        kw["cli_roles_file"] = str(tmp_path / "cli-roles.json")
    b = _broker(tmp_path, {"chat": "qwen3*:8b"}, **kw)
    reads = _counting_reads(monkeypatch)
    state = b.cli_state()
    assert reads.count("roles.json") == 1, f"cli_state read roles.json {reads.count('roles.json')} times"
    if switches is not None:
        assert state["roles"] == ["chat"] and state["roles_source"] == "file"
    if switches and "gone" in switches:
        assert "'gone'" in state["roles_problem"] and "has no role for" in state["roles_problem"]


class _ChatOllama(_Ollama):
    """Answers a chat, buffered or streamed, with nothing resident to evict."""

    async def ps(self):
        return []

    async def chat(self, model, messages, **kw):
        return {"model": model, "message": {"role": "assistant", "content": "ok"}, "done": True}

    async def chat_stream(self, model, messages, **kw):
        yield {"model": model, "message": {"role": "assistant", "content": "ok"}, "done": True}


@pytest.mark.parametrize("how", ["chat", "chat_stream"])
def test_an_at_role_chat_reads_roles_json_once(tmp_path, monkeypatch, how):
    b = _broker(tmp_path, {"chat": "qwen3*:8b"})
    b.ollama = _ChatOllama(["qwen3:8b"])
    reads = _counting_reads(monkeypatch)
    messages = [{"role": "user", "content": "hi"}]

    async def go():
        if how == "chat":
            return [await b.chat("@chat", messages)]
        return [frame async for frame in b.chat_stream("@chat", messages)]

    frames = asyncio.run(go())
    assert frames[-1]["model"] == "qwen3:8b"
    assert reads.count("roles.json") == 1, f"one @role {how} read roles.json {reads.count('roles.json')} times"


def test_a_delegated_role_reads_each_settings_file_once_per_call(tmp_path, monkeypatch):
    """route() read upstreams.json to split the role's value and again to build the box's client."""
    (tmp_path / "upstreams.json").write_text(
        json.dumps({"offsite": {"url": "http://gpu-box.example.internal:11500"}}), encoding="utf-8")
    b = _broker(tmp_path, {"chat": "offsite::qwen3*:30b"},
                upstreams_file=str(tmp_path / "upstreams.json"))
    reads = _counting_reads(monkeypatch)

    async def go():
        up, model, fb = await b.route("@chat", substitute=True)
        return up.name, model, fb

    assert asyncio.run(go()) == ("offsite", "qwen3*:30b", None)
    assert (reads.count("roles.json"), reads.count("upstreams.json")) == (1, 1), reads


@pytest.mark.parametrize("view", ["roles_view", "roles_inventory"])
def test_a_roles_sweep_reads_upstreams_json_once(tmp_path, monkeypatch, view):
    """Three delegated roles on two boxes, and one naming a box nobody registered (resolved here,
    so it reaches resolve_ref with its `::`). Every box is down, so nothing is dialled past the
    health probe. roles_inventory reads the registry for its rows and hands it to the sweep."""
    (tmp_path / "upstreams.json").write_text(json.dumps({
        "offsite": {"url": "http://gpu-box.example.internal:11500"},
        "spare": {"url": "http://spare-box.example.internal:11500"}}), encoding="utf-8")
    roles = {"chat": "offsite::qwen3*:30b", "code": "offsite::qwen2.5-coder:32b",
             "vision": "spare::gemma3:4b", "edu": "nowhere::qwen3:8b"}
    b = _broker(tmp_path, roles, upstreams_file=str(tmp_path / "upstreams.json"))
    b.ollama = _Ollama(["qwen3:8b"])

    async def down(self):
        return False

    monkeypatch.setattr(broker_mod.UpstreamClient, "healthy", down)
    reads = _counting_reads(monkeypatch)
    out = asyncio.run(getattr(b, view)())
    rows = {r["role"]: r for r in (out["roles"] if view == "roles_inventory" else out)}
    assert (rows["chat"]["upstream"], rows["vision"]["upstream"]) == ("offsite", "spare")
    assert rows["edu"]["upstream"] == "local"
    assert reads.count("upstreams.json") == 1, (
        f"one {view} read upstreams.json {reads.count('upstreams.json')} times")


# --- the reads an owner's CLI call and an OpenAI-dialect @role still repeated (2026-10-14) ------
#
# An owner's CLI call read the switch file and roles.json in cli_decision, again in the
# re-decision once it held its slot, and once more for its --model (_cli_model_for), and the
# switch file's note on a key no role answers to read roles.json once more each time. An
# OpenAI-dialect @role read the role map for its delegation check and again inside resolve_ref.

_CLI_ENV = ("BROKER_CLI_ENABLED", "BROKER_CLI_EXE", "BROKER_CLI_OWNERS", "BROKER_CLI_ROLES",
            "BROKER_CLI_OAUTH_TOKEN", "BROKER_CLI_OAUTH_TOKEN_FILE", "BROKER_CLI_WORKDIR",
            "BROKER_CLI_MODEL", "BROKER_CLI_MODELS", "BROKER_CLI_ROLES_FILE",
            "BROKER_CLI_CONCURRENCY", "BROKER_CLI_TIMEOUT", "BROKER_CLI_TEMP_ROOT")
HAIKU = "claude-haiku-5-5"


def _owner_cli(tmp_path, monkeypatch, switches: dict, held: list | None = None,
               built: list | None = None, **kw):
    """A broker whose owner-one may send @chat to a stand-in CLI client, switched by a real
    switch file holding `switches`. Returns (broker, calls): each call's (`--model`, how). A call
    whose turn in `held` is an Event waits on it inside the CLI. With `built`, each call first
    builds the real client and drops it, so its token read is counted, and the token it would
    have carried is appended to `built`."""
    for key in _CLI_ENV:
        monkeypatch.delenv(key, raising=False)
    exe = tmp_path / "claude.exe"
    exe.write_bytes(b"")
    (tmp_path / "cli-roles.json").write_text(json.dumps(switches), encoding="utf-8")
    b = _broker(tmp_path, {"chat": "qwen3*:8b"}, cli_enabled=True, cli_exe=str(exe),
                cli_owners="owner-one", cli_roles_file=str(tmp_path / "cli-roles.json"),
                cli_workdir=str(tmp_path), **kw)
    calls: list[list] = []
    answer = {"model": "claude-cli", "message": {"role": "assistant", "content": "from cli"}}

    def client(timeout=None, model=None, token_read=None):
        if built is not None:
            carried = {} if token_read is None else {"token_read": token_read}
            built.append(Broker._cli_client(b, timeout=timeout, model=model,
                                            **carried).oauth_token)
        call = [model, None]
        calls.append(call)

        async def chat(messages, **_kw):
            call[1] = "chat"
            if held and held[len(calls) - 1] is not None:
                await held[len(calls) - 1].wait()
            return {**answer, "done": True, "cli": {"backend": "claude"}}

        async def chat_stream(messages, **_kw):
            call[1] = "chat_stream"
            yield {**answer, "done": False}
            yield {**answer, "message": {"role": "assistant", "content": ""}, "done": True,
                   "cli": {"backend": "claude"}}

        return SimpleNamespace(chat=chat, chat_stream=chat_stream)

    b._cli_client = client
    return b, calls


@pytest.mark.parametrize("switches", [{"chat": {"enabled": True, "model": HAIKU}},
                                      {"chat": True, "gone": False}],
                         ids=["switched-with-a-model", "naming-a-lost-role"])
@pytest.mark.parametrize("how", ["chat", "chat_stream"])
def test_an_owner_s_cli_call_reads_each_settings_file_once(tmp_path, monkeypatch, how, switches):
    """The decision, the re-decision once the call holds its slot (taken at once here), and the
    model the call passes all come from one read of the switch file, judged against one read of
    roles.json, whose word on a key no role answers to comes from that same read."""
    b, calls = _owner_cli(tmp_path, monkeypatch, switches)
    reads = _counting_reads(monkeypatch)
    messages = [{"role": "user", "content": "hi"}]

    async def go():
        if how == "chat":
            return [await b.chat("@chat", messages, requester="owner-one")]
        return [f async for f in b.chat_stream("@chat", messages, requester="owner-one")]

    frames = asyncio.run(go())
    assert any((f.get("message") or {}).get("content") == "from cli" for f in frames), frames
    model = HAIKU if isinstance(switches["chat"], dict) else ""
    assert calls == [[model, how]], calls
    counts = (reads.count("cli-roles.json"), reads.count("roles.json"))
    assert counts == (1, 1), f"one owner {how} read (switch file, roles.json) {counts} times"


def test_an_owner_s_cli_call_that_waited_for_its_slot_reads_each_file_once_more(tmp_path,
                                                                               monkeypatch):
    """The re-decision reads again only for a call that WAITED, which is what it is for: a role
    switched off while the call queued must stop it. Once on arrival, once when the slot came
    free, and the model from that second read."""
    async def go():
        release = asyncio.Event()
        b, calls = _owner_cli(tmp_path, monkeypatch, {"chat": True}, held=[release, None],
                              cli_concurrency=1)
        messages = [{"role": "user", "content": "hi"}]
        first = asyncio.ensure_future(b.chat("@chat", messages, requester="owner-one"))
        while not calls:
            await asyncio.sleep(0.01)
        reads = _counting_reads(monkeypatch)
        second = asyncio.ensure_future(b.chat("@chat", messages, requester="owner-one"))
        while b._cli_waiting == 0:
            await asyncio.sleep(0.01)
        release.set()
        out = await asyncio.wait_for(asyncio.gather(first, second), 10)
        return out, calls, reads

    out, calls, reads = asyncio.run(go())
    assert [o["message"]["content"] for o in out] == ["from cli", "from cli"]
    assert len(calls) == 2
    counts = (reads.count("cli-roles.json"), reads.count("roles.json"))
    assert counts == (2, 2), f"the call that waited read (switch file, roles.json) {counts} times"


# The token file is read with read_bytes, so the read_text counters above never saw it: an owner's
# CLI call read it in the decision, again in the re-decision once it held its slot, and once more
# in _cli_client for the token the client carries.

def _counting_token_reads(monkeypatch, token: pathlib.Path) -> list[str]:
    reads: list[str] = []
    real = pathlib.Path.read_bytes

    def counting(self, *a, **k):
        if self.name == token.name:
            reads.append(self.name)
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_bytes", counting)
    return reads


async def _until(cond, limit: float = 10.0) -> None:
    """Poll `cond` until it holds; a broker that never gets there fails here, not by hanging."""
    async def poll():
        while not cond():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(poll(), limit)


@pytest.mark.parametrize("how", ["chat", "chat_stream"])
def test_an_owner_s_cli_call_reads_the_token_file_once(tmp_path, monkeypatch, how):
    """The decision's read is carried to the re-decision (the slot was free) and to the client,
    which carries the token that read found."""
    token = tmp_path / "cli-token.txt"
    token.write_text("sk-ant-oat01-first", encoding="utf-8")
    built: list[str] = []
    b, calls = _owner_cli(tmp_path, monkeypatch, {"chat": True}, built=built,
                          cli_oauth_token_file=str(token))
    reads = _counting_token_reads(monkeypatch, token)
    messages = [{"role": "user", "content": "hi"}]

    async def go():
        if how == "chat":
            return [await b.chat("@chat", messages, requester="owner-one")]
        return [f async for f in b.chat_stream("@chat", messages, requester="owner-one")]

    frames = asyncio.run(go())
    assert any((f.get("message") or {}).get("content") == "from cli" for f in frames), frames
    assert calls == [["", how]], calls
    assert built == ["sk-ant-oat01-first"]
    assert len(reads) == 1, f"one owner {how} read the token file {len(reads)} times"


def test_an_owner_s_cli_call_that_waited_reads_the_token_file_again_and_carries_that_read(
        tmp_path, monkeypatch):
    """A call that waited is decided again on fresh reads, the token file's included, so a token
    rotated while it queued is the one its client carries: once on arrival, once when the slot
    came free, and the client's token from that second read."""
    token = tmp_path / "cli-token.txt"
    token.write_text("sk-ant-oat01-first", encoding="utf-8")
    built: list[str] = []

    async def go():
        release = asyncio.Event()
        b, calls = _owner_cli(tmp_path, monkeypatch, {"chat": True}, held=[release, None],
                              built=built, cli_concurrency=1, cli_oauth_token_file=str(token))
        messages = [{"role": "user", "content": "hi"}]
        first = asyncio.ensure_future(b.chat("@chat", messages, requester="owner-one"))
        await _until(lambda: calls or first.done())
        reads = _counting_token_reads(monkeypatch, token)
        second = asyncio.ensure_future(b.chat("@chat", messages, requester="owner-one"))
        await _until(lambda: b._cli_waiting or second.done())
        token.write_text("sk-ant-oat01-rotated", encoding="utf-8")
        release.set()
        out = await asyncio.wait_for(asyncio.gather(first, second), 10)
        return out, reads

    out, reads = asyncio.run(go())
    assert [o["message"]["content"] for o in out] == ["from cli", "from cli"]
    assert built == ["sk-ant-oat01-first", "sk-ant-oat01-rotated"]
    assert len(reads) == 2, f"the call that waited read the token file {len(reads)} times"


def test_an_openai_dialect_role_reads_each_settings_file_once(tmp_path, monkeypatch):
    """resolve_for_external read the role map for its delegation check, then resolve_ref read it
    again; a value naming a box nobody registered read upstreams.json there and again in
    resolve_ref's guard; an embedder's disabled.json was read by resolve_ref and again by the
    check after it."""
    (tmp_path / "upstreams.json").write_text(
        json.dumps({"offsite": {"url": "http://gpu-box.example.internal:11500"}}),
        encoding="utf-8")
    b = _broker(tmp_path, {"chat": "qwen3*:8b", "edu": "nowhere::qwen3:8b", "embed": "bge-m3"},
                upstreams_file=str(tmp_path / "upstreams.json"))
    (tmp_path / "d.json").write_text("[]", encoding="utf-8")
    b.ollama = _Ollama(["qwen3:8b", "bge-m3:latest"])
    reads = _counting_reads(monkeypatch)

    def counted(call):
        reads.clear()
        out = asyncio.run(call)
        return out, {n: reads.count(n) for n in ("roles.json", "upstreams.json", "d.json")}

    (name, fb), seen = counted(b.resolve_for_external("@chat"))
    assert (name, fb) == ("qwen3:8b", None)
    assert seen == {"roles.json": 1, "upstreams.json": 0, "d.json": 1}, seen
    (name, fb), seen = counted(b.resolve_for_external("@edu"))
    assert name == "qwen3:8b" and fb is not None and fb.reason == "not installed"
    assert seen["roles.json"] == 1 and seen["upstreams.json"] == 1, seen
    (name, fb), seen = counted(b.resolve_for_external("@embed", substitute=False))
    assert (name, fb) == ("bge-m3", None)
    assert seen == {"roles.json": 1, "upstreams.json": 0, "d.json": 1}, seen


# --- Ollama's HTTP client: built on first use, once -------------------------------------------

@pytest.fixture
def made_clients(monkeypatch):
    """Every httpx.AsyncClient built while the test runs, each answering /api/version from a
    fake transport (which also spares it the SSL context the real one would build)."""
    made: list[httpx.AsyncClient] = []
    real = httpx.AsyncClient

    def counting(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"version": "0.0.0-test"}))
        client = real(*args, **kwargs)
        made.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", counting)
    return made


def test_a_broker_builds_no_http_client_until_it_asks_ollama_something(tmp_path, made_clients):
    """Constructed, and closed, without a word to Ollama: no client at all. Asked twice: one
    client, built on the first ask, kept for the second, closed with the broker."""
    _broker(tmp_path, {})
    idle = _broker(tmp_path, {})
    asyncio.run(idle.aclose())
    assert made_clients == [], f"{len(made_clients)} HTTP client(s) built for nothing"

    b = _broker(tmp_path, {})

    async def run():
        assert await b.ollama.version() == "0.0.0-test"
        assert await b.ollama.version() == "0.0.0-test"
        await b.aclose()

    asyncio.run(run())
    assert len(made_clients) == 1, f"{len(made_clients)} clients for two requests"
    assert made_clients[0].is_closed


@pytest.mark.parametrize("base", ["http://127.0.0.1:11434", "http://127.0.0.1:11434/",
                                  "http://box.example:11434/ollama"])
def test_whether_ollama_sent_a_failed_request_is_answered_alike_before_its_client_exists(base):
    """sent() (whether a transport error is an Ollama outage, the broker's 503) compares the
    request with the client's base URL. Before the client is built it compares with the same URL
    as httpx would hold it, trailing slash and all, and asking builds nothing."""
    urls = [base.rstrip("/") + "/api/tags", "http://127.0.0.1:11435/api/tags",
            "https://127.0.0.1:11434/api/tags", "http://box.example:11434/ollama/api/tags",
            "http://box.example:11434/ollamax/api/tags", "http://lan-host:11434/api/tags"]

    def answers(client: OllamaClient) -> list[bool]:
        return [client.sent(httpx.ConnectError("refused", request=httpx.Request("GET", u)))
                for u in urls]

    cold = OllamaClient(base)
    before = answers(cold)
    assert cold._http is None, "asking sent() built a client"
    assert before[0] is True
    warm = OllamaClient(base)
    warm._client = httpx.AsyncClient(base_url=warm.base_url, transport=httpx.MockTransport(
        lambda request: httpx.Response(200)))
    assert answers(warm) == before
