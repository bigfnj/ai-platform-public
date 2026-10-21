"""Chip state for a role that runs on ANOTHER broker.

modelstate.py is a generated, byte-identical file shared by every rail, so this exercises
tools/rail_templates/modelstate.py.tmpl through openmaic's copy. A fix goes in the template
followed by `python tools/rail_template.py sync`; the copy this imports is never hand-edited,
and RC023 fails the build if it is.

The bug it pins was live: `@openmaic` was delegated to a 4090 on the LAN, the broker reported
`installed: true` for it, and the chip still read **missing** -- because the resolver compared
the role's model against THIS box's installed list, which will never contain it. A red dot on a
working rail is the same lie the four-state contract exists to prevent, just pointing the other
way, and it is worse than a wrong-but-plausible state because it sends someone to pull a 15 GB
model they already have somewhere else.

PORTED from the older mirror suite, and WHERE THEY DISAGREE THE TEMPLATE IS AUTHORITATIVE.

The row shape this broker's `roles_view()` emits for a delegated role, which the fixture below
copies:

* `resolved` / `serving` are the CONCRETE model the remote will run: the glob is expanded against
  the inventory the REMOTE reports, by the same resolver the remote applies. None when nothing
  there matches (or the remote could not be read) -- never the raw glob, which this resolver would
  otherwise expand against THIS box's inventory and so name a local near-miss.
* `installed` is whether the remote reported such a model; reachable alone is not enough.
* `loaded` is ALWAYS present: residency on the remote card, by that concrete name. (An earlier
  version of this docstring said delegated rows carried no `loaded` key. That stopped being true
  when `_probe` began reading the remote's `status()`, and was useless before the glob was
  expanded, since a glob never equals a resident model's name.)

An older broker sent the un-expanded glob as `resolved`; that shape is still covered below.
"""
from openmaic_app import modelstate


def _roles(**over):
    """A delegated row shaped exactly as services/broker/app/broker.py `roles_view()` builds
    one, plus an ordinary local role to prove the branch is not global."""
    entry = {"role": "openmaic", "pattern": "offsite::mistral-small3*:24b",
             "resolved": "mistral-small3.2:24b", "class": None,
             # For a delegated role this means "the REMOTE has it", not "this box has it".
             "installed": True, "loaded": False, "upstream": "offsite",
             "serving": "mistral-small3.2:24b", "fallback": None}
    entry.update(over)
    return [entry, {"role": "embed", "pattern": "bge-m3*", "upstream": "local",
                    "resolved": "bge-m3:latest", "installed": True}]


def _patch(monkeypatch, roles, installed=("gemma3:4b",), loaded=(), jobs=()):
    """The LOCAL reads stay deliberately small -- the delegated model is not among them, which
    is the whole point."""
    monkeypatch.setattr(modelstate.broker, "roles", lambda: roles)
    monkeypatch.setattr(modelstate.broker, "models",
                        lambda: [{"name": n} for n in installed])
    monkeypatch.setattr(modelstate.broker, "status",
                        lambda: {"loaded": [{"name": n} for n in loaded], "jobs": list(jobs)})


def _state(monkeypatch, roles, **kw):
    _patch(monkeypatch, roles, **kw)
    return modelstate.resolve([("reasoning", "LLM", "@openmaic")])["models"][0]


# --- the delegated branch ---------------------------------------------------------------------

def test_a_delegated_model_absent_locally_is_not_missing(monkeypatch):
    """The regression. Serving from the remote, absent here, must NOT read missing."""
    assert _state(monkeypatch, _roles())["state"] == modelstate.COLD


def test_a_delegated_model_the_remote_cannot_serve_is_missing(monkeypatch):
    """Honest in the other direction too: `installed: false` on a delegated row means the
    upstream did not answer or has nothing the pattern matches, and a role nothing will serve
    is missing."""
    assert _state(monkeypatch, _roles(installed=False))["state"] == modelstate.MISSING


def test_a_delegated_model_reported_resident_reads_loaded(monkeypatch):
    """The broker's `loaded` is residency on the REMOTE card, judged by the concrete name the
    glob expanded to there -- the only way a delegated chip can ever go green."""
    assert _state(monkeypatch, _roles(loaded=True))["state"] == modelstate.LOADED


def test_a_glob_the_remote_could_not_expand_is_missing_and_names_the_role(monkeypatch):
    """The broker's shape when nothing on the remote matches: `resolved` None, not installed.
    This box has a matching near-miss installed AND resident, and it must neither colour the
    chip nor be named on it -- the label falls back to the role."""
    out = _state(monkeypatch, _roles(resolved=None, serving=None, installed=False),
                 installed=("gemma3:4b", "mistral-small3.1:24b"),
                 loaded=("mistral-small3.1:24b",))
    assert out["state"] == modelstate.MISSING
    assert out["model"] == "@openmaic"


# --- the local reads must not be consulted for a delegated role -------------------------------

def test_a_resident_local_near_miss_cannot_turn_a_delegated_chip_green(monkeypatch):
    """This box has a near-miss the delegated glob would match here AND a copy of the very model
    the remote resolved to, both resident. The remote reports its own copy not resident, so the
    only honest answer is still cold -- never green off a local match."""
    out = _state(monkeypatch, _roles(),
                 installed=("gemma3:4b", "mistral-small3.1:24b", "mistral-small3.2:24b"),
                 loaded=("mistral-small3.1:24b", "mistral-small3.2:24b"))
    assert out["state"] == modelstate.COLD


def test_a_delegated_role_never_reads_warming(monkeypatch):
    """Warming means a job HERE is waiting on a model that is not resident HERE. A delegated
    role has no local job to wait on, and the queue that would justify orange is on the other
    box, so a stray local job matching its name must not colour the chip."""
    out = _state(monkeypatch, _roles(),
                 jobs=({"model": "mistral-small3.2:24b", "state": "waiting"},))
    assert out["state"] == modelstate.COLD


# --- what the chip NAMES ----------------------------------------------------------------------

def test_an_unexpanded_glob_is_reported_as_configured(monkeypatch):
    """An OLDER broker's shape: it sent the raw glob as `resolved`. With nothing local matching,
    the label is the pattern as configured."""
    roles = _roles(resolved="mistral-small3*:24b", serving="mistral-small3*:24b")
    assert _state(monkeypatch, roles)["model"] == "mistral-small3*:24b"


def test_a_concrete_remote_resolution_is_reported_as_such(monkeypatch):
    """This broker's shape: the glob was expanded against the REMOTE's inventory, so the chip
    names what will actually run -- not the pattern, and not something local that matches."""
    out = _state(monkeypatch, _roles(), installed=("gemma3:4b", "mistral-small3.1:24b"))
    assert out["model"] == "mistral-small3.2:24b"


# --- a single-broker platform is untouched ----------------------------------------------------

def test_local_roles_are_unaffected(monkeypatch):
    """The delegation branch must not change a platform with no upstreams. `@embed` resolves to
    a model that IS installed here and is not resident, so it stays cold via the original
    path."""
    _patch(monkeypatch, _roles(), installed=("gemma3:4b", "bge-m3:latest"))
    out = modelstate.resolve([("embed", "Retrieval", "@embed")])["models"][0]
    assert out["state"] == modelstate.COLD
    assert out["model"] == "bge-m3:latest"


def test_an_older_broker_omitting_upstream_behaves_exactly_as_before(monkeypatch):
    """Backward compatibility is load-bearing: a rail bundle can be newer than the broker it
    talks to. With no `upstream` key at all the local comparison must still decide, so a model
    genuinely absent here is still missing rather than silently excused as remote."""
    roles = [{"role": "openmaic", "pattern": "mistral-small3*:24b",
              "resolved": "mistral-small3.2:24b", "installed": True}]
    assert _state(monkeypatch, roles)["state"] == modelstate.MISSING


def test_upstream_named_local_is_treated_as_local(monkeypatch):
    """`local` is the broker's own name for this box, not a remote -- and it is what every
    non-delegated row now carries, so reading it as delegation would break the common case."""
    roles = _roles(upstream="local", resolved="gemma3:4b")
    assert _state(monkeypatch, roles)["state"] == modelstate.COLD


def test_an_empty_upstream_is_treated_as_local(monkeypatch):
    """Tolerance, not cleverness: an empty string is absence spelled differently."""
    roles = _roles(upstream="", resolved="gemma3:4b")
    assert _state(monkeypatch, roles)["state"] == modelstate.COLD
