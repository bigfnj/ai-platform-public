# The rail contract

A rail is an **independent deployable**: its own image, its own dependency set, its own config
idiom, its own release cadence. That is deliberate and this document does not erode it. There is
no `rail_core` package every rail imports, and there is not going to be one.

What *is* unified is the set of **contracts** a rail has to honour to be part of the platform.
Every one of them is machine-checked:

```bash
python tools/rail_conformance.py            # exit 1 on any violation
python tools/rail_conformance.py --rules    # the enforced rule list
python tools/rail_conformance.py --rail finance --json
```

`--rules` is the authoritative index. This document explains the *why* behind each rule; the tool
is what the rules actually say. It is stdlib-only and runs in ~4 seconds (measured 3.8–4.1 s
over five `hyperfine` runs, 2026-09-29; the earlier "~2 s" was stale by 2x), so there is no
excuse not to run it.

## Why contracts and not shared code

Six facts about a rail are restated in six places: the rail's own source, its `vite.config.ts`,
its Dockerfile, its compose service, the gateway's routing and dist registries, and the admin
model panel. Sharing code would collapse those, at the cost of the independence the rails were
built for. So instead each rail **declares itself once** in `rails/<dir>/rail.json`
(schema: [`rail-manifest.schema.json`](./rail-manifest.schema.json)) and the checker asserts that
every restatement agrees.

The failure mode this targets is not a broken build. **Every drift found when the contract was
first written here (2026-08-19) was silent** — 18 violations across 10 rails, none of which broke
anything loudly:

- **Three rails claimed vite dev port 5240** (bouquet, recipe-book, terminal-fun) and **two
  claimed 5230** (finance, workstation). The second `npm run dev` to start loses the port, and its
  `/api` proxy silently belongs to the other rail. Nobody noticed because nobody ran two dev
  servers at once.
- **Five rails pinned concrete model names as their in-code default**, and four of those names had
  been *retired* in the 2026-08-04 model consolidation: `llama3.1:8b`, `qwen3:30b-a3b`,
  `gemma3*:27b` (×2), `gemma3:12b`. Compose overrode them with roles, so the containers were fine
  and standalone dev was asking for models that no longer exist.
- **finance redefined `--good`** under `.finance`, shadowing the shared token with an identical
  value — harmless today, and a guarantee that any future change to the shared status colour would
  never reach that rail.
- **The lean installer compose passed no broker token at all.** Inert while the token is empty,
  and a total outage of every model call on that path the day one is configured.

None of those break anything loudly. That is exactly why they need a checker rather than a review.

## When a check fails

**The manifest is the contract; the code is the defect.** Fix the code.

Unless the manifest is what drifted — a rail that legitimately moved its port, renamed a slot,
changed its wrapper class. That is a human judgement and the tool deliberately does not guess.
Update the manifest and say why in its `notes`.

And sometimes the *checker* is what is wrong. Two rules were relaxed on first run here because they
were crying wolf, which is the fastest way to get a checker switched off:

- RC005 now accepts a rail that delegates to `platform_core.BrokerClient`, which injects the bearer
  centrally. Most rails here do that rather than reading the env var themselves.
- RC014 now accepts a compose service that pulls the whole gitignored `deploy/.env` via
  `env_file:` — which is where `BROKER_AUTH_TOKEN` actually lives, so those services get it
  without ever naming it. job-aid is the live example.

## The contracts

### Registration — RC001, RC003, RC004, RC011

A rail is reachable only if the gateway agrees it exists, in four separate places: `APP_CATALOG`
(so the shell draws a rail item), `app_<id>_url` (so `/<id>/api/*` proxies somewhere), `<id>_dist`
plus both mapping helpers (so the federated bundle is served), and a compose service on the
declared port. Miss one and the rail is registered but unreachable, with no error anywhere.

Three optional manifest keys exist because this platform's rails do not all fit one mould, and
guessing wrong means a rule silently verifies nothing:

- **`dir`** — the directory name when it differs from the catalog id. The one case is `rails/iep/`,
  whose id is `iep-goals`. (The catalog's `iep` tile is IEP Present Levels, a different app.)
- **`compose_service`** — the service name when it differs from the id. edu-suite's service is
  still called `dashboard`.
- **`container_port`** — the in-container port when it differs from `ports.backend`. The edu-suite
  family runs three separate containers that all listen on 8800, while the gateway's 8800/8801/8802
  are host-native defaults for standalone dev. Both numbers are real, so both are declared.

**`also_serves`** covers one image backing two catalog tiles. edu-suite serves both `edu-suite` and
`iep` (the same dashboard image run with `IEP_ONLY=1` against its own volumes, so student PII is
physically separate). RC003 and RC004 check the second tile's catalog entry, route and dist —
otherwise nothing in the tree verifies it at all.

### Catalog copy — RC019

Every manifest carries a `description`: the sentence or two a person deciding whether to enable
the rail would actually read. It is its own field because each of the three that look like they
might serve is answering a different question. `label` is a tile caption ("Voice Studio").
`notes` is developer caveats — 9p rename semantics, a dev-port collision — and is noise to
anyone else. The rail's `RailHeader` subtitle lives inside the federated bundle, which the
catalog cannot reach, since the whole point is describing rails that are **not** mounted.

The 40-400 character bound is stated twice on purpose. The schema bounds a description that
exists; RC019 is what makes a **missing** one fail, which the schema cannot — it is an optional
property, so old manifests keep validating. A description that only restates the label warns
rather than fails.

### Broker access — RC005, RC013, RC014

**The broker token is `BROKER_AUTH_TOKEN`, unprefixed.** It is one platform-wide shared secret, not
a per-rail setting. A prefixed alias is fine as long as the unprefixed name is among the names
actually consulted (RC005), **and** every compose file must pass it under that canonical name
(RC014). Both halves are required: a rail that only accepts its prefixed spelling invites a compose
file to be bent to match, and then the rail is correct under that file and tokenless under the
other. Delegating to `platform_core.BrokerClient`, or inheriting the whole `.env` via `env_file:`,
both satisfy this.

**Or `BROKER_AUTH_TOKEN_FILE`, a path whose contents are the token — which wins when both are
set.** Also unprefixed, for the same reason. Either name satisfies RC005 and RC014; a rail on a
stack that has switched over reads only the path, a rail on one that has not reads only the value,
and both are correct where they are. The file form exists because the environment is a bad place
for a shared credential: it is in `docker inspect`, in a crash dump, and in `/proc/<pid>/environ`.
The last of those is the one that matters in practice: anything that can read a backend's
environment can read the token, terminal-fun's games include roguelikes with a working shell
escape, and it is the one credential all nine broker clients share.

Rules for implementing it, all four enforced by tests rather than convention:

- **Strip the contents.** A secret file written by `echo` or an editor ends in a newline, and a
  bearer token with a trailing `\n` is a 401 with no visible cause.
- **An empty `BROKER_AUTH_TOKEN_FILE` means "not configured"**, not "the token is empty" —
  `${BROKER_AUTH_TOKEN_FILE:-}` in compose expands to an empty string, so a key added to a compose
  file ahead of the secret must not break every rail.
- **On a CLIENT, a configured-but-unreadable file yields `""`** — no `Authorization` header, and
  the broker's 401. It does *not* fall back to the plain variable: the fallback would leave a
  deployment that believes it has moved off the environment still reading the secret from it, and
  would hide a typo'd path behind a rail that looks healthy.
- **On the BROKER, the same condition is fatal.** There, an empty token means the control plane is
  *open* — `require_token()` returns early and every `/v1/*` route stops requiring a bearer, with
  no symptom but everything continuing to work. `BrokerSettings` refuses to construct instead.

Note what the file form does and does not buy. It removes the token from the process environment,
so a same-uid reader can no longer lift it out of `/proc/*/environ`. It does **not** stop that same
reader from opening the file. Closing that needs file ownership the reading process does not
hold — which means a uid split in the image that runs it (terminal-fun's Dockerfile is the worked
example).

Which makes the mount's permissions load-bearing, not incidental. Plain (non-swarm) compose
bind-mounts a `secrets:` entry with the **host file's** ownership and silently ignores the
`uid`/`gid`/`mode` keys — those are swarm-only. A world-readable token file hands the credential
straight back to the process the uid split exists to contain, and everything still works, so
nothing reports it. Mount it readable by the backend's uid only, and verify from inside the
container as the game uid before treating the finding as closed.

**Model references are `@role`, in the in-code default, not just in compose.** Admin → Rails
repoints a role in `roles.json` (hot-read, no restart). A rail that pins a concrete name silently
ignores the panel. Checking only the compose value leaves standalone dev pinned — which is exactly
how four dead model names survived the 2026-08-04 consolidation here. Deliberate exceptions set
`pinned_default_ok` with a `note`; the shipped exception is **job-aid**, which supports a
direct-Ollama provider as well as the broker, and an `@role` means nothing to Ollama.

### Model slots — RC006, RC007, RC008, RC015

A slot id is one identity with up to three views: the manifest, the rail's own chip code, and the
gateway's `RAIL_MODEL_SLOTS`. They must agree, because the slot id is the only thing connecting the
control an operator changes to the readout they then look at.

Embedders, TTS and STT set `admin_panel: false` — that panel surfaces chat/vision/image only — so
they correctly have no `RAIL_MODEL_SLOTS` counterpart.

RC007 and RC008 govern **per-rail model chips**. ⚠ This section used to say both rules were inert
because "every rail here sets `status_route: null`". That is false and was false when written:
**12 of the 15 rails declare a `status_route`** (11 at `/api/capabilities`, co-worker at
`/api/models`), and only ai-voice, meeting-atlas and workstation leave it null. Both rules run. A
rule that skips reports as a skip now, not as a pass, which is what made the difference visible.

The rules hold the four-state contract (`missing` / `cold` / `warming` / `loaded`) consistent.
Red-vs-blue is the whole point of four states: red needs an `ollama pull`, blue just needs someone
to ask a question, and a two-state dot collapses them into one useless "off".

Note that the shared palette currently defines `--good`, `--warning` and `--critical` but **no
`--info`**, so the blue state has no stable token yet. That is a prerequisite for chips, not an
oversight to paint over with a literal.

**RC015 — the payload being right is only half of it; the rail has to keep asking.** Residency
changes with nobody touching the UI: the broker evicts on a `keep_alive` expiry, and asking a
question warms a model back up. A one-shot fetch in a mount effect renders a state that is correct
for about a second and silently wrong from then on — and because loading a model is something you
cause *by using the rail*, always after mount, the chips are guaranteed to be stale exactly when
someone looks at them.

This is carried from the sibling public repo, where a rail shipped precisely that bug: chips
reading `cold` for a model that was loaded and actively answering, while the shell's own top-bar
widget correctly showed it resident. **Every other rule passed on that rail**, because the envelope
shape was perfect. Liveness is a property of the caller, not the payload. When chips land here,
poll the status route on a **6 s** interval and clear it on unmount.

### Ports — RC002, RC009

Backend and dev ports are unique across **every** rail in the tree, manifested or not. Checking
manifests only against each other would leave the tool blind to exactly the rails not yet under
contract, and report green while doing it.

| Rail | Backend | Dev |
|---|---|---|
| edu-suite | 8800 | 5210 |
| iep (Present Levels) | 8801 | — (edu-suite image) |
| iep-goals | 8802 (container 8800) | 5300 (reserved) |
| workstation | 8720 | 5270 |
| terminal-fun | 8730 | 5290 |
| job-aid | 8810 | 5220 |
| finance | 8820 | 5230 |
| recipe-book | 8830 | 5280 |
| bouquet | 8840 | 5240 |
| ai-playground | 8850 | 5250 |
| ai-voice | 8860 | 5260 |
| smb-partner-enablement | 8870 | 5320 |
| gemini-cx | 8880 | 5330 |
| co-worker | 8890 | 5310 |
| openmaic | 8900 | 5350 |
| meeting-atlas | 8740 | 5340 |

openmaic's `openmaic-app` sidecar is not in this table: it is not a rail, it has no manifest, and
it `expose`s 3000 on the compose network only, never to the host. It is reachable solely through
the rail's own reverse proxy, which is the point of running it as a sidecar.

### Federation — RC010

`federation_name` is the JS identifier the shell imports as `<name>/module`; `base` is `/<id>/`,
matching where the gateway serves the bundle. A mismatch means the rail cannot mount.

### Theming — RC012

Full rules in [`web/THEMING.md`](../web/THEMING.md). The checker enforces the two failures that
actually break a palette:

1. **Never redefine `--accent`, `--muted`, `--good`.** They inherit the chosen palette. Derive a
   *local alias* instead (`--ac`, `--mut`) with a standalone fallback. Note that
   `--accent: var(--accent, …)` is not a workaround — a self-referential custom property is a
   dependency cycle the spec makes invalid at computed-value time, so the token becomes unreliable
   throughout the rail rather than merely unpalettable.
2. **Status colours come from semantic tokens, not literals.** `--good` / `--warning` /
   `--critical` are defined once on `:root` and deliberately not redefined per palette or per mode:
   status must mean the same thing and read the same way on every palette.

A rail that renders standalone (finance does; it never loads the shared stylesheet) puts its
fallback definitions in its **standalone-only** stylesheet, not in the `theme.css` the shell also
loads. Defining them in `theme.css` shadows the shared tokens for everyone.

### Header — RC017

Every rail's page header is the shared **`RailHeader`** from `@web-core`
([`web/src/RailHeader.tsx`](../web/src/RailHeader.tsx)) — one shape everywhere, top to bottom:

> **icon · bold title · muted subtext · model chips · a full-width rule**

The parts:

- **icon** — an emoji string or an inline SVG (the Gemini spark, the ai-playground slide).
- **title / subtitle** — the rail's name and a one-line description.
- **chips** — render a **`ModelChips`** ([`web/src/ModelChips.tsx`](../web/src/ModelChips.tsx)):
  a four-state dot per model slot (`missing` red / `cold` blue / `warming` orange / `loaded`
  green) resolved live from the broker, so the chip tracks both the model an admin picked in
  **Admin → Rails** (the slot's `@role`) and its VRAM residency. A rail with **no foreground
  model** (workstation, ai-voice) renders `RailHeader` with **no chips** — that is allowed and
  honest; do not invent an Ollama chip for a rail that has none.
- **actions** — the escape hatch: a rail's tab bar, pickers, or buttons go in the `actions` slot,
  to the right of the titles, so the icon/title/subtext/chips column stays identical no matter
  what a rail hangs off the side.

The chip classes and the rule live in the **global** `web/src/styles.css` (the shell loads it once
for the whole document), so even a rail that injects its own `<style>` gets them; import nothing
for styling. Chips are wired by a rail's own `GET /api/capabilities` (copy the four-state resolver
`modelstate.py` and poll it every 6 s) — the resolver is duplicated per rail by design, like the
rest of the broker client, but the four state names and resolution order are a cross-rail visual
language and must stay identical.

**RC017** requires the import from `@web-core` *and* a `<RailHeader>` in the rail's frontend. It
follows a relocated frontend via `package_path`, so edu-suite's `apps/dashboard/frontend` is
covered too — it used to be silently skipped, which read like a pass.

### Identity: fail closed

The gateway authenticates every request and sets `X-Platform-User` (and `X-Platform-Admin`),
stripping any client-supplied copy first. **A request without that header did not come through
the gateway.** In this topology that means a sibling container on the compose network, so the
only safe answer is `401` — not "anonymous", and certainly not "admin".

Eight of fourteen rails got this wrong at once, which is why it is now a contract item rather
than a per-rail habit. Three had an *inverted* gate — `if user is not None and not is_admin: 403`
— which rejects a named non-admin and waves through a caller with no header at all. Three
defaulted the user to the string `"?"`. Two had no identity code. Worst was `workstation`, which
read the header only to label its audit line and then opened a PTY-over-SSH session on the host.

Every rail carries its own `identity.py` (or an equivalent local dependency). That duplication is
deliberate, for the same reason `broker.py` and `modelstate.py` are duplicated: a rail that has to
import the platform to boot is not a component you can lift out. **RC021** enforces the shape, not
the sharing.

- 401 when `X-Platform-User` is absent, unless `PLATFORM_STANDALONE` is set
- one flag name across every rail — the five per-rail names it replaced are a RC021 failure
- standalone yields `user=None`, never the literal string `"standalone"`: rails persist the
  caller into owner columns, and a placeholder there becomes real-looking data
- admin routes take `require_admin`, which reads the flag off a *resolved* identity rather than
  trusting a bare `X-Platform-Admin` header

**Two more headers, if your rail shares data.** `X-Platform-Peers` (JSON array of usernames) and
`X-Platform-Rooms` (JSON array of workspace ids) widen owner scope; both are sent only when
non-empty. Take the one that matches your data: **peers** when a record belongs to whoever made it,
**rooms** when it belongs to a subject that outlives its creator. The parse rule is the transferable
part and it is **fail-closed and all-or-nothing** — absent, `""`, `[]`, non-JSON, a JSON object, a
bare string, or an array holding one non-string all mean *no sharing*. A partly-readable header is
one you do not understand, and honouring the readable half widens access on a guess. Reference
implementation: `rails/edu-suite/apps/dashboard/src/dashboard/access.py` (`parse_peers`,
`visible_owners`, `may_access`), which `iep_rail._parse_peers` matches element-for-element because
both rails read the same header and must agree on every input. Never trim, lowercase or otherwise
normalise a username from these headers: it could map one real user onto another's rows.

Two shapes are in use and both are fine. An **app-level** `dependencies=[Depends(identity)]` (with
`docs_url`/`openapi_url` set to `None`, since FastAPI's doc routes bypass app-level dependencies)
gates every route and cannot be forgotten when a route is added. **Per-route** `Depends(identity)`
leaves `/api/health` open as a liveness probe. Two things app-level gating does *not* cover, both
found the hard way: `app.mount(...)` static mounts are not routes (terminal-fun's `/api/webtoys`
was serving 200 un-gated), and websocket handshakes should be refused *before* any other
validation so an un-gated caller cannot probe what exists by reading close codes.

### Student identifiers — RC018

`rails/iep/` holds records about real children. `local/` keeps the source documents out of git,
and nothing stopped the **names inside them** walking out into the code anyway — they did: golden
assertions, unit fixtures copied from a real case, and edge-case comments recording whose sheet
exhibited the bug. Cleaned up 2026-08-23; this rule is what stops it coming back. The leak is a
by-product of debugging against real data rather than carelessness, which is why a habit was
never going to be enough.

Registered against `iep-goals`, but **repo-wide**: it runs once and scans every tracked file,
because a name can land anywhere. The identifiers are read from the gitignored
`rails/iep/local/pseudonyms.json`, so the checker itself never contains them. Three consequences
follow, all deliberate:

- On a clean checkout the guard **cannot run, and says so** — WARN, not a silent pass. A check
  that quietly does nothing is worse than no check.
- A finding reports **file and line only, never the matched text.** Echoing the name into a
  terminal or a CI log just moves the leak.
- Matching is case-**sensitive**, so an ordinary lowercase noun that collides with a surname does
  not trip the capitalised name.

Each student contributes three tokens: the full `Last, First`, the surname, and the first name. A
first name that collides with an unrelated proper noun elsewhere in the monorepo can be dropped
with `scan_first_name: false`, leaving the other two to identify. Genuine exceptions belong in
`scan_allow` (one line) or `scan_allow_paths` (a subtree) rather than in a loosened rule — and
keep the path prefixes **narrow**, because a broad one switches the guard off for everything
under it and nothing will report that.

Neither this section nor the rule's own docstring gives an example of a matching name. The
publish gate scans case-**insensitively**, so an illustrative surname written here would fail it:
the rule applied to itself.

## The rail template: three tiers

With fourteen rails there is enough evidence to say which files must be identical and which
must not. That was measured, not assumed:

| file | copies | distinct implementations |
|---|---|---|
| `identity.py` | 6 | **1** (2 on disk; the difference was line endings) |
| `modelstate.py` `resolve()` | 11 | **5**, of a resolver this document calls identical |
| `broker.py` | 9 | **9**, legitimately |

Saying "these are duplicated consistently" did not make it so, because nothing compared the
copies. So the template is a program, `tools/rail_template.py`, and the same code both writes a
new rail and checks the existing ones. They cannot disagree.

**Tier 1 — invariant.** Generated. Byte-identical everywhere. RC023 fails on any difference.
Today: `identity.py`, `modelstate.py`. Do not edit these in place; change the template and run
`rail_template.py sync`. Line endings are normalised on the way out, because a CRLF-only
difference is drift no reviewer will ever spot.

**Tier 2 — conventional.** Shape enforced, content free. The manifest and its required keys,
a `tests/test_auth.py` asserting the 401, the compose service, the eight registration points,
styles scoped to the declared wrapper. Rules RC001-RC021 own this tier.

**Tier 3 — free.** `broker.py`, `config.py`, routes, the frontend module. Genuinely per-rail;
the generator seeds them and never looks again.

### The one thing tier 3 owes tier 1

`broker.py` is free, but it must expose a **surface**: `roles()`, `models() -> list[dict]`,
`status()`, and a `BrokerError`. RC022 checks it, and it is the whole reason `modelstate.py`
can be identical everywhere.

That surface is also how the five variants happened. `models()` used to mean two different
things: the broker's raw list in most rails, and a UI-picker `{broker_up, models: [...]}` dict
in finance and recipe-book. Their resolvers had to unwrap a dict while everyone else's read a
list. The picker shape is now `picker_models()`, and the ambiguity is gone.

A rail that does no model work at all (meeting-atlas, workstation, ai-voice) needs no facade
and no `modelstate.py`; the rule skips it rather than demanding an empty one.

## Adding a rail

1. Write `rails/<dir>/rail.json` first. It is the input to everything else, so a rail that
   starts from its manifest cannot be born disagreeing with itself. Schema:
   `docs/rail-manifest.schema.json`.
2. `python tools/rail_template.py new <id>` — creates the tree and the tier-1 files.
3. Write the tier-3 files it lists: `broker.py` (with the canonical surface),
   `config.py`, `api/app.py` (require `Depends(identity)` app-wide and set
   `docs_url`/`openapi_url` to `None`), and `tests/test_auth.py` asserting the 401.
4. Register it: `APP_CATALOG`, `app_<id>_url`, `<id>_dist`, both mapping helpers in the gateway
   config, `RAIL_MODEL_SLOTS` if it has panel slots, a compose service, and the shell's `lazy()`
   + `remotes.d.ts` + mount branch. RC003, RC004, RC006, RC010, RC011 and RC016 each check one
   of these, so a missed step is a named failure rather than a rail that silently 404s.
5. **If the installer should offer it, wire the lean path too** — a *separate* set of
   restatements from step 4, and the one most recently missed. A profiled service in
   `deploy/installer/docker-compose.installer.yml`, the same id in `Get-ComposeProfiles`
   (`lib-runtime.ps1`) **and** in both chooser lists in `install.ps1`, the frontend baked into
   `deploy/Dockerfile.gateway.bundled`, and every non-image `@role` in `roles.lean.json`.
   RC025-RC027 check these. See "The lean installer path" below for why each one is silent.
6. Reference models as `@role` in the **in-code** default, and read `BROKER_AUTH_TOKEN`
   unprefixed.
7. Scope styles under the declared wrapper; render the shared `RailHeader` from `@web-core`.
8. Give it a deskpet quip bank (`deskpet/quips/<catalog-id>.json`, 100+ lines) and register it
   in `deskpet/lines.ts`. Banks key off **catalog id**, not directory. RC024 warns if it is
   missing.
9. **Give it a test suite** at `rails/<id>/tests/` with at least one `test_*.py`. RC028 checks
   this. It is a rule rather than a convention because the gap is invisible without one:
   `run-tests.ps1` builds its plan from the directories that EXIST, so a rail with no suite is
   not a failing target, it is a target that was never created. `smb-partner-enablement` and
   `gemini-cx` were imported on 2026-08-20 with no tests at all and `-List` read as correct
   (12 targets for 14 rails) for three weeks.
10. If it WRAPS a third-party app and that app serves assets from the origin root, declare
    them in `root_assets` and mirror them into the gateway's `ROOT_ASSETS`. RC029 checks it.
11. `python tools/rail_conformance.py` until clean.

## Root-origin assets — RC029

A rail written for this platform lives entirely under `/<id>/` and never needs this. A rail that
**wraps** a third-party app does. `openmaic` fronts an upstream Next.js app whose source carries
~124 hand-written `<img src="/logos/...">` literals, and Next's `basePath` only rewrites URLs
Next itself generates — a string literal in JSX is passed through untouched, so the browser
resolves it against the ORIGIN root and it leaves the rail's namespace entirely.

Those requests did not even 404. The shell's client-side-routing catch-all answered `index.html`
with **status 200**, so the browser showed a broken image while the network tab showed success.

`root_assets` in the manifest declares the paths; `ROOT_ASSETS` in the gateway's `config.py`
mirrors them, because the gateway CONTAINER cannot see `rails/` at all (compose mounts only each
rail's built dist). RC029 is the price of that mirroring, and it checks three things:

- the two copies agree;
- no two rails claim overlapping paths. The origin root is an **exhaustible shared resource with
  a silent failure mode** — two rails claiming `/avatars/` would not error, one would simply
  serve the other's images. Same class as the vite-port collision RC002 exists for;
- nothing claims a platform-reserved prefix (`/api/`, `/assets/`, `/ws/`), or a rail could shadow
  the shell's own bundle for every user at once.

Two things the gateway does that are easy to get wrong, both locked by tests in
`apps/platform/backend/tests/test_root_assets.py`:

- **The owning rail is bound in a CLOSURE, never a default argument.** `async def handler(request,
  _owner: str = app_id)` is the obvious way to capture a loop variable and FastAPI reads it as a
  **query parameter**, so `/logo-horizontal.png?_owner=other-rail` would re-point the request
  after the entitlement gate had already authorised it on the path.
- **These paths carry the rail's entitlement.** They do not start with an app id, which is the
  whole point of them, so `app_access_gate` resolves the owner by prefix and applies the same
  check it would under `/<id>/`. Without that they would miss the gate entirely.

GET and HEAD only: a rail claiming the root must not thereby acquire a writable surface outside
its own namespace.

## The lean installer path

RC001-RC024, RC028 and RC029 govern the **full** stack. The lean installer is a second, parallel set of
restatements, and until RC025-RC027 nothing checked it at all — `meeting-atlas` was ported into
`deploy/docker-compose.yml` and left out of every installer file, and the checker stayed green
while a downstream Podman install got a rail in the nav that no backend answered.

Which rails are lean-installable is **derived from `docker-compose.installer.yml`**, not declared
in `rail.json`. That file already is the statement of what the installer offers, so keying off it
needs no new manifest field (the schema is `additionalProperties: false`) and avoids a second
declaration that can disagree with the first.

Each link fails silently on its own, which is why all four are checked rather than the obvious one:

| Restatement | Rule | What its absence does |
|---|---|---|
| `profiles:` on the service | — | (the declaration; everything else agrees with this) |
| `Get-ComposeProfiles` | RC025 | `compose up` skips the service and prints nothing |
| both chooser lists in `install.ps1` | RC025 | the id never enters `PLATFORM_ENABLED_APPS`, so the profile is never selected and the line above never matches |
| `Dockerfile.gateway.bundled` | RC026 | **unrecoverable at runtime** — the shell resolves federation remotes at BUILD time, so a rail absent from the image cannot mount however the installer is configured |
| `roles.lean.json` | RC027 | the install succeeds and the rail's first model call fails on an `@role` the broker cannot expand |

The bundled image states its own requirement in a comment, and `meeting-atlas` was missing from
it anyway: a comment is not a check. RC026 covers four separate places that file has to name a
rail — the stage-1 frontend `COPY`, the npm build loop, the stage-2 `COPY` of the built `dist`,
and `PLATFORM_<RAIL>_DIST` — and then checks `PLATFORM_APP_<RAIL>_URL` against the manifest's
compose service and backend port, because a stale port there is silent in the same way.
co-worker's backend moved 8860 → 8890 on import, and a partial `up` against an older gateway is
the failure that finds it.

Image slots are exempt from the `roles.lean.json` rule: the lean profile runs with the broker
media pipeline off and recipe-book's icons pre-rendered into its seed, so nothing there invokes
`@recipe-icon`.

## What is deliberately not unified

- **Config idioms differ** (pydantic-settings with a rail prefix; bare `os.environ` module
  constants). Both are fine. The contract governs the *names and values*, not the mechanism.
- **Backend layouts differ** (`src/<pkg>/` with a `create_api()` factory vs `backend/<pkg>_app/`
  with a module-level `app`). The manifest's `layout` records which so the checker looks in the
  right place; `package_path` covers edu-suite, which fits neither.
- **Per-rail `broker.py` clients**, `api.ts` fetch helpers and `theme.css` are still forked. That is
  real duplication and a candidate for a future shared layer — but it is *consistent* duplication
  now, which is the prerequisite for extracting it safely later.

## Known gaps

None open. The checker reports `0 fail, 0 warn` across 15 manifests and 29 rules.

Both entries that used to sit here closed, and both are worth knowing about because the shape
recurs:

- *"The broker still reaches into `rails/ai-voice/native/` by name"* (RC020, WARN) — closed. The
  engine root is now **configuration**, supplied by the deployment as
  `BROKER_VOICE_ENGINES_DIR` and overlaid by `services/broker/voice_engines.json`
  (`services/broker/app/config.py:115`). An empty root means no engines and voice reports
  unavailable, which is the honest state for a platform with ai-voice removed, rather than
  `/v1/voice/*` failing with no error until a job ran. Dispatch deliberately stayed in the
  broker: synthesis takes the GPU gate and evicts resident models, which is arbitration only the
  broker can do. That was the last of three such couplings; `edu_media_core` and the XTTS
  reference clips had already moved to `packages/edu-media-core`.
- *"`iep-goals` is absent from `RAIL_MODEL_SLOTS`"* — closed. It is present at
  `apps/platform/backend/platform_gateway_app/rails_models.py:50`, so Admin → Rails repoints it
  per-rail like every other slot-bearing rail.

Retired from this list earlier, after being re-checked against the tree (all four were stale):

- *"One concrete model pin survives in compose: `RECIPE_BOOK_LLM_MODEL`"* — it was never a pin to
  repoint, it was dead code: every call site already passed an explicit `@role`, so the fallback
  was unreachable. Removed from compose when `model` became a required argument, and the last
  copy (a baked `ENV` in `rails/recipe-book/deploy/Dockerfile`, still naming a 24 GB model and so
  actively misleading on a small card) went 2026-08-26.

- *"No rail renders model chips"* — 15 rails import `ModelChips`, `co-worker`, `gemini-cx` and
  `smb-partner-enablement` declare a `status_route`, and the `--info` token exists in the palette.
- *"compose pins concrete globs for six rails"* — finance, terminal-fun, ai-playground and three
  of recipe-book's four now use `@role`; only the one above remains.
- *"five `<id>_dist` defaults point at pre-monorepo directories"* — every default is now
  `RAILS / <rail> / frontend / dist`.
