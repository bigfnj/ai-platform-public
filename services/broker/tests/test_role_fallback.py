"""Model fallback: a role whose model cannot run serves a capability-compatible substitute.

The thing this has to get right is not "does it substitute" -- that is easy and one test would
cover it. It is that a SUBSTITUTION IS A DEGRADED STATE and must never be mistaken for a healthy
one, in either direction:

  * it must preserve capability. @vision falling back to a text model produces a confident
    description of a photo no model ever saw, with a 200 and no error anywhere.
  * it must be announced. Every substitution writes a log line, marks its queue job, and bumps a
    counter on /v1/status, because a fallback that hides a broken role map is worse than the
    error it replaced.
  * it must refuse when it cannot do either.

Settings come through the REAL overlay files rather than patched methods, for the reason
test_openai_compat's fixture already gives: a test that stubs disabled() would still pass if the
production path stopped reading disabled.json at all.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.broker import Broker, Fallback, NoSubstituteError, UnknownRoleError


def _run(coro):
    return asyncio.run(coro)


def _broker(roles: dict, installed: list[tuple[str, list[str]]], disabled: tuple[str, ...] = ()):
    """A Broker with tags(), /api/show capabilities and the role map stubbed.

    `installed` is [(name, capabilities)], so a test can install a model that IS multimodal and
    one that is not. Per-model capabilities are the whole point: a recorder that returns the
    same capability list for every model makes every vision assertion vacuous.
    """
    b = Broker.__new__(Broker)
    caps = dict(installed)
    tags = [{"name": n, "size": 1 << 30, "digest": n, "details": {"family": "t"}}
            for n, _c in installed]

    async def _tags():
        return tags

    async def _show(name):
        return {"capabilities": caps.get(name, [])}

    b.settings = SimpleNamespace(roles=lambda: roles, disabled=lambda: set(disabled),
                                 overlay_roles=lambda: dict(roles),
                                 embed_hints=lambda: ())
    b.ollama = SimpleNamespace(tags=_tags, show=_show)
    b._embed_hints = ()
    b._caps_cache = {}
    b._digests = {}
    b._fallback_count = 0
    b._last_fallback = None
    return b


VISION = ["completion", "vision"]
TEXT = ["completion"]


# --- the capability gate, which is the part that cannot be got wrong ---------------------------

def test_vision_never_falls_back_to_a_model_that_cannot_see():
    b = _broker({"vision": "gemma4:26b"},
                [("gemma4:26b", VISION), ("mistral-small3.2:24b", TEXT)],
                disabled=("gemma4:26b",))
    with pytest.raises(NoSubstituteError) as exc:
        _run(b.resolve_ref("@vision", substitute=True))
    assert "vision" in str(exc.value)


def test_vision_falls_back_to_a_vision_model_the_catalog_files_as_chat():
    """The measured trap, and the reason capability is asked of the BACKEND.

    platform_core.model_catalog files gemma3* as 'chat'. deploy/installer/roles.lean.json points
    @vision straight at gemma3:4b, because it is small AND multimodal. A same-category rule built
    on the editorial label alone would refuse this substitution on the 8 GB box that needs it
    most.
    """
    b = _broker({"vision": "gemma4:26b"},
                [("gemma4:26b", VISION), ("gemma3:4b", VISION), ("mistral-small3.2:24b", TEXT)],
                disabled=("gemma4:26b",))
    name, fb = _run(b.resolve_ref("@vision", substitute=True))
    assert name == "gemma3:4b"
    assert fb.category == "vision" and fb.reason == "disabled"


def test_a_disabled_model_is_never_chosen_as_the_substitute():
    b = _broker({"chat": "qwen3.6:27b"},
                [("qwen3.6:27b", TEXT), ("gemma4:26b", VISION), ("mistral-small3.2:24b", TEXT)],
                disabled=("qwen3.6:27b", "gemma4:26b"))
    name, fb = _run(b.resolve_ref("@chat", substitute=True))
    assert name == "mistral-small3.2:24b" and fb is not None


# --- disabled, :latest-tolerant (audit 2026-10-14) ----------------------------------------------
#
# The disabled set holds what the Models tab wrote: list_models' names, so an untagged pull is
# `qwen3:latest`. A role pinned `qwen3` resolves to the bare name, and a plain `in` let it serve
# the disabled model with no fallback, while the installed check beside it already tolerated the
# tag.

def test_a_role_pinned_untagged_falls_back_when_its_latest_install_is_disabled():
    b = _broker({"chat": "qwen3"},
                [("qwen3:latest", TEXT), ("mistral-small3.2:24b", TEXT)],
                disabled=("qwen3:latest",))
    name, fb = _run(b.resolve_ref("@chat", substitute=True))
    assert name == "mistral-small3.2:24b", "the disabled qwen3:latest served the role"
    assert fb is not None and fb.reason == "disabled" and fb.original == "qwen3"


def test_an_untagged_name_in_disabled_json_is_never_chosen_as_the_substitute():
    """The other direction: disabled.json is hand-editable, and `qwen3` written there is the model
    Ollama lists as `qwen3:latest`. Nothing else is installed, so the honest answer is a refusal."""
    b = _broker({"chat": "qwen3.6:27b"},
                [("qwen3.6:27b", TEXT), ("qwen3:latest", TEXT)],
                disabled=("qwen3.6:27b", "qwen3"))
    with pytest.raises(NoSubstituteError):
        _run(b.resolve_ref("@chat", substitute=True))


def test_the_model_list_marks_a_latest_install_disabled_under_its_untagged_name():
    """list_models' flag is what the Models tab draws; it must agree with what the resolver
    refuses, or the toggle reads 'on' for a model no role will serve."""
    b = _broker({}, [("qwen3:latest", TEXT), ("gemma4:26b", TEXT)], disabled=("qwen3",))
    rows = {m["name"]: m["disabled"] for m in _run(b.list_models())}
    assert rows == {"qwen3:latest": True, "gemma4:26b": False}


def test_an_embed_role_never_falls_back_to_a_generative_model():
    b = _broker({"embed": "bge-m3"}, [("bge-m3", TEXT), ("mistral-small3.2:24b", TEXT)],
                disabled=("bge-m3",))
    with pytest.raises(NoSubstituteError):
        _run(b.resolve_ref("@embed", substitute=True))


def test_a_media_role_is_never_substituted():
    """A media backend is loaded by the media worker from the HF cache and never appears in
    tags(). Without the exemption every @recipe-icon becomes a chat model handed to diffusers."""
    b = _broker({"recipe-icon": "flux-schnell"}, [("mistral-small3.2:24b", TEXT)])
    name, fb = _run(b.resolve_ref("@recipe-icon", substitute=True))
    assert name == "flux-schnell" and fb is None


@pytest.mark.parametrize("pinned,backend", [("FLUX-Schnell", "flux-schnell"),
                                            ("SDXL-Turbo", "sdxl-turbo")])
def test_a_media_role_pinned_in_any_case_resolves_to_its_backend(pinned, backend):
    """/v1/image draws `FLUX-Schnell` as FLUX (image_backend, 2026-10-14), but resolve_ref
    compared a role's pattern exactly, so the same role pinned by hand in roles.json resolved as
    an Ollama model: not installed, and a substitute planned for a role that renders."""
    b = _broker({"recipe-icon": pinned}, [("mistral-small3.2:24b", TEXT)])
    assert _run(b.resolve_ref("@recipe-icon", substitute=True)) == (backend, None)


@pytest.mark.parametrize("pinned,backend", [("FLUX-Schnell", "flux-schnell"),
                                            ("SDXL-Turbo", "sdxl-turbo")])
def test_a_media_role_pinned_in_any_case_shows_installed(tmp_path, pinned, backend):
    """The BACKLOG's check: a role hand-pinned `FLUX-Schnell` renders, so /v1/roles reads it
    installed, an image role, served by the backend as it spells itself. The pattern stays as
    the admin wrote it."""
    media_py = tmp_path / "python.exe"
    media_py.write_text("")
    b = _broker({"recipe-icon": pinned}, [("mistral-small3.2:24b", TEXT)])
    b.settings.media_python = str(media_py)
    row = next(r for r in _run(b.roles_view()) if r["role"] == "recipe-icon")
    assert (row["installed"], row["class"], row["fallback"]) == (True, "image", None), row
    assert (row["pattern"], row["resolved"], row["serving"]) == (pinned, backend, backend)


# --- the triggers -----------------------------------------------------------------------------

def test_a_role_pointing_at_an_uninstalled_model_falls_back():
    b = _broker({"chat": "not-installed:70b"},
                [("mistral-small3.2:24b", TEXT)])
    name, fb = _run(b.resolve_ref("@chat", substitute=True))
    assert name == "mistral-small3.2:24b" and fb.reason == "not installed"


def test_a_glob_matching_nothing_falls_back():
    b = _broker({"chat": "llama9*:70b"}, [("mistral-small3.2:24b", TEXT)])
    name, fb = _run(b.resolve_ref("@chat", substitute=True))
    assert name == "mistral-small3.2:24b" and fb.reason == "not installed"


def test_an_untagged_install_is_not_treated_as_missing():
    """Ollama reports an untagged pull as ':latest'. Without the tolerance every such role
    would 'fall back' to itself and report a degraded state that is not happening."""
    b = _broker({"embed": "bge-m3"}, [("bge-m3:latest", TEXT)])
    name, fb = _run(b.resolve_ref("@embed", substitute=True))
    assert name == "bge-m3" and fb is None


def test_a_healthy_role_reports_no_fallback():
    b = _broker({"chat": "qwen3.6:27b"}, [("qwen3.6:27b", TEXT)])
    name, fb = _run(b.resolve_ref("@chat", substitute=True))
    assert name == "qwen3.6:27b" and fb is None


def test_substitute_false_leaves_the_resolution_alone():
    """The policy switch. A concrete name, a bare glob and any internal read must see exactly
    what the map says, or read views start reporting the substitute as the configured value."""
    b = _broker({"chat": "qwen3.6:27b"}, [("mistral-small3.2:24b", TEXT)], disabled=())
    name, fb = _run(b.resolve_ref("@chat", substitute=False))
    assert name == "qwen3.6:27b" and fb is None


# --- unknown roles ----------------------------------------------------------------------------

def test_unknown_role_raises_instead_of_becoming_a_model_name():
    """It used to return the literal string, so @nosuchrole reached Ollama as a model NAMED
    'nosuchrole' and the caller read a 502 wrapping a 404."""
    b = _broker({"chat": "qwen3.6:27b"}, [("qwen3.6:27b", TEXT)])
    with pytest.raises(UnknownRoleError) as exc:
        _run(b.resolve_ref("@nosuchrole", substitute=True))
    assert "nosuchrole" in str(exc.value) and "chat" in str(exc.value)


def test_unknown_media_role_is_refused_too():
    b = _broker({"chat": "qwen3.6:27b"}, [("qwen3.6:27b", TEXT)])
    with pytest.raises(UnknownRoleError):
        b._resolve_media("@nosuchrole")


# --- the announcement, which is what stops a fallback hiding a broken map ----------------------

def test_every_fallback_writes_a_log_line(capsys):
    b = _broker({"chat": "gone:70b"}, [("mistral-small3.2:24b", TEXT)])
    _run(b.resolve_ref("@chat", substitute=True))
    assert "MODEL FALLBACK" in capsys.readouterr().err


def test_the_log_line_fires_on_every_call_not_just_the_first(capsys):
    """Deduplicating it would make the volume stop being proportional to the damage."""
    b = _broker({"chat": "gone:70b"}, [("mistral-small3.2:24b", TEXT)])
    _run(b.resolve_ref("@chat", substitute=True))
    _run(b.resolve_ref("@chat", substitute=True))
    assert capsys.readouterr().err.count("MODEL FALLBACK") == 2


def test_the_counter_persists_past_the_job():
    """A job lives for seconds. The counter is what lets the widget stay warning-toned with an
    empty queue, which is the difference between a report and a blink."""
    b = _broker({"chat": "gone:70b"}, [("mistral-small3.2:24b", TEXT)])
    _run(b.resolve_ref("@chat", substitute=True))
    assert b._fallback_count == 1
    assert b._last_fallback["model"] == "mistral-small3.2:24b"


def test_a_read_view_plans_without_announcing(capsys):
    """_plan_substitute is called by audit_roles and the Rails tab. If it announced, opening a
    tab would write one 'we are degraded' line per bad role, and a stream nobody can act on is
    a stream nobody reads."""
    b = _broker({"chat": "gone:70b"}, [("mistral-small3.2:24b", TEXT)])
    _run(b._plan_substitute("chat", "gone:70b", "not installed"))
    assert "MODEL FALLBACK" not in capsys.readouterr().err
    assert b._fallback_count == 0


def test_the_headers_are_safe_to_put_on_a_response():
    """A CR/LF in a header value makes Starlette raise, which would turn a degraded call into
    a 500 -- the one outcome worse than the error this feature replaced."""
    fb = Fallback(model="a\r\nb", requested="@chat", original="x\ny", reason="disabled",
                  category="chat")
    for v in fb.headers().values():
        assert "\r" not in v and "\n" not in v


def test_a_generative_role_may_substitute_across_chat_and_reasoning():
    """Capability is preserved; the editorial CATEGORY is only a ranking hint.

    @chat resolves to qwen3.6:27b, which the catalog files as 'reasoning'. The only other
    installed generative model is filed 'chat'. Refusing that substitution would amount to
    claiming a reasoning model cannot hold a conversation, and would leave the rail dead for
    no reason. The line drawn is capability (vision, embed, image), not taste.
    """
    b = _broker({"chat": "qwen3.6:27b"},
                [("qwen3.6:27b", TEXT), ("mistral-small3.2:24b", TEXT)],
                disabled=("qwen3.6:27b",))
    name, fb = _run(b.resolve_ref("@chat", substitute=True))
    assert name == "mistral-small3.2:24b"
    assert fb.category == "reasoning", "the ORIGINAL model's category is what is reported"


def test_roles_view_reports_both_the_configured_target_and_what_will_serve():
    """Two fields, not one. Collapsing them is how the Rails tab starts reporting a substitute
    as the admin's own setting, and an admin 'fixes' a role that was never wrong."""
    b = _broker({"chat": "qwen3.6:27b"},
                [("qwen3.6:27b", TEXT), ("mistral-small3.2:24b", TEXT)],
                disabled=("qwen3.6:27b",))
    row = next(r for r in _run(b.roles_view()) if r["role"] == "chat")
    assert row["resolved"] == "qwen3.6:27b", "the Rails picker must show the admin's choice"
    assert row["serving"] == "mistral-small3.2:24b"
    assert row["fallback"]["reason"] == "disabled"


def test_roles_view_does_not_announce(capsys):
    """The Rails tab POLLS. Announcing here would write one 'we are degraded' line per bad role
    per poll, which is how a log that matters becomes one nobody reads."""
    b = _broker({"chat": "qwen3.6:27b"},
                [("qwen3.6:27b", TEXT), ("mistral-small3.2:24b", TEXT)],
                disabled=("qwen3.6:27b",))
    _run(b.roles_view())
    assert "MODEL FALLBACK" not in capsys.readouterr().err
    assert b._fallback_count == 0


def test_a_media_role_is_not_reported_as_missing(tmp_path):
    """@recipe-icon -> flux-schnell is loaded by the media worker from the HF cache and never
    appears in Ollama's tags(). Reporting it as not-installed made it permanently red in the
    Rails tab and made 'every role resolves' unusable as a health assertion."""
    media_py = tmp_path / "python.exe"
    media_py.write_text("")
    b = _broker({"recipe-icon": "flux-schnell"}, [("mistral-small3.2:24b", TEXT)])
    b.settings.media_python = str(media_py)
    row = next(r for r in _run(b.roles_view()) if r["role"] == "recipe-icon")
    assert row["installed"] is True
    assert row["class"] == "image"
    assert row["serving"] == "flux-schnell" and row["fallback"] is None


@pytest.mark.parametrize("media_python", ["", None, "C:/nope/media-venv/Scripts/python.exe"])
def test_a_media_role_without_its_worker_is_not_installed(media_python):
    """The other half: a box with no media venv cannot serve the role at all, so a green chip
    there is a lie that only shows up as a failed job. "" must not count: Path("") is "."."""
    b = _broker({"recipe-icon": "flux-schnell"}, [("mistral-small3.2:24b", TEXT)])
    if media_python is not None:
        b.settings.media_python = media_python
    row = next(r for r in _run(b.roles_view()) if r["role"] == "recipe-icon")
    assert row["installed"] is False
    assert row["class"] == "image" and row["fallback"] is None


def test_a_media_role_is_not_installed_when_media_is_disabled(tmp_path):
    """BROKER_MEDIA_ENABLED=false makes every image job raise "media is disabled", so a present
    interpreter must not make the chip green."""
    media_py = tmp_path / "python.exe"
    media_py.write_text("")
    b = _broker({"recipe-icon": "flux-schnell"}, [("mistral-small3.2:24b", TEXT)])
    b.settings.media_python = str(media_py)
    b.settings.media_enabled = False
    row = next(r for r in _run(b.roles_view()) if r["role"] == "recipe-icon")
    assert row["installed"] is False
