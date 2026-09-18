"""Chip state for a role that runs on ANOTHER broker.

modelstate.py is a generated, byte-identical file shared by every rail, so this exercises the
template through openmaic's copy.

The bug it pins was live: `@openmaic` was delegated to a 4090 on the LAN, the broker resolved it
correctly to `mistral-small3.2:24b` and reported `installed: true`, and the chip still read
**missing** — because the resolver compared that name against THIS box's four installed models.
A red dot on a working rail is the same lie the four-state contract exists to prevent, just
pointing the other way, and it is worse than a wrong-but-plausible state because it sends someone
to pull a 15 GB model they already have somewhere else.
"""
from openmaic_app import modelstate


def _roles(**over):
    entry = {"role": "openmaic", "pattern": "offsite::mistral-small3*:24b",
             "upstream": "offsite", "resolved": "mistral-small3.2:24b",
             "installed": True, "loaded": False}
    entry.update(over)
    return [entry, {"role": "embed", "pattern": "bge-m3*", "upstream": "local",
                    "resolved": "bge-m3:latest", "installed": True}]


def _patch(monkeypatch, roles, installed=("gemma3:4b",), loaded=(), jobs=()):
    """The LOCAL reads stay deliberately small — the delegated model is not among them, which is
    the whole point."""
    monkeypatch.setattr(modelstate.broker, "roles", lambda: roles)
    monkeypatch.setattr(modelstate.broker, "models",
                        lambda: [{"name": n} for n in installed])
    monkeypatch.setattr(modelstate.broker, "status",
                        lambda: {"loaded": [{"name": n} for n in loaded], "jobs": list(jobs)})


def _state(monkeypatch, roles, **kw):
    _patch(monkeypatch, roles, **kw)
    out = modelstate.resolve([("reasoning", "LLM", "@openmaic")])
    return out["models"][0]


def test_a_delegated_model_absent_locally_is_not_missing(monkeypatch):
    """The regression. Installed on the remote, absent here, must NOT read missing."""
    assert _state(monkeypatch, _roles())["state"] == modelstate.COLD


def test_a_delegated_model_resident_on_the_remote_reads_loaded(monkeypatch):
    assert _state(monkeypatch, _roles(loaded=True))["state"] == modelstate.LOADED


def test_a_delegated_model_the_remote_does_not_have_is_missing(monkeypatch):
    """Still honest in the other direction: the upstream said it has not got it."""
    assert _state(monkeypatch, _roles(installed=False))["state"] == modelstate.MISSING


def test_the_reported_model_is_the_remote_resolution(monkeypatch):
    """The glob was resolved against the upstream's inventory, so the chip must name what will
    actually run — not the pattern, and not something local that happens to match."""
    assert _state(monkeypatch, _roles())["model"] == "mistral-small3.2:24b"


def test_local_roles_are_unaffected(monkeypatch):
    """The delegated branch must not change a single-broker platform. `@embed` resolves to a
    model that IS installed here and is not resident, so it stays cold via the original path."""
    _patch(monkeypatch, _roles(), installed=("gemma3:4b", "bge-m3:latest"))
    out = modelstate.resolve([("embed", "Retrieval", "@embed")])["models"][0]
    assert out["state"] == modelstate.COLD
    assert out["model"] == "bge-m3:latest"


def test_an_older_broker_omitting_upstream_behaves_exactly_as_before(monkeypatch):
    """Backward compatibility is load-bearing: a rail image can be newer than the broker it
    talks to. With no `upstream` key the local comparison must still decide, so a model that is
    genuinely absent here is still missing."""
    roles = [{"role": "openmaic", "pattern": "mistral-small3*:24b",
              "resolved": "mistral-small3.2:24b", "installed": True}]
    assert _state(monkeypatch, roles)["state"] == modelstate.MISSING


def test_upstream_named_local_is_treated_as_local(monkeypatch):
    """'local' is the broker's own name for this box, not a remote."""
    roles = _roles(upstream="local", resolved="gemma3:4b")
    assert _state(monkeypatch, roles)["state"] == modelstate.COLD
