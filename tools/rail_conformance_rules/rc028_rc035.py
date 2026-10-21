# RC028, RC029, RC033, RC030-RC032, RC034 and RC035, in the order they run: a test suite,
# root assets, the gateway's own compose block, routes hidden behind routes, unslotted
# models, the built bundle's global CSS, the docs routes, and the request-body ceiling.
#
# A rule file of tools/rail_conformance.py. That module runs it into its own namespace rather
# than importing it (its "where the rules live" says why), so rule, F, S, Manifest, Finding,
# read, rel, REPO and the rest of what is used here without being defined here are its names.
# A comment rather than a docstring: a docstring would replace that module's, its --help text.
from __future__ import annotations

import ast
import re
from functools import lru_cache


@rule("RC028", "The rail has a test suite.")
def rc028(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """RAIL_CONTRACT: a rail under contract carries its own tests.

    smb-partner-enablement and gemini-cx were imported from the public sibling on 2026-08-20
    and shipped with no tests/ directory at all. Nothing caught it for three weeks, because
    run-tests.ps1 builds its plan from the directories that EXIST -- a rail with no suite is
    not a failing target, it is a target that was never created. `-List` showed two targets
    fewer than there were rails and read as correct.

    That is the same shape as every other drift this file exists to catch: the contract already
    knows which rails exist, so "a rail under contract has tests" is checkable from the manifest
    rather than from whatever happens to be on disk.

    Discovery deliberately mirrors run-tests.ps1 (any dir named `tests`, minus the vendored
    trees) so a suite this rule accepts is a suite the runner will actually run. Requiring an
    actual test_*.py inside is the point: an empty tests/ directory would satisfy both this
    rule and the runner's plan while running nothing, which is exactly the vacuous-green state
    the rule is meant to prevent.
    """
    src = next((d for d in (m.root / "src", m.root / "backend") if d.is_dir()), None)
    if src is None:
        return []  # no backend to test (none today; keeps the rule honest if one appears)
    skip = {"native", "node_modules", ".venv", "__pycache__"}
    for d in m.root.rglob("tests"):
        if not d.is_dir() or skip & set(d.relative_to(m.root).parts):
            continue
        if any(d.rglob("test_*.py")):
            return []
    return [F("RC028", m, "no test suite -- run-tests.ps1 builds its plan from the directories "
                          "that exist, so this rail is not a failing target, it is a target "
                          "that was never created", rel(m.root))]


@rule("RC029", "Root-origin asset claims agree with the gateway, collide with nobody, "
               "and avoid reserved paths.")
def rc029(m: Manifest, allm: list[Manifest]) -> list[Finding]:
    """`root_assets` lets a rail serve paths OUTSIDE its own /<id>/ namespace.

    Only a rail that wraps a third-party app needs it: openmaic fronts an upstream Next.js app
    whose source carries absolute `<img src="/logos/...">` literals, and Next's basePath does not
    rewrite a string literal in JSX, so the browser resolves it against the origin root.

    Three things are checked, because each fails differently and none of them loudly:

      * The gateway's ROOT_ASSETS mirror must match the manifest. The gateway container cannot
        see rails/, so the list is necessarily duplicated; a drifted copy means the rail declares
        a path nothing routes, or the gateway routes a path the rail no longer serves.
      * No two rails may claim overlapping paths. The origin root is an exhaustible shared
        resource and a collision is SILENT -- one rail simply serves the other's images. This is
        the same failure RC002 exists for, one namespace over.
      * Nothing may claim a platform-reserved prefix, or a rail could shadow the shell's own
        /assets/ bundle or the /api/ surface for every user at once.

    Numbered 029 rather than 028, which it is called upstream: this repo's RC028 already means
    "the rail has a test suite", and an id is quoted in commit messages and passed to --rule.
    """
    mine = list(m.data.get("root_assets") or [])
    mirror = gateway_root_assets()
    out: list[Finding] = []
    where = rel(GATEWAY / "config.py")

    if mirror is None:
        if mine:
            out.append(F("RC029", m, "could not read the gateway's ROOT_ASSETS literal, so "
                                     "nothing here is verified — a checker/gateway mismatch, "
                                     "not a rail defect", where, level="warn"))
        return out
    if not mine and not mirror.get(m.id):
        return [S("RC029", m, "claims no root-origin assets, which is the normal case for a "
                              "rail that lives entirely under its own /<id>/")]

    declared = mirror.get(m.id, [])
    if sorted(declared) != sorted(mine):
        out.append(F("RC029", m, f"gateway ROOT_ASSETS has {sorted(declared)!r} for this rail "
                                 f"but the manifest declares {sorted(mine)!r}", where))

    for prefix in mine:
        for reserved in ("/api/", "/assets/", "/ws/"):
            if prefix == reserved.rstrip("/") or prefix.startswith(reserved):
                out.append(F("RC029", m, f"{prefix!r} claims the platform-reserved {reserved!r}",
                             rel(m.path)))
        # A rail's own /<id>/ is already its namespace; claiming it at the root is a mistake
        # that would shadow its own federated bundle.
        for other in allm:
            if prefix.startswith(f"/{other.id}/") or prefix == f"/{other.id}":
                out.append(F("RC029", m, f"{prefix!r} is inside a rail namespace "
                                         f"(/{other.id}/), which the gateway already routes",
                             rel(m.path)))

    # Collisions, checked against every OTHER rail's manifest.
    for other in allm:
        if other.id == m.id:
            continue
        for a in mine:
            for b in (other.data.get("root_assets") or []):
                if _root_prefix_overlaps(a, b):
                    out.append(F("RC029", m, f"root path {a!r} overlaps {b!r} claimed by "
                                             f"'{other.id}' — one rail would silently serve the "
                                             f"other's assets", rel(m.path)))
    return out


@rule("RC033", "The GATEWAY's own compose block restates the rail: url, dist env, dist mount, "
               "depends_on.")
def rc033(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """The four restatements that live in someone else's service block.

    RC001/RC003/RC004/RC011 cover the gateway's PYTHON registries and the rail's OWN compose
    service. Nothing covered the gateway SERVICE's block, which restates every rail four more
    times -- and those four are the ones that decide whether the rail is reachable in the
    deployed stack rather than merely declared.

    Found the hard way: watch-builder landed with all eight documented registration points
    correct, `rail_conformance.py` reporting 0 fail / 0 warn, 112 green rail tests, and a
    container verified end to end in isolation. It did not appear in the rail list at all,
    because:

      * PLATFORM_<ID>_DIST was absent, so the gateway fell back to the config default -- a
        HOST path (`RAILS / <id> / frontend / dist`) that does not exist inside the gateway
        container. `resolved_app_dists()` drops a missing dist silently by design, so the
        bundle was simply never served and nothing logged a word about it.
      * the read-only dist MOUNT was absent, so even the right env var would have pointed at
        nothing.
      * PLATFORM_APP_<ID>_URL was absent, so `app_urls()` used the config default of
        127.0.0.1:<port>, which inside the gateway container is the GATEWAY. The API proxy
        would have hit itself.
      * depends_on was absent, so the gateway could start before the rail.

    Each one fails silently and three of them fail as a 200 or a missing tile rather than an
    error. That is the exact shape this file exists for, one service block over.

    Only checked for ENABLED rails: a rail deliberately left out of `enabled_apps` has no
    business in the gateway's block, and demanding it there would make disabling a rail a
    conformance failure. That exemption is a counted skip, and a default this file cannot read
    is a warning. It used to search the tuple's text for `"<id>"`, and a default that was not a
    literal tuple gave no text, so every rail was exempted while the rule counted itself as
    having run.

    Each restatement is read as a whole LINE of the block: a commented-out
    `# PLATFORM_APP_<ID>_URL: ...` or `# - host:/app/<id>-dist:ro` satisfied the old checks (a
    substring test for the URL, a search for the line's END for the mount) while the stack ran
    without it.
    """
    src = read(COMPOSE)
    if not src:
        return []
    block = _compose_service(src, "gateway")
    if block is None:
        return [F("RC033", m, "no compose service named 'gateway' — this rule inspected "
                              "NOTHING", rel(COMPOSE), level="warn")]

    out: list[Finding] = []
    # Only rails the gateway is told to serve.
    cfg = gateway_config_text()
    if cfg:
        enabled = _enabled_app_ids(cfg)
        if enabled is None:
            out.append(F("RC033", m, "could not read the gateway's enabled_apps default as a "
                                     "literal tuple of ids, so this rail is checked as enabled "
                                     "rather than exempted unseen", rel(GATEWAY / "config.py"),
                         level="warn"))
        elif m.id not in enabled:
            return [S("RC033", m, "not in the gateway's enabled_apps, so the gateway's block "
                                  "has no reason to name it")]

    env = m.id.upper().replace("-", "_")

    url_key = f"PLATFORM_APP_{env}_URL"
    if _compose_env_value(block, url_key) is None:
        out.append(F("RC033", m, f"gateway compose block does not set {url_key!r} (absent, "
                                 f"commented out or blank), so the gateway would proxy "
                                 f"/{m.id}/api/* to its own 127.0.0.1:{m.container_port()}",
                     rel(COMPOSE)))

    # The dist env var and the dist MOUNT are checked against EACH OTHER rather than against a
    # guessed path. The container path is not always /app/<id>-dist: smb-partner-enablement
    # mounts at /app/smb-partner-dist, and an earlier version of this rule failed it for
    # having a perfectly correct pair. Reading the declared value and requiring a mount that
    # delivers it also catches the drift that actually bites -- an env var naming one path and
    # a mount providing another, which resolves to a silently unserved bundle.
    dist_key = f"PLATFORM_{env}_DIST"
    want = _compose_env_value(block, dist_key)
    if want is None:
        out.append(F("RC033", m, f"gateway compose block is missing {dist_key!r} — the "
                                 f"gateway falls back to a HOST path it cannot see, and "
                                 f"resolved_app_dists() drops a missing dist silently",
                     rel(COMPOSE)))
    elif not _compose_ro_mount(block, want):
        out.append(F("RC033", m, f"{dist_key} points at {want!r} but no read-only mount "
                                 f"delivers that path, so the bundle is never served",
                     rel(COMPOSE)))

    # depends_on is a list entry, so match it as one rather than as a substring: a bare
    # `m.id in block` would be satisfied by the dist mount path and never fail.
    if not re.search(rf"^[ \t]+-[ \t]+{re.escape(m.compose_service())}[ \t]*(?:#[^\n]*)?$",
                     block, re.M):
        out.append(F("RC033", m, f"gateway compose block does not depend_on "
                                 f"'{m.compose_service()}' — the gateway can start "
                                 f"before the rail", rel(COMPOSE)))
    return out


def _compose_env_value(block: str, key: str) -> str | None:
    """The value a mapping-form `environment:` line in a compose block gives `key`, without
    quotes or a trailing comment; None when no line sets it or the line is blank (a blank value
    passes the host's unset variable through, which is the same as not setting it here)."""
    hit = re.search(rf"^[ \t]+{re.escape(key)}:[ \t]*([^\s#][^\n]*?)[ \t]*(?:[ \t]#[^\n]*)?$",
                    block, re.M)
    if not hit:
        return None
    value = hit.group(1)
    if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value or None


def _compose_ro_mount(block: str, target: str) -> bool:
    """Is there a short-form volume entry in the block mounting something read-only AT
    `target`? A list entry, optionally quoted and with a trailing comment."""
    return bool(re.search(rf"^[ \t]+-[ \t]+(['\"]?)[^\s#'\"]+:{re.escape(target)}:ro\1"
                          rf"[ \t]*(?:#[^\n]*)?$", block, re.M))


@lru_cache(maxsize=4)
def _enabled_app_ids(cfg: str) -> frozenset[str] | None:
    """The ids in the gateway config's `enabled_apps` default, or None when that default is not
    a literal tuple or list of strings.

    Parsed rather than imported, like the rest of this file: the checker must run on a bare
    checkout with no gateway dependencies installed. Parsed rather than searched for `"<id>"`:
    the search read a default that was not a literal as naming no rail, and a single-quoted id
    as absent, and both of those exempt a rail without a word.
    """
    try:
        tree = _ast_parse(cfg)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            target = node.target
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        else:
            continue
        if not (isinstance(target, ast.Name) and target.id == "enabled_apps") or node.value is None:
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError, SyntaxError):
            return None
        if isinstance(value, (tuple, list)) and all(isinstance(v, str) for v in value):
            return frozenset(value)
        return None
    return None


_ROUTE_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
_PARAM = re.compile(r"\{([A-Za-z_]\w*)(?::(\w+))?\}")


def _route_matches(general: list[str], specific: list[str]) -> bool:
    """Does every URL `specific` routes also match `general`, which is registered first?
    Segment by segment: equal text, or a {param} in `general` that would accept `specific`'s
    literal there (Starlette's {x} and {x:str} take any one segment, {x:int} / {x:float} only
    digits, {x:path} the whole remainder). And at least one segment where `general` has the
    param and `specific` the literal, or the two are simply the same route."""
    widened = False
    for k, seg in enumerate(general):
        p = _PARAM.fullmatch(seg)
        if p and p.group(2) == "path":
            return len(specific) > k and any(not _PARAM.fullmatch(s) for s in specific[k:])
        if k >= len(specific):
            return False
        other = specific[k]
        if seg == other:
            continue
        if not p or _PARAM.fullmatch(other):
            return False
        conv = p.group(2)
        if conv == "int" and not other.isdigit():
            return False
        if conv == "float" and not re.fullmatch(r"\d+(\.\d+)?", other):
            return False
        widened = True
    return widened and len(general) == len(specific)


def shadowed_routes(m: Manifest) -> list[tuple[str, str]]:
    """[(earlier route, the route it hides)] in each of the rail's Python modules.

    Starlette matches routes in DECLARATION order, so `GET /api/jobs/{job_id}` registered above
    `GET /api/jobs/export.xlsx` answers the export request itself: `export.xlsx` is parsed as a
    `job_id` and the export handler is never reached (job-aid: an audit reported a 500 from an
    arity bug in a handler no request could reach; the real response was a 422). Read from the
    decorators, per router object (`app`, `router`, ...) within one module, which is where
    order is decided."""
    out: list[tuple[str, str]] = []
    for p in m.py_sources():
        try:
            tree = _ast_parse(read(p))
        except SyntaxError:
            continue
        routes: list[tuple[int, str, str, str]] = []  # (line, owner, method, path)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) \
                        and d.func.attr in _ROUTE_METHODS and d.args \
                        and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str):
                    routes.append((d.lineno, ast.unparse(d.func.value), d.func.attr, d.args[0].value))
        routes.sort()
        for i, (ln, owner, meth, path) in enumerate(routes):
            segs = path.strip("/").split("/")
            for ln2, owner2, meth2, path2 in routes[i + 1:]:
                if owner2 == owner and meth2 == meth and _route_matches(segs, path2.strip("/").split("/")):
                    out.append((f"{meth.upper()} {path} ({rel(p)}:{ln})",
                                f"{meth.upper()} {path2} (:{ln2})"))
    return out


@rule("RC030", "No route is hidden behind an earlier, more general route on the same router.")
def rc030(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    if not m.py_sources():
        return [S("RC030", m, "no Python sources, so no routes to order")]
    return [F("RC030", m, f"{first} is registered before {hidden}, and Starlette matches in "
                          f"declaration order, so the second is never reached: register the "
                          f"literal route above the parameterised one")
            for first, hidden in shadowed_routes(m)]


#: A model env var: `<anything>_MODEL`, the slot convention (RECIPE_BOOK_ASSISTANT_MODEL).
_MODEL_ENV = re.compile(r"[A-Z][A-Z0-9_]*_MODEL")


def unslotted_model_reads(m: Manifest) -> list[tuple[str, str, str]]:
    """[(env name, literal default, where)] for every `os.environ.get("<X>_MODEL", "<model>")`
    or `os.getenv(...)` in the rail's Python whose name is no declared slot's `env` and whose
    default is a concrete model rather than an @role or nothing."""
    slot_envs = {str(s.get("env")) for s in m.slots() if s.get("env")}
    out = []
    for p in m.py_sources():
        try:
            tree = _ast_parse(read(p))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and len(node.args) >= 2):
                continue
            f = ast.unparse(node.func)
            if f not in ("os.environ.get", "environ.get", "os.getenv", "getenv"):
                continue
            name, default = node.args[0], node.args[1]
            if not (isinstance(name, ast.Constant) and isinstance(name.value, str)
                    and _MODEL_ENV.fullmatch(name.value)):
                continue
            if not (isinstance(default, ast.Constant) and isinstance(default.value, str)):
                continue
            value = default.value.strip()
            if not value or value.startswith("@") or name.value in slot_envs:
                continue
            out.append((name.value, value, f"{rel(p)}:{node.lineno}"))
    return out


@rule("RC031", "Every model a rail reads is a declared slot or a declared, reasoned exemption.")
def rc031(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """RC013 holds a SLOT's in-code default to an @role. A model read outside every slot was
    invisible to it and to RC027: recipe-book's `RECIPE_BOOK_EMBED_MODEL` defaulted to the
    literal `bge-m3` and declared no `embed` slot, so a deployment pinned its embedder while
    every other rail followed `@embed`, and Admin -> Rails could not even show it. A model that
    cannot be an @role (a third-party cloud model; a checkpoint an index was built with) is
    declared in `unslotted_models` with the reason. Reads through
    pydantic-settings fields are not followed: every model read here is an environ.get.
    """
    exempt = m.data.get("unslotted_models") or []
    out: list[Finding] = []
    declared: dict[str, str] = {}
    for e in exempt if isinstance(exempt, list) else []:
        env = str((e or {}).get("env") or "") if isinstance(e, dict) else ""
        why = str((e or {}).get("why") or "").strip() if isinstance(e, dict) else ""
        if not env or not why:
            out.append(F("RC031", m, f"unslotted_models entry {e!r} needs both an env and a "
                                     f"why", rel(m.path)))
            continue
        declared[env] = why
    reads = unslotted_model_reads(m)
    for env, value, where in reads:
        if env not in declared:
            out.append(F("RC031", m, f"{env} defaults to the concrete model {value!r} outside "
                                     f"every model slot, so Admin -> Rails cannot show or repoint "
                                     f"it: declare a slot whose default is an @role, or list it "
                                     f"in unslotted_models with the reason it cannot be one", where))
    for env in sorted(set(declared) - {e for e, _v, _w in reads}):
        out.append(F("RC031", m, f"unslotted_models names {env}, which nothing in the rail "
                                 f"reads with a concrete default: a stale exemption", rel(m.path)))
    return out


@rule("RC032", "The built bundle's stylesheets hold no global rule (html, body, :root, #root, *, "
               "a bare element).")
def rc032(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """The deployed artefact, not the source. RC012 reads the rail's own stylesheets as written;
    this reads what the build actually emitted, dist/assets/*.css, which is what remoteEntry.js
    injects into the shell's document (with cssCodeSplit:false, every stylesheet of the bundle;
    with code splitting, a subset of them). Nothing removes it on navigation, so a global rule in
    it restyles the whole platform from the first visit to the rail until a reload. bouquet's
    bundle carried `html, body { background: var(--page) }`, `#root { padding: 20px }` and
    `:root { --page }` from a dev-only page sheet: the shell was inset 20px and stopped following
    the palette. That sheet came in through main.tsx, which RC012 never read, so the bundle is
    the place that cannot be argued with: a package's CSS, an index.html <style> and an import
    RC012 did not resolve all land here.

    Only rails with a built bundle are judged. dist/ is gitignored, so an unbuilt rail is
    skipped under the `unbuilt-tree` allowance, like RC016. A STALE bundle IS judged: it is what
    the gateway serves until someone rebuilds it, so a failure here after a source fix means
    "rebuild", and the message says so.
    """
    assets = m.frontend_src().parent / "dist" / "assets"
    if not (assets / "remoteEntry.js").is_file():
        return [S("RC032", m, f"no built bundle at {rel(assets)} (dist/ is gitignored): unbuilt, "
                              f"so there is no artefact to read", allowance="unbuilt-tree")]
    wrapper = str(m.data.get("css_wrapper") or "")
    out: list[Finding] = []
    for css in sorted(assets.glob("*.css")):
        hits = global_selectors(read(css), wrapper)
        if hits:
            shown = ", ".join(hits[:10]) + (f" ... and {len(hits) - 10} more" if len(hits) > 10 else "")
            out.append(F("RC032", m, f"the built {css.name} holds {len(hits)} global rule(s): "
                                     f"{shown}. remoteEntry.js injects it into the shell and nothing "
                                     f"removes it, so these restyle the whole platform. Find the "
                                     f"stylesheet that holds them (RC012 names a rail-owned one) and "
                                     f"keep it out of the build, then rebuild; if the source is "
                                     f"already fixed, this bundle is stale", rel(css)))
    return out


#: The FastAPI() keywords RC034 holds to None, with what each serves when left at its default.
_DOCS_OFF = (("openapi_url", "/openapi.json, the map of every route"),
             ("docs_url", "/docs, the interactive Swagger page"))


@rule("RC034", "Every FastAPI app a rail builds turns off its schema and docs.")
def rc034(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """FastAPI registers /openapi.json, /docs and /redoc itself, outside every route's own
    dependencies, so a rail gated per route served its whole API map to anything that could
    open a socket to it, and an app-level gate still answers 401 where a 404 would say nothing.
    Two rails still served them in October 2026 while every other rail had closed them: the
    contract's "set docs_url/openapi_url to None" had never been checked.

    Every `FastAPI(...)` / `fastapi.FastAPI(...)` call in the rail's Python is judged on its
    own (a rail can build a second app, as openmaic's LLM shim does): each of the two keywords
    must be the constant None. redoc_url is not required: FastAPI adds /redoc, like /docs, only
    when openapi_url is set, and the contract never named it. A value this rule cannot read (a
    name, a call, or `**kwargs` that may carry the keyword) is a warning, never a pass."""
    apps = 0
    out: list[Finding] = []
    for p in m.py_sources():
        try:
            tree = _ast_parse(read(p))
        except SyntaxError as exc:
            out.append(F("RC034", m, f"cannot read {rel(p)} ({exc.msg}), so a FastAPI app in it "
                                     f"was not checked", f"{rel(p)}:{exc.lineno or 0}",
                         level="warn"))
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _callee(node.func) == "FastAPI"):
                continue
            apps += 1
            where = f"{rel(p)}:{node.lineno}"
            given = {k.arg: k.value for k in node.keywords if k.arg is not None}
            spread = any(k.arg is None for k in node.keywords)
            for key, serves in _DOCS_OFF:
                value = given.get(key)
                if isinstance(value, ast.Constant) and value.value is None:
                    continue
                if value is None and spread:
                    out.append(F("RC034", m, f"cannot read {key}: this FastAPI(...) takes "
                                             f"**kwargs, which may or may not set it; pass "
                                             f"{key}=None by name", where, level="warn"))
                elif value is None:
                    out.append(F("RC034", m, f"FastAPI(...) leaves {key} at its default, so the "
                                             f"app serves {serves}, outside every route's "
                                             f"identity dependency: pass {key}=None", where))
                elif isinstance(value, ast.Constant):
                    out.append(F("RC034", m, f"FastAPI(...) sets {key}={value.value!r}, so the "
                                             f"app serves {serves}: pass {key}=None", where))
                else:
                    out.append(F("RC034", m, f"cannot read {key}={ast.unparse(value)}: only the "
                                             f"constant None can be checked; pass {key}=None",
                                 where, level="warn"))
    if not apps:
        return out or [S("RC034", m, "no FastAPI app in the rail's Python")]
    return out


#: The range RC035 holds `max_request_bytes` to. Below 1 MiB is a figure written in the wrong
#: unit (25 meaning MB), which would refuse every upload; above 1 GiB bounds nothing anyone needs
#: (the largest real figure is 256 MiB). The schema states the same two numbers.
MIN_REQUEST_BYTES = 1024 * 1024
MAX_REQUEST_BYTES_CAP = 1024 * 1024 * 1024


@rule("RC035", "A declared request-body ceiling is a whole number of bytes in range, and the "
               "gateway's mirror agrees for every tile the rail serves.")
def rc035(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """`max_request_bytes` is the most the gateway relays to this rail in one request body.

    The gateway streams every proxied body and refuses one past the rail's ceiling with a 413
    (up front when Content-Length says so), so the figure is a door the rail's own uploads must
    fit through. Three things fail silently, so each is checked:

      * a figure that is not a whole number (a string, a float, `true`), which the relay could
        not apply;
      * a figure outside 1 MiB-1 GiB: written in the wrong unit it refuses every upload, and
        too large it bounds nothing;
      * a gateway mirror (config.MAX_REQUEST_BYTES) that disagrees, for the rail's own id or
        any `also_serves` tile. The container cannot see rails/, so the mirror is what the relay
        applies: a figure raised here and not there still refuses the upload it was raised for.

    A rail that declares nothing gets the platform default, and must then have no mirror entry
    either; a stale one would hold it to a figure no manifest states. So must a tile listed in
    `also_serves_default_ceiling`, which takes the default rather than the rail's figure: one
    image can back a tile whose uploads are far smaller (edu-suite takes a 250 MiB folder, its
    iep tile one 25 MiB PDF), and the bigger figure left that tile's door ten times too wide.
    A name there that is not one of the rail's `also_serves` tiles fails: it holds nothing."""
    raw = m.data.get("max_request_bytes")
    out: list[Finding] = []
    also = m.catalog_ids()[1:]
    defaulted = m.data.get("also_serves_default_ceiling") or []
    if not isinstance(defaulted, list):
        out.append(F("RC035", m, f"also_serves_default_ceiling must be a list of tile ids, not "
                                 f"{defaulted!r}", rel(m.path)))
        defaulted = []
    for tile in defaulted:
        if tile not in also:
            out.append(F("RC035", m, f"also_serves_default_ceiling names {tile!r}, which is not "
                                     f"one of this rail's also_serves tiles", rel(m.path)))
    if raw is not None:
        if isinstance(raw, bool) or not isinstance(raw, int):
            out.append(F("RC035", m, f"max_request_bytes must be a whole number of bytes, not "
                                     f"{raw!r}", rel(m.path)))
        elif not MIN_REQUEST_BYTES <= raw <= MAX_REQUEST_BYTES_CAP:
            out.append(F("RC035", m, f"max_request_bytes {raw} is outside {MIN_REQUEST_BYTES}-"
                                     f"{MAX_REQUEST_BYTES_CAP} (1 MiB-1 GiB): too small refuses "
                                     f"every upload, too large bounds nothing", rel(m.path)))
    mirror = gateway_max_request_bytes()
    where = rel(GATEWAY / "config.py")
    if mirror is None:
        if raw is not None:
            out.append(F("RC035", m, "could not read the gateway's MAX_REQUEST_BYTES literal, so "
                                     "the ceiling the relay applies is not verified", where,
                         level="warn"))
        return out
    ids = m.catalog_ids()
    if raw is None and not any(i in mirror for i in ids):
        return out or [S("RC035", m, "declares no request-body ceiling, so the gateway applies "
                                     "its platform default (PLATFORM_MAX_REQUEST_BYTES)")]
    for app_id in ids:
        got = mirror.get(app_id)
        held = None if app_id in also and app_id in defaulted else raw
        if got != held:
            have = "no entry" if got is None else repr(got)
            want = ("the platform default (also_serves_default_ceiling)" if held is None and raw
                    else "none" if held is None else repr(held))
            out.append(F("RC035", m, f"gateway MAX_REQUEST_BYTES has {have} for '{app_id}' but "
                                     f"the manifest declares {want}", where))
    return out
