# RC005-RC008: the broker token, and the model slots and the chips that show them.
#
# A rule file of tools/rail_conformance.py. That module runs it into its own namespace rather
# than importing it (its "where the rules live" says why), so rule, F, S, Manifest, Finding,
# read, rel, REPO and the rest of what is used here without being defined here are its names.
# A comment rather than a docstring: a docstring would replace that module's, its --help text.
from __future__ import annotations

import ast
import re


@rule("RC005", "The broker token is read from the unprefixed BROKER_AUTH_TOKEN(_FILE).")
def rc005(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """Every rail's compose service is handed BROKER_AUTH_TOKEN, unprefixed, because it is
    one platform-wide shared secret rather than a per-rail setting. A rail that reads only
    its own prefixed spelling gets an empty token and sends no Authorization header — which
    is invisible until the broker starts enforcing, then every model call 401s at once.

    Since 2026-09-10 the token may instead arrive as BROKER_AUTH_TOKEN_FILE, a path whose
    contents are the secret (see TOKEN_ENV_NAMES for why, and why either name suffices). This
    rule is the INVENTORY of where the token is read, so it had to learn the second spelling
    in the same change that introduced it: a rule that knows only the old name turns red on
    the first rail to move, and a red checker that is "expected" is a checker nobody reads.

    A prefixed alias IS allowed (pydantic AliasChoices), as long as an unprefixed name is
    among the names actually consulted.

    ALSO satisfied by delegating to the shared platform_core BrokerClient, which injects the
    bearer centrally (`broker_token()` reads the unprefixed names). Most rails here do that
    rather than rolling their own client, and flagging them for "never reading the token"
    would be nine false alarms — the fastest way to get a checker switched off.

    Matched against exact string CONSTANTS in the parsed AST, not raw text. A substring
    search over the source finds the name in a comment explaining the variable and passes a
    rail that never reads it — which is precisely how this defect stayed hidden. Note that
    exact-constant matching is also what keeps the two names distinct: "BROKER_AUTH_TOKEN" is
    a prefix of "BROKER_AUTH_TOKEN_FILE", so a substring search could never have told a rail
    that reads only the file form from one that reads the value.
    """
    if not m.slots():
        return []  # a rail that does no model work needs no token
    sources = m.py_sources()
    if not sources:
        return []

    reads_token = False
    prefixed_field: tuple[str, str] | None = None  # (attr, file) for a better message
    prefix = str(m.data.get("env_prefix") or "")
    value_only: list[str] = []  # modules that read the literal and never the file form
    file_aware: list[str] = []  # modules that read the file form
    for p in sources:
        try:
            tree = _ast_parse(read(p))
        except SyntaxError:
            continue
        names = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                 and n.value in TOKEN_ENV_NAMES}
        if names == {"BROKER_AUTH_TOKEN"}:
            value_only.append(rel(p))
        if "BROKER_AUTH_TOKEN_FILE" in names:
            file_aware.append(rel(p))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in TOKEN_ENV_NAMES:
                reads_token = True
            # Delegation to the shared client counts: it sends the header for them.
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "platform_core") and any(a.name == "BrokerClient" for a in node.names):
                reads_token = True
            # A pydantic-settings field named *_auth_token under a rail env_prefix resolves
            # to <PREFIX>BROKER_AUTH_TOKEN, which nothing in deploy/ sets.
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                    and node.target.id.endswith("broker_auth_token"):
                prefixed_field = (node.target.id, rel(p))
    if reads_token:
        # Either name alone is right for one deployment (TOKEN_ENV_NAMES), but a rail that has
        # moved SOME readers to the file form and not others is right for neither: at the
        # switchover the stragglers send no Authorization header while the rest keep working.
        # The 2026-09-10 sweep moved job-aid's llm/client.py and missed broker_status.py, and
        # this rule passed it because the rail as a whole named both constants.
        if not file_aware:
            return []
        return [F("RC005", m, f"{p} reads BROKER_AUTH_TOKEN but never BROKER_AUTH_TOKEN_FILE, "
                              f"while {file_aware[0]} reads the file form: at the switchover "
                              f"this reader sends no Authorization header and its calls 401 "
                              f"while the other keeps working", p)
                for p in value_only]

    msg = (f"backend never reads {' or '.join(TOKEN_ENV_NAMES)}, so it sends no broker "
           f"Authorization header and every /v1/* call 401s once the broker enforces a token")
    where = rel(m.backend_dir())
    if prefixed_field:
        attr, where = prefixed_field
        msg = (f"reads the token ONLY as {prefix}{attr.upper()}. The canonical name is the "
               f"unprefixed BROKER_AUTH_TOKEN (or BROKER_AUTH_TOKEN_FILE), which is what "
               f"every other rail reads and what "
               f"deploy/docker-compose.yml passes to all nine services; a rail that accepts "
               f"only its prefixed spelling works under whichever compose file was bent to "
               f"match it and silently gets no token under the other")
    return [F("RC005", m, msg, where)]


@rule("RC006", "Panel slots have a matching gateway RAIL_MODEL_SLOTS entry, and vice versa.")
def rc006(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    where = rel(GATEWAY / "rails_models.py")
    panel = gateway_rail_slots().get(m.id)
    mine = {str(s["slot"]): s for s in m.panel_slots() if s.get("slot")}
    if panel is None:
        if mine:
            return [F("RC006", m, f"declares admin-panel slots {sorted(mine)} but the rail "
                                  f"is absent from RAIL_MODEL_SLOTS, so Admin -> Rails "
                                  f"cannot repoint it", where)]
        return []
    theirs = {str(s.get("slot")): s for s in panel}
    out: list[Finding] = []
    for slot in sorted(set(mine) - set(theirs)):
        out.append(F("RC006", m, f"slot '{slot}' is admin_panel but RAIL_MODEL_SLOTS has "
                                 f"{sorted(theirs)}", where))
    for slot in sorted(set(theirs) - set(mine)):
        out.append(F("RC006", m, f"RAIL_MODEL_SLOTS declares slot '{slot}' which the "
                                 f"manifest does not", where))
    for slot in sorted(set(mine) & set(theirs)):
        w, g = str(mine[slot].get("role")), str(theirs[slot].get("role"))
        if w != g:
            out.append(F("RC006", m, f"slot '{slot}' role mismatch: manifest {w!r} vs "
                                     f"panel {g!r}", where))
        w, g = str(mine[slot].get("env")), str(theirs[slot].get("env"))
        if w != g:
            out.append(F("RC006", m, f"slot '{slot}' env mismatch: manifest {w!r} vs "
                                     f"panel {g!r}", where))
    return out


def _ast_slot_ids(m: Manifest) -> set[str]:
    """Slot ids read from the parse tree, for the rails the regexes cannot follow.

    Three rails build their spec list in a VARIABLE before passing it to modelstate.resolve --
    job-aid and edu-suite assign it, openmaic assigns conditionally and then appends -- so the
    inline-literal regex declined all three. openmaic was added by the same session that added
    that regex and landed outside it, which is the tell.

    The shape matched is EXACTLY the documented one: a 3-tuple whose first two elements are
    string constants, inside a function that calls modelstate.resolve. Looser than that and it
    starts collecting ("broker", ok) and ("done", x) from the same functions -- measured, not
    guessed -- and a checker that cries wolf gets switched off as surely as a vacuous one.
    """
    found: set[str] = set()
    for p in m.py_sources():
        try:
            tree = _ast_parse(read(p))
        except SyntaxError:
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(isinstance(n, ast.Attribute) and n.attr == "resolve"
                       and _callee(n.value) == "modelstate" for n in ast.walk(fn)):
                continue
            for n in ast.walk(fn):
                if not (isinstance(n, ast.Tuple) and len(n.elts) == 3):
                    continue
                a, b = n.elts[0], n.elts[1]
                if not (isinstance(a, ast.Constant) and isinstance(a.value, str)
                        and isinstance(b, ast.Constant) and isinstance(b.value, str)):
                    continue
                if re.fullmatch(r"[a-z][a-z0-9-]*", a.value):
                    found.add(a.value)
    return found


@rule("RC007", "The rail's own chip slot ids match the manifest.")
def rc007(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """The rail declares its chip slots as (slot, label, ref) tuples — MODEL_SLOTS at module
    level, or a _model_slots() helper. Both spellings are in use; find whichever is present
    and compare the slot ids only. Labels are cosmetic and deliberately not enforced.

    Heuristic by necessity (the tuples reference config attributes, so they are not literal
    and ast.literal_eval cannot touch them). It extracts the first string of each tuple and
    only reports when it found some — never when it found none, so an unrecognised spelling
    is a silent skip rather than a false accusation.
    """
    if m.data.get("status_route") is None:
        return [S("RC007", m, "no status_route — the rail renders no chips, so it has "
                              "no slot ids to agree with the manifest")]
    # `kind: image` slots are exempt, and the reason is the same one the broker's audit_roles
    # gives for MEDIA_IMAGE_BACKENDS: an image backend (flux-schnell, sdxl-turbo) is loaded by
    # the media worker from the HF cache and NEVER appears in Ollama's tag list. A four-state
    # chip for one could only ever read "missing", so recipe-book deliberately renders two
    # chips against three declared slots. That divergence is stated in the manifest by the
    # kind, not inferred here.
    declared = {str(s["slot"]) for s in m.slots()
                if s.get("slot") and s.get("kind") != "image"}
    if not declared:
        return [S("RC007", m, "manifest declares no Ollama-backed model slots")]

    found: set[str] = set()
    where = ""
    for p in m.py_sources():
        src = read(p)
        # Three idioms, because rails really do use three. The third — the slot list written
        # inline into `modelstate.resolve([...])` — was missing, and the skip accounting is
        # what surfaced it: RC007 was silently declining all but three rails and reporting
        # `0 fail, 0 warn`, so it genuinely checked three. Most of the declined ones
        # declare their slots exactly this way (finance, recipe-book, job-aid, bouquet,
        # edu-suite, iep-goals, ai-playground, terminal-fun).
        for block in re.findall(
            r"(?:MODEL_SLOTS[^=]*=\s*\[|def _model_slots\(\)[^:]*:.*?return\s*\["
            r"|modelstate\.resolve\(\s*\[)(.*?)\]",
            src, re.S,
        ):
            ids = re.findall(r"\(\s*[\"']([a-z][a-z0-9-]*)[\"']\s*,", block)
            if ids:
                found.update(ids)
                where = where or rel(p)
    if not found:
        found = _ast_slot_ids(m)
        where = where or rel(m.backend_dir())
    if not found:
        return [S("RC007", m, "no MODEL_SLOTS / _model_slots() / modelstate.resolve() spec "
                              "could be read — the chip code spells its slots a way neither "
                              "the regexes nor the AST scan can follow, and guessing would be "
                              "a false accusation")]
    out: list[Finding] = []
    # A rail with `also_serves` runs ONE image as more than one tile and legitimately declares a
    # different slot set per tile -- edu-suite renders a "writer" slot only under IEP_ONLY=1,
    # for the Present Levels tile, and its own manifest describes the edu-suite tile. The
    # divergence is stated by also_serves rather than inferred, so only the direction that
    # cannot be explained that way is reported.
    extra = set() if m.data.get("also_serves") else found - declared
    for slot in sorted(extra):
        out.append(F("RC007", m, f"chip code declares slot '{slot}' which the manifest does "
                                 f"not (manifest: {sorted(declared)})", where))
    for slot in sorted(declared - found):
        out.append(F("RC007", m, f"manifest declares slot '{slot}' which the chip code does "
                                 f"not (code: {sorted(found)})", where))
    return out


@rule("RC008", "Model chips use the four-state contract, not a two-state resident flag.")
def rc008(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """missing / cold / warming / loaded, on both sides of the wire. The red-vs-blue
    distinction is the operationally useful one: red needs an `ollama pull`, blue just needs
    someone to ask a question. A two-state dot collapses those into one useless "off".
    """
    if m.data.get("status_route") is None:
        return [S("RC008", m, "no status_route — no chips, so no four-state contract "
                              "to honour")]
    out: list[Finding] = []

    py = "\n".join(read(p) for p in m.py_sources())
    if py and not all(f'"{s}"' in py or f"'{s}'" in py for s in CHIP_STATES):
        missing = [s for s in CHIP_STATES if f'"{s}"' not in py and f"'{s}'" not in py]
        out.append(F("RC008", m, f"backend never emits chip state(s) {missing} — the "
                                 f"four-state contract is not implemented server-side",
                     rel(m.backend_dir())))
    if re.search(r'"resident"\s*:', py):
        out.append(F("RC008", m, "backend emits a boolean \"resident\" field; the contract "
                                 "is a four-valued \"state\"", rel(m.backend_dir())))
    if re.search(r'"broker_reachable"\s*:', py):
        out.append(F("RC008", m, "backend emits \"broker_reachable\"; the contract envelope "
                                 "key is \"broker\": \"ok\" | \"unreachable\"",
                     rel(m.backend_dir())))

    # Frontend: only a dot driven by MODEL residency is a violation. A binary dot is correct
    # for things that really are binary — smb-partner's voice backend, terminal-fun's PTY
    # connection — so keying on a generic `? 'on' : 'off'` would flag those forever, and a
    # rule that cries wolf gets suppressed. Key on the model-residency field instead.
    for p in m.ts_sources():
        src = read(p)
        for hit in re.finditer(r"dot \$\{[^}]*\}", src):
            if re.search(r"\.resident\b", hit.group(0)):
                out.append(F("RC008", m, "frontend renders a model dot from a boolean "
                                         ".resident; the contract is a four-valued .state",
                             rel(p)))
        if re.search(r"\.resident\b", src):
            out.append(F("RC008", m, "frontend still reads the removed boolean .resident "
                                     "field", rel(p)))
    return out
