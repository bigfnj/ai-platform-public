# RC021-RC024: the identity gate, the broker facade, the template's files, and the deskpet quips.
#
# A rule file of tools/rail_conformance.py. That module runs it into its own namespace rather
# than importing it (its "where the rules live" says why), so rule, F, S, Manifest, Finding,
# read, rel, REPO and the rest of what is used here without being defined here are its names.
# A comment rather than a docstring: a docstring would replace that module's, its --help text.
from __future__ import annotations

import ast
import re
from pathlib import Path


#: Dependency callables that really perform the platform-identity check, i.e. that answer a
#: header-less caller with 401, directly or through another of these. A route gated by anything
#: else (a feature flag, a rate limiter) is not gated at all, so accepting a bare `Depends(...)`
#: would pass exactly the routes this rule exists to catch. A rail that spells its dependency
#: some other way belongs in this set, not outside the rule.
#:
#: `require_owner` (finance) and `_identity` (job-aid) are here for a reason worth keeping: the
#: PUBLIC copy of this checker carries only the first three names, because finance and job-aid
#: are withheld from the public mirror and its author never met them. Running that set here
#: reds finance, which is correctly gated app-wide (an app-level `require_owner` dependency)
#: and 401s on both an absent and a blank header. A checker imported from a smaller tree
#: arrives pre-blinded, and a false alarm on a correct rail is how a checker gets switched off.
_IDENTITY_DEPS = frozenset({"identity", "_identity", "require_admin", "require_owner", "owner_id"})

#: The decorator methods that register a route on an app or a router.
_ROUTE_DECORATORS = frozenset({"get", "post", "put", "patch", "delete", "head", "options",
                               "trace", "route", "api_route", "websocket"})


def _depends_name(node: ast.expr) -> str:
    """The dependency inside a `Depends(x)` / `Depends(deps.x)` expression, or ""."""
    if isinstance(node, ast.Call) and _callee(node.func) == "Depends" and node.args:
        return _callee(node.args[0])
    return ""


def _gates(keywords: list[ast.keyword]) -> bool:
    """True when a `dependencies=[Depends(identity), ...]` keyword carries a real identity gate.

    Used for all three places FastAPI accepts one: the `FastAPI(...)` app, an `APIRouter(...)`,
    and an individual route decorator.
    """
    for kw in keywords:
        if kw.arg != "dependencies":
            continue
        elts = kw.value.elts if isinstance(kw.value, (ast.List, ast.Tuple)) else []
        if any(_depends_name(e) in _IDENTITY_DEPS for e in elts):
            return True
    return False


def _signature_gated(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when a handler takes `ident: X = Depends(identity)` (or any other _IDENTITY_DEPS)."""
    args = fn.args
    defaults = [*args.defaults, *[d for d in args.kw_defaults if d is not None]]
    return any(_depends_name(d) in _IDENTITY_DEPS for d in defaults)


def _status_route_gate(m: Manifest, srcs: dict[Path, str]) -> tuple[bool, bool, str]:
    """Is the manifest's `status_route` actually behind the identity gate?

    Returns (found, gated, where). `found` is False when no handler for that path could be
    located at all, which is a SILENT SKIP by design: a route registered some way this cannot
    read (a dynamically built path, a mounted sub-app) is a rail this tool cannot judge, and
    guessing there would produce the false alarm that gets a checker switched off.

    A route counts as gated when any of the four things FastAPI actually honours is present:
    an app-wide `FastAPI(dependencies=[Depends(identity)])` in the same module, a gated
    `APIRouter(dependencies=[...])` it is registered on, a `dependencies=[...]` on its own
    decorator, or an identity dependency in the handler's signature.
    """
    route = str(m.data.get("status_route") or "")
    found = gated = False
    where = ""
    for p, src in srcs.items():
        try:
            tree = _ast_parse(src)
        except SyntaxError:
            continue
        # An app-level gate is per MODULE, not per rail: a rail may mount a second FastAPI() for
        # a shim, and "some app in this rail is gated" would let an un-gated main app hide
        # behind it.
        app_gated = any(isinstance(n, ast.Call) and _callee(n.func) == "FastAPI"
                        and _gates(n.keywords) for n in ast.walk(tree))
        prefixes: dict[str, str] = {}
        gated_routers: set[str] = set()
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                    and _callee(n.value.func) == "APIRouter"):
                continue
            pfx = next((k.value.value for k in n.value.keywords if k.arg == "prefix"
                        and isinstance(k.value, ast.Constant)
                        and isinstance(k.value.value, str)), "")
            for t in n.targets:
                if isinstance(t, ast.Name):
                    prefixes[t.id] = pfx
                    if _gates(n.value.keywords):
                        gated_routers.add(t.id)
        for n in ast.walk(tree):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in n.decorator_list:
                if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr in _ROUTE_DECORATORS and dec.args):
                    continue
                first = dec.args[0]
                if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                    continue
                owner = _callee(dec.func.value)
                if prefixes.get(owner, "") + first.value != route:
                    continue
                found = True
                if (app_gated or owner in gated_routers or _gates(dec.keywords)
                        or _signature_gated(n)):
                    gated = True
                elif not where:
                    where = f"{rel(p)}:{n.lineno}"
    return found, gated, where


@rule("RC021", "A rail with an API fails closed on a missing platform identity.")
def rc021(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """The gateway authenticates every request and sets X-Platform-User, stripping any client
    copy. A request without it did not come through the gateway — on this platform that means a
    sibling container — so the only safe response is 401.

    Most of the rails got this wrong at once, in three flavours. Three had an INVERTED admin gate
    (`if user is not None and not is_admin: 403`), which rejects a named non-admin but waves
    through a caller with no header at all. Three defaulted the user to the string "?". Two had
    no identity code whatsoever. The worst case was workstation, which read the header only to
    label its audit line and then opened an SSH session on the host as Admin.

    Three things are checked, because each catches a different flavour:
      - a 401 exists for the header-less case
      - the escape hatch is the platform-wide PLATFORM_STANDALONE, not a per-rail name
      - the inverted predicate is gone, and Header(default="?") with it

    A rail with no FastAPI app is skipped; there is nothing to gate.
    """
    srcs = {p: read(p) for p in m.py_sources()}
    if not any("FastAPI(" in s for s in srcs.values()):
        return [S("RC021", m, "no FastAPI app — there is no surface to gate")]
    blob = "\n".join(srcs.values())
    out: list[Finding] = []

    # The header is spelled three ways depending on how it is read: the FastAPI parameter name
    # (x_platform_user), a raw header lookup (x-platform-user), or the canonical form in prose.
    reads_identity = re.search(r"x[_-]platform[_-]user", blob, re.I)
    if "401" not in blob or not reads_identity:
        out.append(F("RC021", m, "no 401 for a request with no X-Platform-User — a sibling "
                                 "container can call this rail directly", rel(m.path)))

    # The substring pass above is a floor, not the check. `blob` is every .py file in the rail
    # concatenated, so ANY rail containing the three characters "401" anywhere — a comment, an
    # unrelated status code, a docstring — satisfied it while serving its model state to any
    # container on the compose network. That is not hypothetical: two rails were doing exactly
    # that while this file reported `0 fail, 0 warn`. RC005's own docstring already makes this
    # argument ("a substring search over the source finds the name in a comment explaining the
    # variable and passes a rail that never reads it"); the lesson was learned there and not
    # carried across. So ask the question that matters — is THIS route gated — of the parse tree.
    route = str(m.data.get("status_route") or "")
    if route:
        found, gated, where = _status_route_gate(m, srcs)
        if found and not gated:
            out.append(F("RC021", m, f"the declared status_route {route} has no identity "
                                     f"dependency — a sibling container reads this rail's model "
                                     f"state with no X-Platform-User at all (gate it per-route "
                                     f"with Depends(identity), or app-wide via "
                                     f"FastAPI(dependencies=[Depends(identity)]))", where))

    for p, src in srcs.items():
        for i, raw in enumerate(src.splitlines(), 1):
            line = raw.split("#", 1)[0]
            if re.search(r'Header\(\s*default\s*=\s*[\"\']\?[\"\']', line):
                out.append(F("RC021", m, "identity defaults to \"?\" — a header-less caller is "
                                         "treated as a real user", f"{rel(p)}:{i}"))
            # Only an `if ...:` branch is the bug. The same predicate as an ASSIGNMENT is a
            # legitimate non-security use (recipe-book decides a contributor's category with
            # it), and it also appears inside docstrings describing the fix.
            if (line.lstrip().startswith("if ") and line.rstrip().endswith(":")
                    and re.search(r"user is not None and not .*is_admin", line)):
                out.append(F("RC021", m, "inverted admin gate: this rejects a NAMED non-admin "
                                         "but passes a caller with no identity at all",
                             f"{rel(p)}:{i}"))
            for legacy in ("EDU_STANDALONE", "IEP_STANDALONE", "AI_PLAYGROUND_STANDALONE",
                           "GEMINI_CX_STANDALONE", "SMB_PARTNER_STANDALONE"):
                if legacy in line:
                    out.append(F("RC021", m, f"uses the retired {legacy}; the escape hatch is "
                                             f"PLATFORM_STANDALONE on every rail",
                                 f"{rel(p)}:{i}"))
    return out


@rule("RC022", "The rail's broker facade exposes the canonical surface.")
def rc022(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """roles(), models() -> list[dict], status(), and a BrokerError.

    Required of a rail that carries a modelstate.py OR renders model chips (status_route plus a
    non-image slot), because that file is GENERATED against exactly this surface. When the
    surface varied, the resolver had to vary with it: the rails' copies of modelstate.py had grown
    into five distinct implementations of a function the contract calls identical everywhere.
    One of them unwrapped a dict because its rail's models() returned the UI picker shape
    under the same name. A chip rail with NO copy used to be skipped here as "does no model
    work", which is how one rail kept a sixth, hand-rolled one.

    The facade module is the rail's own broker.py unless the manifest declares `broker_module`
    (edu-suite reaches a platform package; job-aid's is called broker_status).
    """
    try:
        tpl = _rail_template()
    except Exception as exc:                      # noqa: BLE001 - tool missing is not a rail fault
        return [F("RC022", m, f"cannot load tools/rail_template.py ({exc})", "", level="warn")]
    rail = tpl.Rail(m.path)
    if rail.modelstate_path() is None:
        return [S("RC022", m, "renders no model chips and carries no modelstate.py, so it "
                              "needs no broker facade")]
    gaps = tpl.facade_gaps(rail)
    if not gaps:
        return []
    src = tpl.facade_module(rail)
    where = rel(src) if src else rel(m.path)
    return [F("RC022", m, f"broker facade is missing {gaps} — modelstate.py is generated "
                          f"against roles/models/status/BrokerError and cannot read this one",
              where)]


@rule("RC023", "Generated files match the rail template.")
def rc023(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """identity.py, modelstate.py and (where a rail carries one) logsetup.py are emitted by
    tools/rail_template.py and must match it byte for byte.

    A contract that only SAYS files are identical does not make them so — nothing was comparing
    the copies, and they drifted. Two identity.py copies differed solely in line endings, which
    no reviewer would ever see. `rail_template.py sync` adopts the template; changing the
    template and syncing is how a change to these files is made.

    A MISSING copy is a finding too. A rail that renders model chips must carry modelstate.py
    (rail_template.Rail.renders_chips decides that, once); before that, absence exempted a rail
    from this rule, so a hand-rolled resolver was invisible to the one check meant to catch it.
    """
    try:
        tpl = _rail_template()
    except Exception as exc:                      # noqa: BLE001
        return [F("RC023", m, f"cannot load tools/rail_template.py ({exc})", "", level="warn")]
    rail = tpl.Rail(m.path)
    out: list[Finding] = []
    for name, dest in tpl.invariants(rail):
        if not dest.is_file():
            out.append(F("RC023", m, f"{name} is missing — this rail renders model chips, so it "
                                     f"must carry the generated resolver; run "
                                     f"`python tools/rail_template.py sync --rail {m.id}`",
                         rel(dest)))
            continue
        want = tpl.render(name, rail)
        have = dest.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        if want != have:
            out.append(F("RC023", m, f"{name} has drifted from the template — run "
                                     f"`python tools/rail_template.py check --diff --rail {m.id}`",
                         rel(dest)))
    return out


def _deskpet_registered(lines_ts: str) -> set[str]:
    """Catalog ids present in the RAIL map in deskpet/lines.ts.

    The map mixes two JS forms: a quoted key for a hyphenated id ("recipe-book": recipeBook)
    and shorthand for a single-word one (bouquet,). Matching only the quoted form reported
    finance, iep and workstation as unregistered when they are registered.
    """
    body = lines_ts.split("const RAIL", 1)[-1]
    body = body.split("};", 1)[0]
    ids: set[str] = set()
    for raw in body.splitlines():
        line = raw.split("//", 1)[0].strip().rstrip(",")
        if not line:
            continue
        m = re.match(r'^["\'"]([^"\']+)["\']\s*:', line)
        if m:
            ids.add(m.group(1)); continue
        m = re.match(r"^([A-Za-z_][\w]*)$", line)
        if m:
            ids.add(m.group(1))
            continue
        # THIRD form: an unquoted key with an explicit value (`openmaic: openMaic,`). A
        # single-word id whose imported binding is camelCased cannot use JS shorthand, so this
        # is the only spelling available to it -- and it was being read as "not registered",
        # which is a false alarm on a correctly wired rail. A checker that cries wolf gets
        # switched off just as surely as one that never fires.
        m = re.match(r"^([A-Za-z_][\w]*)\s*:", line)
        if m:
            ids.add(m.group(1))
    return ids


@rule("RC024", "The rail has a deskpet quip bank, registered under its catalog id.")
def rc024(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """RAIL_CONTRACT step 6: a bank at deskpet/quips/<catalog-id>.json plus an entry in the
    RAIL map in deskpet/lines.ts.

    WARN, not FAIL. Four rails shipped without one (co-worker, gemini-cx, smb-partner,
    meeting-atlas) because nothing checked, and writing 100+ quips is not something to block a
    build on. Without the bank `pickIdle` falls through to the generic pool and `pickArrive`
    returns the bare label, so the rail is quietly less alive than its neighbours and no one
    can tell why.

    Banks key off the CATALOG id, not the directory — which is how ai-voice and iep-goals
    shipped without theirs even though their folders look right.
    """
    deskpet = SHELL / "deskpet"
    if not deskpet.is_dir():
        return []
    lines_ts = read(deskpet / "lines.ts") if (deskpet / "lines.ts").is_file() else ""
    out: list[Finding] = []
    for cid in m.catalog_ids():
        bank = deskpet / "quips" / f"{cid}.json"
        if not bank.is_file():
            out.append(F("RC024", m, f"no deskpet quip bank at deskpet/quips/{cid}.json — the "
                                     f"pet falls back to generic lines for this rail",
                         rel(deskpet / "quips"), level="warn"))
            continue
        if cid not in _deskpet_registered(lines_ts):
            out.append(F("RC024", m, f"quip bank {cid}.json exists but is not registered in the "
                                     f"RAIL map in deskpet/lines.ts, so it is never read",
                         rel(deskpet / "lines.ts"), level="warn"))
    return out
