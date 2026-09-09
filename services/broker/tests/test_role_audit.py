"""Startup role audit: does the shipped role map actually fit the card it landed on?

The case that produced this: the 24 GB role map this repo ships reached an 8 GB laptop, every
role pointing at a model that neither fit nor was installed, and nothing said so. It surfaced as
red "missing" chips on every rail and a suggested remedy of ~60 GB of pulls that could not help.
"""
import asyncio
from types import SimpleNamespace

from app.broker import Broker

GB = 1024 * 1024 * 1024


def _broker(roles: dict, monkeypatch, tags: list[dict], total_mib: int | None,
            stub_resolve: bool = True, overlay: dict | None = None):
    """A Broker with its two external reads stubbed: Ollama's tag list and nvidia-smi.

    ``stub_resolve`` replaces _resolve with a pass-through. That shortcut is why the
    glob bug below survived: with _resolve stubbed, NO test in this file could reach the
    globbing path, and every shipped role is a glob. Pass ``stub_resolve=False`` to audit
    through the real resolver.
    """
    b = Broker.__new__(Broker)                      # no __init__: no client, no event loop
    # overlay=None means "this box set every role itself", so the tests written before
    # provenance existed see no [inherited …] note and keep asserting the same strings.
    b.settings = SimpleNamespace(
        roles=lambda: roles,
        overlay_roles=lambda: dict(roles if overlay is None else overlay))
    b.ollama = SimpleNamespace(tags=_async(tags))
    monkeypatch.setattr("app.broker.gpu.vram",
                        _async({"total_mib": total_mib} if total_mib else None))
    # _resolve globs against tags(); the maps here are concrete, so pass the pattern through.
    if stub_resolve:
        async def resolve(ref):
            return roles[ref[1:]] if ref.startswith("@") else ref
        b._resolve = resolve
    return b


def _async(value):
    async def f(*_a, **_k):
        return value
    return f


def _run(b):
    return asyncio.run(b.audit_roles())


TAGS_8GB_BOX = [{"name": "gemma3:4b", "size": 3 * GB}, {"name": "bge-m3:latest", "size": 1 * GB}]


def test_lean_map_on_a_small_card_is_silent(monkeypatch):
    b = _broker({"chat": "gemma3:4b", "embed": "bge-m3:latest"},
                monkeypatch, TAGS_8GB_BOX, total_mib=8 * 1024)
    assert _run(b) == []


def test_reports_a_role_whose_model_is_not_installed(monkeypatch):
    b = _broker({"chat": "mistral-small3.1:24b"}, monkeypatch, TAGS_8GB_BOX, total_mib=8 * 1024)
    out = _run(b)
    assert any("NOT installed" in line and "@chat" in line for line in out)


def test_reports_an_installed_model_larger_than_the_card(monkeypatch):
    tags = [{"name": "qwen3.6:27b", "size": 17 * GB}]
    b = _broker({"reasoning": "qwen3.6:27b"}, monkeypatch, tags, total_mib=8 * 1024)
    out = _run(b)
    assert any("@reasoning" in line and "17 GB" in line and "8 GB" in line for line in out)


def test_names_modelplan_once_when_anything_is_wrong(monkeypatch):
    b = _broker({"chat": "absent-a", "vision": "absent-b"},
                monkeypatch, TAGS_8GB_BOX, total_mib=8 * 1024)
    out = _run(b)
    assert sum("modelplan.ps1" in line for line in out) == 1
    assert "-VramGb 8" in out[-1]


def test_media_backends_are_exempt(monkeypatch):
    """flux-schnell is loaded by the media worker from the HF cache and never appears in
    Ollama's tag list. Auditing it would flag every correct image role as missing."""
    b = _broker({"recipe-icon": "flux-schnell"}, monkeypatch, TAGS_8GB_BOX, total_mib=8 * 1024)
    assert _run(b) == []


def test_tolerates_the_matched_tag_suffix(monkeypatch):
    """Ollama reports an untagged pull as ':latest'. Treating that as absent is the same
    tag-tolerance bug that made the broker call a resident bge-m3 'installed: false'."""
    b = _broker({"embed": "bge-m3"}, monkeypatch, TAGS_8GB_BOX, total_mib=8 * 1024)
    assert _run(b) == []


def test_no_gpu_reports_absence_but_not_size(monkeypatch):
    """A box with no nvidia-smi still gets the useful half. Size checks need a card to compare
    against; 'this model is not here' does not, and guessing a limit would be worse than silence."""
    b = _broker({"chat": "absent", "embed": "bge-m3:latest"},
                monkeypatch, TAGS_8GB_BOX, total_mib=None)
    out = _run(b)
    assert len(out) == 1 and "NOT installed" in out[0]


def test_ollama_being_down_is_silent(monkeypatch):
    """The audit is a diagnostic. It must never be why the broker fails to come up."""
    b = _broker({"chat": "gemma3:4b"}, monkeypatch, TAGS_8GB_BOX, total_mib=8 * 1024)
    async def boom(*_a, **_k):
        raise ConnectionError("ollama is not running")
    b.ollama = SimpleNamespace(tags=boom)
    assert _run(b) == []


# --- globs: the shape the shipped map actually uses -----------------------------------
# Everything above resolves a PLAIN NAME, which is returned untouched and then caught by the
# `size is None` branch. The real map is globs, and a glob that matches nothing RAISES out of
# resolve_ollama_model instead of returning. That raise used to hit a bare
# `except Exception: continue`, so the audit reported nothing for exactly the misconfiguration
# it was written to catch. Observed downstream: a startup with @edu -> an uninstalled 24 GB
# glob on an 8 GB card produced zero ROLE WARNING lines.

def test_a_glob_matching_nothing_is_reported_not_swallowed(monkeypatch):
    b = _broker({"chat": "mistral-small3*:24b"}, monkeypatch, TAGS_8GB_BOX,
                total_mib=8 * 1024, stub_resolve=False)
    out = _run(b)
    assert any("@chat" in line and "matches NO installed model" in line for line in out)


def test_a_glob_that_resolves_is_still_size_checked(monkeypatch):
    """The RESOLVED name is what gets measured, not the pattern."""
    tags = [{"name": "qwen3.6:27b", "size": 17 * GB}]
    b = _broker({"reasoning": "qwen3.6*:27b"}, monkeypatch, tags,
                total_mib=8 * 1024, stub_resolve=False)
    out = _run(b)
    assert any("@reasoning" in line and "17 GB" in line and "8 GB" in line for line in out)


def test_a_glob_that_resolves_and_fits_stays_silent(monkeypatch):
    b = _broker({"chat": "gemma3*:4b"}, monkeypatch, TAGS_8GB_BOX,
                total_mib=8 * 1024, stub_resolve=False)
    assert _run(b) == []


# --- provenance: configured here, or inherited from the 24 GB default? -----------------
# DEFAULT_ROLES backstops anything roles.json omits, so a lean install inherits pins sized
# for the card this repo was built on without anyone choosing them. RC027 cannot catch it —
# it only checks the rails the installer actually ships. The audit can, but only if it says
# WHICH map the bad value came from: "fix your roles.json" and "you never had one" are
# different jobs.

def test_an_inherited_default_is_named_when_it_is_wrong(monkeypatch):
    b = _broker({"chat": "mistral-small3.1:24b"}, monkeypatch, TAGS_8GB_BOX,
                total_mib=8 * 1024, overlay={})
    out = _run(b)
    assert any("@chat" in line and "inherited from DEFAULT_ROLES" in line for line in out)


def test_a_locally_set_role_is_not_blamed_on_the_default(monkeypatch):
    b = _broker({"chat": "mistral-small3.1:24b"}, monkeypatch, TAGS_8GB_BOX,
                total_mib=8 * 1024, overlay={"chat": "mistral-small3.1:24b"})
    out = _run(b)
    assert any("@chat" in line for line in out)
    assert not any("inherited" in line for line in out)


def test_an_inherited_default_that_works_stays_silent(monkeypatch):
    """Provenance decorates a role that is ALREADY wrong. Most roles on a healthy box are
    inherited and fine; noting each one would bury the two that matter."""
    b = _broker({"chat": "gemma3:4b"}, monkeypatch, TAGS_8GB_BOX,
                total_mib=8 * 1024, overlay={})
    assert _run(b) == []


def test_an_unexpected_resolve_failure_says_so_rather_than_skipping(monkeypatch):
    """Still best-effort, but no longer silent: a role skipped without comment is
    indistinguishable from one that passed."""
    b = _broker({"chat": "gemma3:4b"}, monkeypatch, TAGS_8GB_BOX, total_mib=8 * 1024)
    async def boom(_ref):
        raise RuntimeError("nope")
    b._resolve = boom
    out = _run(b)
    assert any("@chat" in line and "could not be checked" in line and "RuntimeError" in line
               for line in out)
