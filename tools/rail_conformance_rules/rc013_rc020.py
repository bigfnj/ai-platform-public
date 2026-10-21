# RC013, RC014, RC016, RC015 and RC017-RC020, in the order they run: model slots name a
# role, compose's token names, the gateway's dist default, status polling, RailHeader, the
# student-identifier quarantine, the Rail Manager's copy, and shared code reaching into a
# rail.
#
# A rule file of tools/rail_conformance.py. That module runs it into its own namespace rather
# than importing it (its "where the rules live" says why), so rule, F, S, Manifest, Finding,
# read, rel, REPO and the rest of what is used here without being defined here are its names.
# A comment rather than a docstring: a docstring would replace that module's, its --help text.
from __future__ import annotations

import json
import subprocess
import re
from pathlib import Path


@rule("RC013", "Model slots reference an @role, so Admin -> Rails stays authoritative.")
def rc013(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """A rail that pins a concrete model name silently ignores the admin panel: the admin
    repoints the role, the rail keeps using the pin, and nothing reports the disagreement.
    Checks the IN-CODE default, not the compose override — a compose-only fix leaves
    standalone dev pinned, which is how this class of bug survived its last cleanup.

    Set pinned_default_ok on the slot (with a note) for a deliberate exception.
    """
    out: list[Finding] = []
    blob = {p: read(p) for p in m.py_sources()}
    prefix = str(m.data.get("env_prefix") or "")
    for s in m.slots():
        if s.get("pinned_default_ok"):
            continue
        env, role = str(s.get("env") or ""), str(s.get("role") or "")
        if not env:
            continue
        # A slot may name SEVERAL env vars ("A + B"), which bouquet's writer slot does with
        # a note claiming "the @role rule is checked against the pair". It was not: the raw
        # string went straight into both regexes, neither could match it, and the slot was
        # skipped in silence. Each name is now checked on its own.
        names = [n.strip() for n in env.split("+") if n.strip()]
        matched = 0
        for name in names:
            # removeprefix, not a slice. `env[len(prefix):]` assumes the var STARTS with the
            # rail's prefix; edu-suite declares env_prefix EDU_SUITE_ and a var EDU_LLM_MODEL,
            # so the slice produced attr="del" and the pydantic branch below could never match
            # anything. It survived only because that rail happens to use the os.environ form.
            attr = (name.removeprefix(prefix) if name.startswith(prefix) else name).lower()
            want = f"@{role}" if role else ""
            for p, src in blob.items():
                # pydantic-settings style:  synthesis_model: str = "@co-worker-synthesis"
                for hit in re.finditer(
                        rf"^\s*{re.escape(attr)}:\s*str\s*=\s*\"([^\"]*)\"", src, re.M):
                    matched += 1
                    out += _rc013_check(m, s, hit.group(1), want, name, rel(p))
                # os.environ style:  MODEL = os.environ.get("GEMINI_CX_RAG_MODEL", "@gemini-cx-rag")
                # os.getenv is the same thing and was the blind spot: matching only environ.get
                # meant this rule read 0 findings while edu-suite sat on four live pinned models.
                for hit in re.finditer(
                    rf"os\.(?:environ\.get|getenv)\(\s*[\"']{re.escape(name)}[\"']\s*,"
                    rf"\s*[\"']([^\"']*)[\"']",
                    src,
                ):
                    matched += 1
                    out += _rc013_check(m, s, hit.group(1), want, name, rel(p))
        if not matched:
            # ANNOUNCE it. A slot this rule could not find a default for is not a slot that
            # passed; measured before this change, 1 of 18 slots was skipped this way and
            # nothing said so.
            out.append(F("RC013", m, f"slot {s.get('slot')!r} declares env {env!r} but no "
                                     f"in-code default matching it was found, so this slot "
                                     f"was NOT checked", rel(m.path), level="warn"))
    return out


def _rc013_check(m: Manifest, s: dict, got: str, want: str, env: str, where: str) -> list[Finding]:
    """One default, against the role the manifest declares for it.

    The role was previously READ and interpolated into the message as "(expected '@x')" and
    then never compared: the only condition was `startswith("@")`. So a rail defaulting to a
    DIFFERENT @role passed while the message claimed to know better. Live instance at the
    time: bouquet defaulted three slots to generic roles where its manifest declares
    rail-specific ones, compose corrected them, and standalone dev was left wrong, which is
    precisely the split this rule's docstring says it exists to catch.
    """
    if not got.startswith("@"):
        return [F("RC013", m, f"slot {s.get('slot')!r} default for {env} is {got!r}, "
                              f"not an @role"
                              + (f" (expected {want!r})" if want else ""), where)]
    if want and got != want:
        return [F("RC013", m, f"slot {s.get('slot')!r} default for {env} is {got!r} but the "
                              f"manifest declares {want!r}; compose may correct this, which "
                              f"leaves standalone dev pointing at the wrong role", where)]
    return []


@rule("RC014", "Every compose file passes the broker token under a canonical unprefixed name.")
def rc014(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """RC005 checks what the rail READS; this checks what deploy/ WRITES.

    Either canonical spelling counts (TOKEN_ENV_NAMES): the value in BROKER_AUTH_TOKEN, or a
    path in BROKER_AUTH_TOKEN_FILE. This rule is the other half of the same 2026-09-10 change
    — teaching RC005 the file form and not this one would mean the compose file that switches
    over reports "passes no broker token" for all nine services, which is the opposite of what
    happened. Note the regex below must not let BROKER_AUTH_TOKEN_FILE satisfy a check for
    BROKER_AUTH_TOKEN by prefix: the names are captured whole and compared as a set.

    Both matter, and only together. co-worker read the token solely as
    CO_WORKER_BROKER_AUTH_TOKEN, so whoever wired the installer compose bent that file to spell
    it the same way — which made the rail work there and silently tokenless under
    deploy/docker-compose.yml, where all nine services get the unprefixed name. Fixing the rail
    without fixing the compose file just moves the disagreement.

    A rail with no model slots needs no token and is skipped. So is a service that pulls the
    whole gitignored deploy/.env via `env_file:` — that file is where BROKER_AUTH_TOKEN
    actually lives, so those services get it without naming it, and flagging them would be a
    false alarm on a rail that authenticates correctly today.
    """
    if not m.slots():
        return []
    canonical = set(TOKEN_ENV_NAMES)
    out: list[Finding] = []
    for cf in (COMPOSE, REPO / "deploy" / "installer" / "docker-compose.installer.yml"):
        src = read(cf)
        if not src:
            continue
        block = _compose_service(src, m.compose_service())
        if block is None:
            continue  # RC011 owns "the service is missing"; a profile-gated file may omit it
        if re.search(r"^\s*env_file:", block, re.M):
            continue  # inherits the whole .env, token included
        # The prefix group must be OPTIONAL and must itself end in '_'. Written as
        # [A-Z][A-Z0-9_]*BROKER_AUTH_TOKEN it consumes a character before the literal, so the
        # bare canonical name never matches and every correctly-wired service reports as
        # passing no token at all — nine false alarms, which is how a rule gets ignored.
        names = set(re.findall(r"^\s+((?:[A-Z][A-Z0-9_]*_)?BROKER_AUTH_TOKEN(?:_FILE)?):",
                               block, re.M))
        if not names:
            out.append(F("RC014", m, f"service passes no broker token, so the rail cannot "
                                     f"authenticate to the broker once one is enforced",
                         rel(cf), level="warn"))
        elif not (names & canonical):
            out.append(F("RC014", m, f"service passes the token as {sorted(names)[0]} rather "
                                     f"than a canonical {' / '.join(TOKEN_ENV_NAMES)}", rel(cf)))
    return out


@rule("RC016", "The gateway's <id>_dist default names this rail's own build output, and a "
               "built one holds assets/remoteEntry.js.")
def rc016(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """`resolved_app_dists()` SKIPS a rail whose dist is missing rather than failing — its own
    docstring says "the app just won't load its bundle". Combined with a stale default, that is
    a rail serving nothing, silently.

    Which is what had happened: five defaults still pointed at pre-monorepo standalone
    directories that no longer existed, so a host-native gateway resolved barely half the rails. Nobody
    noticed because compose overrides every one of these with PLATFORM_*_DIST, so containers
    were fine — the value only matters for standalone dev, which is exactly where nobody looks.

    Checks the DEFAULT in config.py, deliberately, not the runtime value. The runtime value is
    whatever the deployment sets; the default is the thing that rots.

    Reports the directory rather than remoteEntry.js: a rail that simply has not been built yet
    is a normal state, and failing on that would make the checker cry wolf on a fresh clone.

    ⚠ That last paragraph was the INTENT and not the behaviour until 2026-09-17. Checking the
    directory instead of the bundle is not enough, because `dist/` is gitignored: in a tree
    where nothing has been built the directory does not exist either, so the rule failed on
    every rail. It had been failing the `public-snapshot-dryrun.yml` merge gate on EVERY push
    for at least a month -- gates 1-6 clean, gate 7 nine RC016 fails -- and a merge gate that
    is always red is not a merge gate, because nobody can tell a real leak from the standing
    failure. `--verify-build` does not save it: `build_shell()` builds `apps/platform/frontend`
    and no rail frontend.

    ⚠ The fix after that was COMPARATIVE ("a rail missing its dist while siblings have theirs
    is rotted"), and it failed every unbuilt rail the moment any one rail was built: a partial
    local build (a branch building the rails it was fixing) made `--strict --allow unbuilt-tree`
    fail on nine rails whose defaults were fine. Whether a SIBLING is built says nothing about
    this rail's default.

    So the question is now asked of each default on its own, by its shape:
      * ROTTED, fail: the default is not inside this rail's own directory, or the directory
        that should hold the build output does not exist. That is the pre-monorepo path, true
        whatever is or is not built.
      * UNBUILT, skip under the `unbuilt-tree` allowance: the default names a build-output
        directory inside the rail whose parent exists, and nothing has been built there yet.
      * BROKEN, fail: the directory exists but has no `assets/remoteEntry.js`, which is the one
        file `resolved_app_dists()` requires, so the gateway skips the rail without an error.
    A STALE dist (older than its sources) is not judged: dist is untracked, so every pull leaves
    a valid build looking older than the files it was built from, and a rule that cried wolf on
    each one would be switched off.
    """
    cfg_path = GATEWAY / "config.py"
    # Read the MODEL's defaults, not the source text. The old regex needed a quoted literal
    # and every default is now `str(RAILS / ... )`, so it matched nothing and this rule
    # skipped every rail while reporting green. Measured before this change: 0 inspected, every
    # rail skipped.
    defaults, why = gateway_field_defaults()
    if defaults is None:
        # ANNOUNCE the degradation. Returning [] here is what made the old shape dangerous:
        # "I could not look" and "I looked and it is fine" printed identically.
        return [F("RC016", m, f"cannot read the gateway settings to check dist defaults "
                              f"({why}); this rule inspected NOTHING", rel(cfg_path),
                  level="warn")]
    out: list[Finding] = []
    unbuilt: list[str] = []
    inspected = 0
    root = m.root.resolve()
    for app_id in m.catalog_ids():
        ident = app_id.replace("-", "_")
        if f"{ident}_dist" not in defaults:
            continue  # RC004 owns "the setting is missing"
        raw = defaults[f"{ident}_dist"]
        if not raw:
            continue  # an empty default is "resolve at runtime", not a rotted path
        inspected += 1
        dist = Path(str(raw))
        inside = dist.resolve().is_relative_to(root)
        if not inside or not dist.parent.is_dir():
            where = "outside this rail's directory" if not inside else \
                f"under {rel(dist.parent)!r}, which does not exist"
            out.append(F("RC016", m, f"{ident}_dist default points at {str(raw)!r}, {where}: a "
                                     f"rotted default. resolved_app_dists() will skip this rail "
                                     f"and it will serve no bundle, without an error",
                         rel(cfg_path)))
        elif not dist.is_dir():
            unbuilt.append(f"{ident}_dist")
        elif not (dist / "assets" / "remoteEntry.js").is_file():
            out.append(F("RC016", m, f"{ident}_dist {rel(dist)!r} exists but holds no "
                                     f"assets/remoteEntry.js, the file resolved_app_dists() "
                                     f"requires: a broken or partial build, so the gateway "
                                     f"skips this rail without an error", rel(dist)))
    if unbuilt and not out:
        # One skip per rail, so the coverage count stays one per rail. Unbuilt is a fact about
        # the checkout (a fresh clone, a publish snapshot, a rail nobody has built yet), not a
        # defect in it, whatever the OTHER rails' build state.
        return [S("RC016", m, f"{', '.join(unbuilt)} names this rail's own build output, which "
                              f"has not been built in this checkout: unbuilt, not rotted",
                  allowance="unbuilt-tree")]
    if m.catalog_ids() and not inspected:
        # Same reasoning as the import guard: a rail whose default this rule could not find
        # must say so rather than count as clean.
        out.append(F("RC016", m, "no *_dist default was found for any of this manifest's "
                                 "catalog ids, so nothing was checked", rel(cfg_path),
                     level="warn"))
    return out


@rule("RC015", "A rail with a status_route polls it, so its chips cannot freeze.")
def rc015(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """The four states describe LIVE residency, and residency changes with nobody touching
    the UI: the broker evicts on a keep_alive expiry, and asking a question warms a model
    back up. So a one-shot fetch on mount renders a state that is right for about a second
    and silently wrong afterwards.

    LIVE, and not inert. This used to say "every rail on this platform sets status_route:
    null, because per-rail chips are a deferred follow-on". That stopped being true when the
    chips landed and the sentence was never updated: measured 2026-09-15 across the fleet, all
    but a few manifests declare a real route (`/api/capabilities`, or co-worker's
    `/api/models`), and meeting-atlas and workstation are among the few that are null.

    Worth correcting rather than leaving, because "inert" is an instruction to stop reading:
    anyone triaging a finding from this rule would have dismissed it. It also makes RC017's
    aside about RC007/RC008 already owning chip correctness true, which the inert framing
    would have denied.

    It is not hypothetical. The sibling public repo shipped a rail that fetched its status
    route once in a mount effect with an empty dep array and never again; its chips sat on
    `cold` for a model that was loaded and actively answering, while the shell's own top-bar
    widget, which does poll, correctly showed it resident. Every other rule passed on that
    rail, because the payload SHAPE was perfect. Liveness is a property of the caller, not the
    envelope, so no amount of shape-checking would ever have caught it.

    Deliberately narrow. It looks at the files that touch the status route (directly or via
    an accessor exported from the rail's api module) and only complains when NOT ONE of them
    sets an interval or calls web-core's usePoll / startPoll. A rail that polls by some other mechanism, or whose fetch site this
    misses, is a silent pass — a false negative, which is the side to err on.
    """
    route = str(m.data.get("status_route") or "")
    if not route:
        return []
    srcs = {p: read(p) for p in m.ts_sources()}
    if not srcs:
        return []

    # The accessor(s) wrapping the route. Two shapes are in use and both must be found:
    #   export const capabilities = () => getJSON('/api/capabilities')   (gemini-cx)
    #   export const api = { capabilities: () => getJSON('/api/capabilities'), ... }  (bouquet)
    # Matching only the first meant bouquet and recipe-book looked like they never polled --
    # they both do, at 6s, from module.tsx. The rule reported two false positives the moment
    # their manifests stopped saying status_route: null.
    accessors: set[str] = set()
    for src in srcs.values():
        for hit in re.finditer(
            rf"export\s+(?:const|function)\s+(\w+)[^\n]*{re.escape(route)}", src
        ):
            accessors.add(hit.group(1))
        for hit in re.finditer(rf"(\w+)\s*:\s*\([^)]*\)\s*=>[^\n]*{re.escape(route)}", src):
            accessors.add(hit.group(1))

    # Every file that either names the route or calls one of its accessors. The polling has
    # to live in one of them. `.name(` as well as `name(`, since the object shape is called
    # as a method.
    touching = {
        p for p, src in srcs.items()
        if route in src or any(re.search(rf"(?:\.|\b){a}\s*\(", src) for a in accessors)
    }
    if not touching:
        return []
    # A raw setInterval, or web-core's shared poller (usePoll / startPoll, web/src/poll.ts),
    # which is how the shell and most rails poll now: it skips a hidden tab and never
    # overlaps a slow answer. Read as a CALL, so an import naming it is not a poll.
    if any("setInterval" in srcs[p] or re.search(r"\b(?:usePoll|startPoll)\s*\(", srcs[p])
           for p in touching):
        return []
    where = sorted(touching, key=lambda p: (p.name != "module.tsx", str(p)))[0]
    return [F("RC015", m, f"fetches {route} but nothing polls it (setInterval, usePoll, "
                          f"startPoll), so the chips "
                          f"render their page-load state forever — a model that loads "
                          f"afterwards keeps reporting the state it had at mount",
              rel(where))]


@rule("RC017", "The rail's frontend renders the shared RailHeader from @web-core.")
def rc017(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """One header for every rail: icon · bold title · muted subtext · model chips · a rule.
    It lives in web/src/RailHeader.tsx (+ ModelChips) so it stays identical everywhere; see
    RAIL_CONTRACT.md. A rail that hand-rolls its own header is exactly the drift this whole
    contract exists to stop, so require the shared component rather than trusting each rail to
    reproduce the shape.

    Checks for BOTH the import from '@web-core' and a `<RailHeader` usage, so re-exporting the
    name without rendering it does not pass. Chips are NOT required here — a rail with no
    foreground model (workstation, ai-voice) legitimately renders <RailHeader> without them;
    RC007/RC008 already own chip correctness.

    Relocated frontends ARE covered as of the frontend_src fix: edu-suite serves its two tiles
    from apps/dashboard/frontend/src, which this rule could not see, so it skipped the rail
    entirely and reported nothing — a coverage hole that read exactly like a pass.
    """
    srcs = m.ts_sources()
    if not srcs:
        return [S("RC017", m, "no TypeScript sources — nothing renders a header yet")]
    # PER FILE, not over the concatenation. Joining every .tsx in the rail meant the import
    # could sit in one file and the `<RailHeader` in another, and either could be dead code:
    # a rail that imported the name in types.ts and rendered a hand-rolled header everywhere
    # else passed. Requiring ONE file to do both is what the rule always meant.
    for p in srcs:
        text = read(p)
        if "RailHeader" in text and "@web-core" in text and "<RailHeader" in text:
            return []
    return [F("RC017", m, "frontend does not use the shared RailHeader from '@web-core' — every "
                          "rail's header must be <RailHeader> (web/src/RailHeader.tsx) so the "
                          "icon / title / subtext / chips / rule stay identical; see "
                          "RAIL_CONTRACT.md", rel(m.frontend_src()))]


@rule("RC018", "No quarantined student identifier appears in any tracked file.")
def rc018(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """The iep rail holds records about real children. `local/` keeps the SOURCE documents out of
    git, but nothing stopped the NAMES inside them walking out into code — and they did: golden
    assertions, unit fixtures copied from a real case, and edge-case comments naming whose sheet
    exhibited the bug. Fixed 2026-08-23; this rule is what stops it coming back, because the leak
    is a natural by-product of debugging against real data, not carelessness.

    Repo-wide by design, though it is registered against one rail: a name can land in any file.
    It runs once (on the owning rail) and scans every tracked path.

    The identifiers are read from a GITIGNORED pseudonyms file in that rail's local/, so the
    checker itself never contains them. Two consequences, both deliberate:
      * on a clean checkout the guard cannot run, and says so as a WARN rather than passing
        silently — a check that quietly does nothing is worse than no check;
      * a finding reports FILE and LINE only, never the matched text, because echoing the name
        into a terminal or CI log just moves the leak.

    Matching is case-SENSITIVE, so an ordinary lowercase noun that happens to also be one of the
    surnames does not trip the capitalised name. (This docstring deliberately does not spell out
    an example: the publish gate scans case-INSENSITIVELY, and an illustrative surname here would
    fail that scan — the same "do not restate the identifier" rule this rule itself enforces.)
    Add genuine exceptions to "scan_allow" in the pseudonyms file rather than loosening this.
    """
    if m.id != "iep-goals":          # run once, not once per rail
        return []
    names_file = REPO / "rails" / "iep" / "local" / "pseudonyms.json"
    if not names_file.is_file():
        return [F("RC018", m, "cannot verify: rails/iep/local/pseudonyms.json is absent, so the "
                              "student-identifier scan did NOT run (expected on a clean checkout; "
                              "on the box that holds the data, restore it)", "", level="warn",
                  allowance="pseudonyms-absent")]
    try:
        spec = json.loads(names_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [F("RC018", m, f"cannot verify: pseudonyms.json is unreadable ({exc.__class__.__name__})",
                  rel(names_file))]

    tokens: set[str] = set()
    for row in spec.get("students", []):
        real = str(row.get("real", "")).strip()
        if not real:
            continue
        tokens.add(real)                                   # "Last, First" - always
        surname, _, first = real.partition(",")
        surname, first = surname.strip(), first.strip()
        if len(surname) > 2:
            tokens.add(surname)                            # the identifying half
        # A bare first name is weak identification on its own, and some collide with ordinary
        # proper nouns elsewhere in the monorepo (a vendor product line, say). Scanning those
        # produces noise that trains you to ignore the rule, so a student may set
        # "scan_first_name": false and rely on the full-name + surname tokens instead.
        if len(first) > 2 and row.get("scan_first_name", True):
            tokens.add(first)
    allow = [a for a in spec.get("scan_allow", []) if a]
    # Path prefixes exempted wholesale, for subtrees of third-party product documentation
    # where a first name collides with an unrelated vendor proper noun. Keep these NARROW:
    # a broad prefix silently switches the guard off for a whole subtree.
    allow_paths = [a for a in spec.get("scan_allow_paths", []) if a]
    if not tokens:
        return [F("RC018", m, "cannot verify: pseudonyms.json lists no students", rel(names_file))]

    try:
        tracked = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True,
                                 check=True).stdout.decode("utf-8", "replace").split("\0")
    except (OSError, subprocess.CalledProcessError) as exc:
        return [F("RC018", m, f"cannot verify: git ls-files failed ({exc.__class__.__name__})", "")]

    out: list[Finding] = []
    for relpath in tracked:
        if not relpath:
            continue
        if any(relpath.startswith(a) for a in allow_paths):
            continue
        path = REPO / relpath
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue                                       # binary or unreadable: nothing to leak
        if not any(t in text for t in tokens):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if any(a in line for a in allow):
                continue
            if any(t in line for t in tokens):
                # Name deliberately omitted: see the docstring.
                out.append(F("RC018", m, "a quarantined student identifier appears here — replace "
                                         "it with its pseudonym from rails/iep/local/pseudonyms.json "
                                         "(real names belong only in local/)", f"{relpath}:{n}"))
    return out


@rule("RC019", "The rail declares user-facing copy for the admin Rail Manager.")
def rc019(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """Every rail needs a `description`: one or two sentences a person choosing whether to enable
    it would actually read.

    It is a separate field from the three that already exist because each of those answers a
    different question and none answers this one. `label` is a tile caption ("Voice Studio"),
    `notes` is developer caveats (9p rename semantics, port collisions), and a rail's
    `RailHeader subtitle` lives inside the federated bundle — which the catalog cannot reach,
    since the whole point is describing rails that are NOT currently mounted.

    Length is bounded on both sides on purpose. Too short and it restates the label; too long and
    a card grid turns into an essay. The schema enforces 40-400; this rule makes a MISSING one
    fail, which the schema cannot (it is an optional property so old manifests keep validating).
    """
    desc = m.data.get("description")
    if not desc:
        return [F("RC019", m, "manifest has no 'description' — the Rails Catalog would show this "
                              "rail with no explanation of what it is for", rel(m.path))]
    if not isinstance(desc, str) or not (40 <= len(desc) <= 400):
        return [F("RC019", m, f"'description' should be 40-400 characters of user-facing copy "
                              f"(got {len(desc) if isinstance(desc, str) else type(desc).__name__})",
                  rel(m.path))]
    if desc.strip().lower().startswith(str(m.data.get("label", "")).lower() + " "):
        return [F("RC019", m, "'description' just restates the label; say what the rail is FOR",
                  rel(m.path), level="warn")]
    return []


@rule("RC020", "No shared component reaches into this rail's tree by name.")
def rc020(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """The dependency arrow points one way: a rail may use the broker and the shell, and neither
    may use a rail. A shared component that hardcodes a path into rails/<id> makes that rail
    load-bearing for the whole platform, which is the opposite of a pluggable component.

    This is not hypothetical. The broker used to resolve edu_media_core out of
    the edu-suite rail's packages/, so removing edu-suite would have broken image generation and
    XTTS for every rail — recipe-book icons and bouquet vision included, neither of which has
    heard of edu-suite. That one moved to packages/edu-media-core. The broker still reaches
    into the ai-voice rail's native/ for the host-native voice engines, which is the remaining warn.

    Scope is deliberate. `deploy/` is EXEMPT: orchestrating rails is exactly its job, and every
    compose build context and dist mount names a rail legitimately. Generic enumeration is fine
    too — the gateway's `RAILS = REPO_ROOT / "rails"` discovers rails without naming one, which
    is how a shell is supposed to work. Only naming a SPECIFIC rail is a finding.

    Comments are stripped first, so an explanatory reference does not fail the rule.

    FAIL as of 2026-08-24: all three original violations are gone. edu_media_core and the XTTS
    clips moved to packages/edu-media-core, and the voice engines' location became deployment
    configuration (BROKER_VOICE_ENGINES_DIR) rather than a hardcoded path. It carried WARN while
    that was in progress, because a permanently-red gate is one nobody reads.
    """
    roots = [REPO / "services", REPO / "packages", REPO / "apps" / "platform"]
    out: list[Finding] = []
    # rails/<id>  or  "rails" / "<id>" as path segments (the form that hid these for months).
    pat = re.compile(rf"rails[/\\]{re.escape(m.id)}\b"
                     rf"|[\"']rails[\"']\s*/\s*[\"']{re.escape(m.id)}[\"']")
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.py")):
            # Tests are excluded: this rule is about RUNTIME coupling, and a regression test
            # legitimately names the path it exists to keep out of the runtime. Only `#`
            # comments are stripped below, so a docstring describing the old coupling would
            # otherwise fail the rule that documents it.
            if ({".venv", "node_modules", "tests"} & set(p.parts)) or p.name.startswith("test_"):
                continue
            for i, raw in enumerate(read(p).splitlines(), 1):
                line = raw.split("#", 1)[0]
                if pat.search(line):
                    out.append(F("RC020", m, f"shared code hardcodes a path into this rail "
                                             f"({raw.strip()[:70]}) — the broker and shell must "
                                             f"not depend on a rail", f"{rel(p)}:{i}"))
    return out
