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
python tools/rail_conformance.py --strict --allow unbuilt-tree   # a WARN fails too
```

A WARN means a rule could not look, and by default it is printed and the run still exits 0.
`--strict` makes it exit 1, and on an unscoped run (no `--rail` or `--rule`) so does a rule that
skipped every rail and so checked nothing.
The only exemptions are conditions that are vacuous by construction in a CI checkout or a publish
snapshot, each named and passed separately with `--allow`: `unbuilt-tree` (RC016 and RC032 skip
every rail when no rail frontend is built) and `pseudonyms-absent` (RC018 cannot read the gitignored
student-name list). An allowance travels with the exact finding it describes, so
`--allow unbuilt-tree` does not excuse RC016's other warning, a gateway config it cannot import.
Allowed warnings are still printed. Publish gate 7 runs `--strict` with both allowances.

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

### Registration — RC001, RC003, RC004, RC011, RC033

A rail is reachable only if the gateway agrees it exists, in four separate places: `APP_CATALOG`
(so the shell draws a rail item), `app_<id>_url` (so `/<id>/api/*` proxies somewhere), `<id>_dist`
plus both mapping helpers (so the federated bundle is served), and a compose service on the
declared port. Miss one and the rail is registered but unreachable, with no error anywhere.

**And a fifth place, in a different file: the gateway SERVICE's own compose block.** Those four
registries are the gateway's Python defaults, which are written for standalone dev — host paths
and `127.0.0.1`. Inside the container both are wrong, so compose overrides every one of them per
rail: `PLATFORM_APP_<ID>_URL`, `PLATFORM_<ID>_DIST`, a read-only mount delivering that path, and
`depends_on`. RC033 owns these. It exists because the first four were all correct for
`watch-builder` and the rail was still invisible in the running stack. It reads each one as a
line of the gateway's block, so a commented-out or blank line does not count, and it checks only
the rails in the gateway's `enabled_apps` default: any other rail is a counted skip, and a default
it cannot read as a literal is a warning rather than a reason to exempt every rail.

Three optional manifest keys exist because this platform's rails do not all fit one mould, and
guessing wrong means a rule silently verifies nothing:

- **`dir`** — the directory name when it differs from the catalog id. The one case is the
  withheld `iep-goals` rail, whose directory is not named for its id. (The catalog's `iep` tile is
  IEP Present Levels, a different app.)
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
**every rail except ai-voice, meeting-atlas and workstation declares a `status_route`** (co-worker
at `/api/models`, every other one at `/api/capabilities`; `tools/tests/test_doc_claims.py` fails
when the manifests stop matching this sentence). Both rules run. A
rule that skips reports as a skip now, not as a pass, which is what made the difference visible.

**The envelope.** The ROUTE is not standardised (a rail with more to report serves a richer
`/api/capabilities`), but the model part of its answer is, and it is what the template's
`modelstate.resolve()` returns (`tools/rail_templates/modelstate.py.tmpl:193`):
`{"broker": "ok" | "unreachable", "models": [...]}`, one entry per slot carrying `slot`,
`label`, `role`, `model`, `state` (the four values below), `upstream`, and the `backend` /
`busy` pair described under the next heading. The unreachable branch carries the same keys, with
every state `missing`, so a consumer never meets a `KeyError` on the day the broker is down. RC008
flags the retired boolean `"broker_reachable"`. `docs/rail-manifest.schema.json` points here for
`status_route`; it used to point at a `model_status_envelope` section that never existed.

The rules hold the four-state contract (`missing` / `cold` / `warming` / `loaded`) consistent.
Red-vs-blue is the whole point of four states: red needs an `ollama pull`, blue just needs someone
to ask a question, and a two-state dot collapses them into one useless "off".

The dot colours are shared tokens, and a rail should not paint its own: `web/src/styles.css`
defines `--chip-missing` / `--chip-cold` / `--chip-warming` / `--chip-loaded` and `--info` (the
cold blue, which `--chip-cold` reads), each with a light value on `:root` (`styles.css:37-41`) and
a dark value in both dark `:root` blocks. `--info` was used and never defined until 2026-10-07, so
every cold dot was painted by the `#58a6ff` fallback literal inside the rule. The dots stopped
reading `--critical` / `--warning` / `--good` at the same time: those are one colour for both
modes and missed 3:1 against the page on many palettes. `web/tests/model-chips.test.js` holds
every dot to 3:1 on every palette in every mode, and `web/THEMING.md` rule 4 says why one colour
per state cannot get there.

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

### Who answers a slot: `backend` and `busy`

The broker can serve an owner's text call from the owner's own Claude CLI subscription instead of
the local GPU (`Broker.cli_decision` in `services/broker/app/broker_cli.py`). Every chip entry from
`modelstate.resolve()` therefore carries two more keys, in the `unreachable` branch as well as
the `ok` one:

- **`backend`**: `"claude"` when the broker will send this slot's call to the CLI, else
  `"ollama"`. Unreachable is always `"ollama"`: the CLI runs inside the broker. A third value,
  `"external"`, is never `modelstate.resolve()`'s: a rail reports it for a slot it serves from an
  endpoint of its own configuration instead of the broker (openmaic's `OPENMAIC_LLM_BASE_URL`).
  The broker never sees such a slot, so the entry also carries `upstream: "external"` and
  `busy: false`, and its `state` is the rail's own guess (`cold`: configured, residency unknown).
  The shell reads it as that: the chip says "external" instead of a residency word, with no
  inline upstream and a neutral dot. A rail that builds such an entry itself still sends every
  key `resolve()` sends.
- **`busy`**: true while the CLI is answering one of THIS viewer's calls for this role, read
  from `/v1/status["cli"]["active"]`. A CLI call takes no GPU gate and joins no job queue, so
  `warming` can never show it. Another owner's call does not light it.

`state` keeps its meaning on a CLI slot. It is still local residency, and that is the health of
the fallback the broker serves the call from when the CLI refuses it. These are separate fields
rather than a fifth state because a web bundle that has not been rebuilt looks the state up in
its text table (`MODEL_STATE_TEXT[m.state]` in `web/src/ModelChips.tsx`), so an unknown state
renders as "undefined" on every rail at once, while an unknown key is ignored. `ModelChips` renders
both: a `backend: "claude"` slot reads "Claude", and `busy` adds "working" to the text, the dot
class and the tooltip (`chipStateText`, `chipDotClass` and `chipTitle` in
`web/src/model_chip_view.ts`).

The template's `cli_serves()` rebuilds `cli_decision` from what `/v1/status["cli"]` exposes,
because a rail cannot import the broker. `services/broker/tests/test_cli_chip_equivalence.py`
runs both against a real `Broker` across every owner, role, requester and blocker combination,
and `services/broker/tests/mutate_cli_chip.py` mutates the template to prove that test fails.

**A slot reads `"claude"` only if the rail opts it in**, with a module-level
`CLI_SLOTS = ("<slot>", ...)` and `resolve(specs, requester=<viewer>, cli_slots=CLI_SLOTS)`
(the form is fixed; see the end of this section). A rule keyed on the role alone
would be wrong, for two reasons. A role can serve more than one rail (two rails' slots in
`RAIL_MODEL_SLOTS` may name the same `@role`), and whether a call carries a
requester is decided per CALL SITE, not per role: recipe-book's background icon-subject batch
carries none on the same role its assistant names the user on (below). The contract for
listing a slot in `cli_slots`:

1. The call site behind the slot passes `requester` on a **`/v1/chat` or `/v1/chat/stream`**
   call. Those are the only broker paths that carry a requester: the OpenAI-compatible surface
   and embeddings never reach the CLI, so never list an embedding slot or one whose calls go
   through `/openai/v1`. A streaming slot may be listed, under two conditions its reader already
   meets if it reads the broker's stream the way the shipped readers do. It must keep skipping
   every frame with no `message.content`: the broker leads with a `{"cli": ...}` frame when the
   CLI answers and a `{"cli_fallback": ...}` frame when it fell back before the first token. And
   it must keep raising on `error`: a frame with `"done_reason": "cli_interrupted"` means the CLI
   stopped AFTER the first token, so the text already shown is incomplete and no local answer
   follows; the rail reports a failure there, never a finished answer. Its first-frame read
   timeout must outlast `BROKER_CLI_TIMEOUT` plus the local model's time to its first token
   (the fallback's first frame can come that late); once text flows the broker sends a frame at
   least every 60 s or ends the stream with `cli_interrupted`. A `format` or images on a stream
   are answered buffered inside it, as one content frame. A vision slot may be listed when its
   photo goes on such a call, Ollama-style (`images` on a message), with the requester:
   recipe-book's `vision` is. Whether
   the CLI reads the photo is the broker's decision. A broker whose CLI client refuses images
   (`vision_unsupported`) answers the call from the local model, as it does any CLI failure,
   while the chip reads Claude, so a vision role is switched on only on a broker that sends
   images to the CLI. Likewise a streaming slot's role is switched on only on a broker whose
   `cli` block says `streams`: an older one answers every streamed call locally.
2. The rail passes `resolve()` the same identity it passes as `requester`. In terminal-fun and
   watch-builder that is `ident.user`, which is also what a watch-builder job records as its
   owner; recipe-book and co-worker pass both through their own `cli_requester`.
3. The `requester` a job's calls carry is the job's owner **only when the owner's own material
   is in the job**, decided once per job by the rail, never per call site. The broker sees a name
   and nothing else, so it cannot tell an owner's own work from an owner working on a workspace
   peer's data. A watch-builder job carries only what its submitter sent in the request, so its
   name is the submitter's, fixed when the job is created (`jobs.Run.requester`). A rail whose
   jobs can hold another user's material records on the job whose material it is, and passes
   `None` when that is not the owner. Anything else is `None` and local.
   So the chip describes the viewer's OWN work; a job holding someone else's material stays on
   the local model whatever the chip says, and `state` is the honest description of that model.

Leaving both arguments out is always safe and reads `"ollama"` everywhere. Opted in:
recipe-book's `assistant` and `vision`, terminal-fun's `assistant`, watch-builder's `designer`
and co-worker's `synthesis`, whose calls are buffered; ai-playground's `generation` and the
`reasoning` slots of smb-partner-enablement, gemini-cx and openmaic (openmaic only where
`OPENMAIC_CLI_REQUESTER` is set), whose answers stream and whose other calls on the same role
are buffered; and the withheld rails that opt in. Any other rail opts a slot in the same way, under the
same three rules. For a rail that sends images through `packages/edu-media-core`,
`broker_media.chat_json` sends an image call's requester exactly as the caller passed it, and on
a named call fits any image the CLI path would refuse (over the broker's 5,000,000-character
image cap, or over the API's 8000 px; `fit_image_for_cli`); every other image, and every image
on a nameless call, goes as rendered.
Three of those rails pass the person asking (`ident.user`) on every interactive call on an
opted-in slot's role: recipe-book's `assistant` and `vision`, the photo import (None while an
admin acts as another user through `?owner=`, `recipe_book.api.deps.cli_requester`; its
background icon-subject batch on the assistant's role passes none, and its icon images go to
`/v1/image`, which carries no name), terminal-fun's `assistant` and
watch-builder's `designer`. Each one's `broker.py` keeps the name only on its own slots' roles
(`_cli_requester`), so a client-chosen role, such as recipe-book's `model` override on
`/api/assistant`, never carries it; that route also drops a client-picked `@recipe-vision`, which
is not its slot's role (`recipe_book.api.routers.assistant._own_role_requester`). A call that carries a requester must be given a broker
timeout that outlasts `BROKER_CLI_TIMEOUT` plus the local answer the broker falls back to, or a
hung CLI call fails the rail instead of falling back. watch-builder's design and variations,
which make several calls each, therefore run as background jobs the page polls (`jobs.py`)
rather than inside one HTTP request, which the gateway's 600 s read timeout would bound as a
whole: every call of a job waits the full `broker.CHAT_TIMEOUT` and carries the job's owner,
the person who submitted it, captured when the job is created (`jobs.Run.requester`). A job is
bounded by a check before each call (cancelled, past `jobs.JOB_CEILING_S`, or unread by its
owner for `jobs.LEASE_S`, which the page's poll and its keep-alive renew, so a job whose tab
was closed starts no call five minutes on), never by trimming a call's timeout.
The broker cannot enforce that timeout rule, because it never sees a rail's client timeout. So
whoever wires a slot checks the rail's broker timeout against the `cli.timeout_s` that
`/v1/status` reports (`BROKER_CLI_TIMEOUT`), with room left for the local answer. The
maintainers' smoke sweep (`tools/rail_smoke.py`, maintainer tooling, not in the public snapshot)
repeats that check on their deployment from its `CLI_RAIL_TIMEOUTS` registry, which a rail that
wires a slot joins in the same change.

co-worker opts in by its own `cli_requester`, and only on its slot's own role (a call on any
other `@role` carries no name, so it cannot ride past the slot's switch): `synthesis` names the
viewer only when they are `CO_WORKER_INBOX_OWNER`, because the inbox has no owner field of its
own; the module entry point and `tools/ab_synthesis.py` pass none. A slot left out of
`CLI_SLOTS` must name nobody, even when its role is made eligible some other way, or its calls
reach Claude while its chip says local. A rail that instead names its requester on every call of
a job, whatever role the call takes, is held to the same rule from the other side: every role
such a call reaches is a chip slot of its own, in `CLI_SLOTS` and in `RAIL_MODEL_SLOTS`. edu-suite
is that rail: its TeachTown builder's last-resort vocabulary draft calls `@iep` while the rest of
its content calls `@edu`, so its chip has a `vocab-fallback` slot on `@iep` beside `content`, and
with `@iep` switched on and `@edu` off the chip says Claude for that slot alone. Every rail that
opts a slot in has a
`mutate_cli_requester.py` beside its `test_cli_requester.py` that mutation-tests those guards:
each published one (recipe-book, terminal-fun, watch-builder, co-worker, ai-playground,
smb-partner-enablement, gemini-cx and openmaic), and each withheld one as well.

A role must also be eligible at the broker before any of this reaches the CLI: listed in
`BROKER_CLI_ROLES`, or switched on in the admin Rails tab (super-admin only), which writes
`BROKER_CLI_ROLES_FILE` and decides alone once that file exists (`services/broker/README.md`).
A chip follows a switch within one poll and with no rail change, because `cli_serves` keys on
`/v1/status["cli"]["roles"]`, which the broker computes with the same function its routing uses.
The same tab chooses which Claude model a role's calls use: one of `BROKER_CLI_MODELS`, kept in
the switch file beside the role's switch, else `BROKER_CLI_MODEL`, else the CLI's own default. A
rail passes nothing for it and needs no change when it moves, and the chip says "Claude" without
naming the model.

A rail that opts a slot in also sets `"cli": True` on that slot in the gateway's
`RAIL_MODEL_SLOTS`, in the same change. The Rails tab offers the Claude switch ON only for such a
slot (the gateway answers 409 otherwise), and
`apps/platform/backend/tests/test_rails_cli_switch.py` holds the flag equal to the rail's
`CLI_SLOTS`, read from the rail's source. That test reads one form: a single module-level tuple
of string literals named `CLI_SLOTS`, which the chip passes as `cli_slots=CLI_SLOTS` or through a
filter over it, `tuple(s for s in CLI_SLOTS if <rule>)`. A filter narrows the literal at runtime
and never adds to it (a rule of the rail's own, such as dropping a slot whose configured role is
not the one it was wired on). The test fails the forms listed here when it finds them in the
rail's source: an inline or computed `cli_slots`, `cli_slots` passed by position, `resolve`
reached through `getattr`, a second binding of `CLI_SLOTS` (an assignment, a parameter, an import, an attribute
write such as `app.CLI_SLOTS = ...`), the name spelled as a string (which is how
`globals()["CLI_SLOTS"]` and `setattr(module, "CLI_SLOTS", ...)` reach it), or a second module
declaring one; `tests/test_rails_cli_switch.py` pins a few more (a star import, `resolve` kept
in a variable, `**` arguments, a declaration no chip passes). It does not catch everything:
forms it misses include a `match` pattern that rebinds `CLI_SLOTS` and `resolve` reached through
`__dict__`, and nothing the source does not show, because the test reads source, not a running
module (a name computed at runtime, `exec`, a module outside the rail). Each rail's own chip
test (its `test_cli_requester.py`) is the backstop for those. A chip slot the
gateway does not list needs no flag, but it must be one of the rail's `rail.json` `model_slots`,
and not one that `rail.json` puts on the admin panel (`admin_panel`): the switch on its role moves
its calls, and with no row the tab names no slot of the rail beside that switch.

A wired slot whose rail streams on its role also sets `"stream": True` there, in the same change:
any of the rail's calls on the role, even if the others are buffered, because the switch is per
role. The Rails tab then offers the switch ON only on a broker whose `cli` block says `streams`
(the gateway answers 409 otherwise, and marks every slot on the role with the reason, a slot of
another rail that borrows the role included), and the same test holds the flag equal to the
rail's source: the roles its broker facade's stream function (the one that POSTs to
`/v1/chat/stream`) names a requester for. It reads the chain the shipped facades use: the value
under `"requester"` comes from a module-level gate (`_cli_requester`), the gate compares the
model with one name (`model == config.RAG_MODEL`), and that name is given an `"@role"` default
somewhere in the rail (`os.environ.get("ENV", "@role")`, or a settings field). A stream function
that names a requester any other way fails the test rather than reading as "does not stream".

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
| course-builder | 8901 | 5351 |
| servicenow | 8910 | 5360 |
| watch-builder | 8920 | 5370 |
| meeting-atlas | 8740 | 5340 |

Every manifested rail has a row here with its `rail.json` `ports`; `tools/tests/test_doc_claims.py`
fails when one is missing or its numbers differ.

openmaic's `openmaic-app` sidecar is not in this table: it is not a rail, it has no manifest, and
it `expose`s 3000 on the compose network only, never to the host. It is reachable solely through
the rail's own reverse proxy, which is the point of running it as a sidecar.

### Federation — RC010

`federation_name` is the JS identifier the shell imports as `<name>/module`; `base` is `/<id>/`,
matching where the gateway serves the bundle. A mismatch means the rail cannot mount.

RC009 and RC010 read the `vite.config.ts` that sits beside the rail's frontend sources, found the
same way RC017 finds them, so the config of a frontend relocated by `package_path` (edu-suite's)
is checked.
Until 2026-10-07 both rules hardcoded `<root>/frontend/`, read nothing on edu-suite, and were
counted as having run. A rail whose config cannot be found now gets a WARN from each rule rather
than a silent pass, because every manifest declares a dev port and a federation name and so
always has something these rules should have been able to read.

### Theming — RC012

Full rules in [`web/THEMING.md`](../web/THEMING.md). RC012 fails a selector that can match outside
the rail, in `theme.css` and in every other stylesheet of the rail's own that the build ships: a
`.css` any module under `frontend/src` imports, and a stylesheet `<link>` or inline `<style>` in
`frontend/index.html`, which Vite builds by default. RC032 reads the built bundle itself,
`dist/assets/*.css`, and fails any global rule there (`html`, `body`, `:root`, `#root`, `*`, a bare
element), wherever it came from. Both exist because remoteEntry.js injects every stylesheet the
build emits (with `cssCodeSplit: false`) into the shell's document and nothing removes it: bouquet's
dev page sheet, imported by `main.tsx`, inset the whole platform by 20px while RC012 read only
`theme.css`. RC032 skips an unbuilt rail under `unbuilt-tree`, and judges a stale bundle, because
that is what the gateway serves until it is rebuilt.

The checker also enforces the two failures that actually break a palette:

1. **Never redefine `--accent`, `--muted`, `--good`.** They inherit the chosen palette. Derive a
   *local alias* instead (`--ac`, `--mut`) with a standalone fallback. Note that
   `--accent: var(--accent, …)` is not a workaround — a self-referential custom property is a
   dependency cycle the spec makes invalid at computed-value time, so the token becomes unreliable
   throughout the rail rather than merely unpalettable.
2. **Status colours come from semantic tokens, not literals.** `--good` / `--warning` /
   `--critical` are defined once on `:root` and deliberately not redefined per palette or per mode:
   status must mean the same thing and read the same way on every palette. The model-chip dots
   are the one exception, and a rail should not paint its own: they read `--chip-*`, which keep
   one hue per state but have a light and a dark value, because no single colour reaches 3:1
   against every palette's page in both modes (see the RC007/RC008 notes above).

A rail that renders standalone (finance does; it never loads the shared stylesheet) puts its
fallback definitions in its **standalone-only** stylesheet, not in the `theme.css` the shell also
loads. Defining them in `theme.css` shadows the shared tokens for everyone. Standalone-only means
no module imports it: an import from `main.tsx` ships it in the bundle all the same. The rails
that have one (ai-playground, ai-voice, bouquet, finance) link `src/standalone.css` into
`index.html` from a Vite plugin with `apply: 'serve'`, so only `npm run dev` loads it.

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
  **Admin → Rails** (the slot's `@role`) and its VRAM residency. Each entry also says who answers
  the slot (`backend`, `busy`); see "Who answers a slot" above. A rail with **no foreground
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
follows a relocated frontend via `package_path`, so edu-suite's, which sits inside its dashboard
app, is covered too — it used to be silently skipped, which read like a pass.

### Identity: fail closed

The gateway authenticates every request and sets `X-Platform-User` (and `X-Platform-Admin`),
stripping any client-supplied copy first. **A request without that header did not come through
the gateway.** In this topology that means a sibling container on the compose network, so the
only safe answer is `401` — not "anonymous", and certainly not "admin".

**The converse does not hold, and the whole model rests on that.** A request that DOES carry the
header is believed from whoever sent it: identity headers are trusted **from the network**, so a
rail is exactly as safe as the set of things that can open a socket to it. For every other rail
that set is the gateway and sibling backends running this repo's code; openmaic-app, the
third-party app the openmaic rail wraps, has its own network (below). terminal-fun is the one
rail that runs code its users choose (a roguelike's shell escape, tmux panes: a real bash), and
on the shared default network that shell could be any user, admin included, to every rail:
measured 2026-10-09 from inside it, recipe-book answered 401 bare and 200 with
`X-Platform-User: admin`. So terminal-fun has two layers of its own:

- its own compose network, `terminal_fun_net`, joined by the gateway and nothing else (both
  compose files; the gateway also stays on `default`), so no other rail resolves or routes from
  inside it;
- a peer check in its backend (`GatewayOnlyIdentity`, `rails/terminal-fun/backend/terminal_fun_app/main.py`):
  any `x-platform-*` header from an address that is not the gateway's is refused (403, or a
  websocket close before accept), because no network keeps the shell off its own loopback. The
  image runs uvicorn with `--no-proxy-headers`, since uvicorn otherwise rewrites the client
  address from `X-Forwarded-For` for a `127.0.0.1` peer.

A new rail that hands its users a shell, an interpreter, or any other way to open a socket takes
both. Copy terminal-fun's shape and its tests (`tools/tests/test_terminal_fun_network.py`,
`rails/terminal-fun/backend/tests/test_gateway_peer.py`); `identity.py` stays the generated,
byte-identical file it is on every rail, and the peer check lives beside it in the rail's own app.

openmaic carries the backend half too (`rails/openmaic/backend/openmaic_app/api/gateway_only.py`),
for the other reason a rail can need it: it shares the network with third-party code it wraps
(openmaic-app), and its identity reads decide more than access, since the proxy's record of who
is using the app decides whose Claude subscription the LLM shim may name. Its copy DROPS the
`x-platform-*` headers of a non-gateway request instead of refusing it, because openmaic-app is a
legitimate non-gateway caller of the shim, whose service-token check must keep deciding; every
gated route then answers that request 401. A rail that wraps a third-party app the same way
copies this one (`rails/openmaic/tests/test_gateway_peer.py`).

The network half is the app's: openmaic-app joins only `openmaic_app_net`, a compose network it
shares with the openmaic rail and nothing else (both compose files; the rail also stays on
`default`, where the gateway reaches it). From inside the app the one service that resolves is
`openmaic`, which proxies it and hosts the shim it generates through, so no other rail can be
told an identity by it. The network is an ordinary bridge, not `internal`, because the app may be
pointed at an endpoint outside the stack (`OPENMAIC_LLM_BASE_URL`). A rail that wraps a
third-party app gives the app such a network too, and copies
`tools/tests/test_openmaic_app_network.py`.

Most of the rails got this wrong at once, which is why it is now a contract item rather
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
non-empty, and only to a tile the gateway lists for that header (`PEER_TILES`, `ROOM_TILES` in
`apps/platform/backend/platform_gateway_app/config.py`, one id a line): a rail that starts reading
one adds its tile there, or it is sent nothing. Take the one that matches your data: **peers** when a record belongs to whoever made it,
**rooms** when it belongs to a subject that outlives its creator. The parse rule is the transferable
part and it is **fail-closed and all-or-nothing** — absent, `""`, `[]`, non-JSON, a JSON object, a
bare string, or an array holding one non-string all mean *no sharing*. A partly-readable header is
one you do not understand, and honouring the readable half widens access on a guess. Both
reference implementations are in withheld rails. edu-suite's dashboard reads peers with three
small functions: `parse_peers` (the parse rule above), `visible_owners` (a list query's owner
filter: the caller, then their peers) and `may_access` (one record's owner against that same
set, so a shared row never lists and then 404s). iep-goals reads the other header,
`X-Platform-Rooms`, with its own `_parse_rooms` under the same rule, applied to positive integer
workspace ids instead of usernames. Never trim, lowercase or otherwise
normalise a username from these headers: it could map one real user onto another's rows.

Two shapes are in use and both are fine. An **app-level** `dependencies=[Depends(identity)]`
gates every route and cannot be forgotten when a route is added. **Per-route** `Depends(identity)`
leaves `/api/health` open as a liveness probe. Either way the app sets `docs_url`/`openapi_url` to
`None` (RC034, below), since FastAPI's doc routes bypass both. Two things app-level gating does
*not* cover, both found the hard way: `app.mount(...)` static mounts are not routes (terminal-fun's
`/api/webtoys` was serving 200 un-gated), and websocket handshakes should be refused *before* any
other validation so an un-gated caller cannot probe what exists by reading close codes.

### Schema and docs — RC034

**Every FastAPI app a rail builds turns off its schema and docs:** `openapi_url=None` and
`docs_url=None` in the `FastAPI(...)` call. FastAPI registers `/openapi.json`, `/docs` and `/redoc`
itself, as plain routes outside every dependency, so neither identity shape above reaches them: a
rail gated per route serves its whole API map to anything that can open a socket to it, and an
app-level gate still answers where a 404 would say nothing. Two rails were still serving them in
October 2026 while every other rail had closed them, because the sentence above was a habit that
nothing checked.

RC034 reads every `FastAPI(...)` and `fastapi.FastAPI(...)` call in the rail's Python, each on its
own (openmaic builds a second app for its LLM shim), and fails one that leaves either keyword at
its default or sets it to anything but the constant `None`. `redoc_url` is not required: FastAPI
adds `/redoc`, like `/docs`, only when `openapi_url` is set. A value the rule cannot read (a name,
a call, or `**kwargs` that may carry the keyword) is a warning, never a pass. Pin it in the rail's
own tests too: `/docs`, `/redoc` and `/openapi.json` answer 404 with an identity and without, since
a 401 would still confirm there is a schema worth asking for.

### Student identifiers — RC018

The iep-goals rail holds records about real children. Its gitignored `local/` directory keeps the
source documents out of git,
and nothing stopped the **names inside them** walking out into the code anyway — they did: golden
assertions, unit fixtures copied from a real case, and edge-case comments recording whose sheet
exhibited the bug. Cleaned up 2026-08-23; this rule is what stops it coming back. The leak is a
by-product of debugging against real data rather than carelessness, which is why a habit was
never going to be enough.

Registered against `iep-goals`, but **repo-wide**: it runs once and scans every tracked file,
because a name can land anywhere. The identifiers are read from a pseudonyms file in that rail's
gitignored `local/` directory, so the checker itself never contains them. Three consequences
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

### Routes — RC030

**No route is hidden behind an earlier, more general one.** Starlette matches routes in the order
they are declared, so `GET /api/jobs/{job_id}` above `GET /api/jobs/export.xlsx` answers the export
request itself: `export.xlsx` is parsed as a `job_id` and the export handler is never reached. That
is how job-aid's export returned a 422 while an audit reasoned about a 500 from a handler no request
could reach. RC030 reads the route decorators of each module, per router object, and fails a route
that an earlier one on the same method matches entirely (`{x}` takes any one segment, `{x:int}` only
digits, `{x:path}` the rest). Register the literal route first.

### Models outside a slot — RC031

**Every model a rail reads is a slot, or a declared exemption with its reason.** RC013 holds a
slot's in-code default to an `@role`; a model read outside every slot was invisible to it.
recipe-book read `RECIPE_BOOK_EMBED_MODEL` with the literal default `bge-m3` and declared no `embed`
slot, so the deployed box pinned its embedder while every other rail followed `@embed`, and Admin →
Rails could not even show it. RC031 fails an `os.environ.get("<X>_MODEL", "<model>")` (or
`os.getenv`) whose name is no slot's `env` and whose default is a concrete model. A model that
cannot be an `@role` goes in the manifest's `unslotted_models` with a `why`: ai-playground's NVIDIA
cloud model (the broker resolves Ollama tags, nothing else) and bouquet's SigLIP checkpoint (the one
its reference index was built with). A listed env that nothing reads fails as stale.

## The rail template: three tiers

With this many rails there is enough evidence to say which files must be identical and which
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
Today: `identity.py`, `modelstate.py`, and `logsetup.py` in a rail that carries one (it routes
the rail's own INFO lines to stderr, which uvicorn's default logging drops; call
`logsetup.configure()` first thing in the app factory). Do not edit these in place; change the
template and run `rail_template.py sync`. Line endings are normalised on the way out, because a
CRLF-only difference is drift no reviewer will ever spot.

**A rail that renders chips must carry `modelstate.py`, and an absent copy is a failure, not an
exemption.** The manifest decides: a `status_route` plus at least one slot that is not
`kind: image` (`Rail.renders_chips()` in `tools/rail_template.py`, the one place this is
derived). `check` reports such a rail's missing copy as `MISSING`, RC023 fails it, RC022
demands its facade, and `sync` writes it. Until 2026-10-07 the file's existence decided
instead, so a chip rail with no copy read as "does no model work" and every one of those checks
skipped it. One rail sat in that hole with a hand-rolled resolver that looked `@embed` up in a
role table keyed `embed` and read a `status()["models"]` field the broker does not send: with
the broker up, every chip it drew said `cold`.

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

A rail that renders no model chips (meeting-atlas, workstation, ai-voice: no `status_route`)
needs no facade and no `modelstate.py`; the rule skips it rather than demanding an empty one.

## Adding a rail

1. Write `rails/<dir>/rail.json` first. It is the input to everything else, so a rail that
   starts from its manifest cannot be born disagreeing with itself. Schema:
   `docs/rail-manifest.schema.json`.
2. `python tools/rail_template.py new <id>` — creates the tree and the tier-1 files.
3. Write the tier-3 files it lists: `broker.py` (with the canonical surface),
   `config.py`, `api/app.py` (require `Depends(identity)` app-wide and set
   `docs_url`/`openapi_url` to `None`, RC034), and `tests/test_auth.py` asserting the 401.
4. Register it: `APP_CATALOG`, `app_<id>_url`, `<id>_dist`, both mapping helpers in the gateway
   config, `RAIL_MODEL_SLOTS` if it has panel slots, a compose service, and the shell's `lazy()`
   + `remotes.d.ts` + mount branch. RC003, RC004, RC006, RC010, RC011 and RC016 each check one
   of these, so a missed step is a named failure rather than a rail that silently 404s.
4b. **Register it a second time, in the GATEWAY's own compose block** — `depends_on`,
   `PLATFORM_APP_<ID>_URL`, `PLATFORM_<ID>_DIST`, and the read-only dist mount that delivers
   that path. RC033 checks all four. Its own step because it is a different file, a different
   service, and the one most recently missed: `watch-builder` landed with every point in step 4
   correct, conformance reporting **0 fail / 0 warn**, 112 green rail tests and a container
   verified end to end in isolation, and did not appear in the rail list at all. The gateway
   fell back to its config defaults, which are HOST paths and `127.0.0.1` — correct for
   standalone dev and meaningless inside the gateway container. Nothing logged a word:
   `resolved_app_dists()` drops a missing dist silently by design, and the API proxy would have
   pointed the rail's `/api/*` at the gateway itself.
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
   `gemini-cx` were imported on 2026-08-20 with no tests at all and `-List`, two targets short
   of the rails, read as correct for three weeks. Give the suite a `conftest.py` that takes the
   offline guard, `tools/tests/offline_guard.py` (copy any rail's import of it): no test outside a
   live class may reach the network, the broker on loopback included, and one that tries errors.
   `tools/tests/test_offline_guard_coverage.py` holds every suite to it.
10. If it WRAPS a third-party app and that app serves assets from the origin root, declare
    them in `root_assets` and mirror them into the gateway's `ROOT_ASSETS`. RC029 checks it.
10b. If one request to it can carry more than a 25 MiB file and its form (several files, a
    folder, a wrapped app's own larger limit), or it wants a tighter door, declare
    `max_request_bytes` from its own upload limits and mirror it into the gateway's
    `MAX_REQUEST_BYTES`. RC035 checks it; see "Request bodies — RC035" below.
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

## Request bodies — RC035

The gateway relays every `/<id>/api/*` request body to the rail **as it arrives**, never read
whole, and stops it at the rail's ceiling. Until 2026-10-13 the relay called
`await request.body()`, so every proxied body was held in full by the one process every rail
shares before a byte was relayed, and nothing capped it: a rail's own upload limit bounds what
that rail receives, never what the gateway holds.

The ceiling is per rail because the rails' own limits differ by more than an order of magnitude:
one rail takes a whole folder (250 MiB of files) in one request, a watchface image is capped at
12 MiB, most rails stop one file at 25 MiB. One gateway-wide figure would refuse the largest
legitimate upload or bound nothing for the rest.

- **`max_request_bytes`** (optional, bytes, multipart framing included) is the rail's figure, and
  applies to every tile in its `also_serves`. Omit it and the platform default applies:
  **26 MiB**, one 25 MiB file plus 1 MiB for its form, set by `PLATFORM_MAX_REQUEST_BYTES` (a
  blank or unusable value means the default; it never stops the gateway starting). Exactly
  25 MiB would refuse a file the rail itself accepts.
- **`also_serves_default_ceiling`** (optional, a list of `also_serves` tiles) holds those tiles
  to the platform default instead of the rail's figure, for a tile whose uploads are far
  smaller than the rail's own: edu-suite takes a 250 MiB folder, its iep tile one 25 MiB PDF.
- **Set it from the rail's own limits, never below them.** The figure is a door every legitimate
  upload must fit through: several files per request multiply the per-file cap, and a wrapped
  app's own limit counts (openmaic's upstream sets `proxyClientMaxBodySize` to 200mb). Raising a
  rail's upload setting means raising this too. Say why in `notes`.
- **What the relay does with it.** A `Content-Length` over the ceiling is a **413 before the body
  is read** and before the rail is contacted. Anything else is streamed and counted, and the
  chunk that crosses the ceiling ends it with a 413, so a refusal costs at most the ceiling plus
  one chunk and the gateway holds one chunk at a time. A known length is passed on to the rail,
  so its own `Content-Length` check still works; a chunked body goes on chunked; a request with
  neither has no body and is sent with none. Nothing replays a body: the relay's client makes no
  retries and follows no redirects.

`MAX_REQUEST_BYTES` in the gateway's `config.py` mirrors the manifests, because the gateway
container cannot see `rails/` (the reason `ROOT_ASSETS` is a mirror). RC035 checks three things,
each silent on its own:

- the figure is a whole number of bytes (not `"25MB"`, a float or `true`);
- it is within **1 MiB-1 GiB**: written in the wrong unit it refuses every upload, and too large
  it bounds nothing (the schema states the same range);
- the mirror agrees for the rail's own id **and every `also_serves` tile**, and holds no entry
  for a rail that declares none or for a tile `also_serves_default_ceiling` lists (each name
  there must be one of the rail's `also_serves` tiles). The mirror is what the relay applies, so
  a figure raised in the manifest alone still refuses the upload it was raised for.

The relay's behaviour is locked by `apps/platform/backend/tests/test_proxy_request_body.py`,
which drives the real app with a client that hands the body over a chunk at a time.

## The lean installer path

RC001-RC024 and RC028-RC035 govern the **full** stack. The lean installer is a second, parallel set of
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

None open. No count is kept here, because the one that was ("`0 fail, 0 warn` across 15
manifests and 29 rules") went stale as rails were added. Measure it instead:

```bash
python tools/rail_conformance.py --strict --allow unbuilt-tree --allow pseudonyms-absent
```

That exits 0 on a conforming tree and prints the current manifest and rule counts. On a fresh
clone, run without the two allowances, it warns, and none of the warnings is a rail defect:
RC016 and RC032 are each reported inert when no rail frontend has been built, and RC018 warns
that its student-identifier scan did not run wherever the iep-goals rail is present without its
gitignored identifier list (only the box that holds the data has it). So a tree that holds that
rail shows three warnings, and a tree without it, the public snapshot among them, shows two.
Anything else that warns is a real gap and belongs in the backlog.

Both entries that used to sit here closed, and both are worth knowing about because the shape
recurs:

- *"The broker still reaches into the ai-voice rail's `native/` directory by name"* (RC020, WARN) —
  closed. The engine root is now **configuration**, supplied by the deployment as
  `BROKER_VOICE_ENGINES_DIR` and overlaid by `services/broker/voice_engines.json`
  (`voice_engines_dir` in `services/broker/app/config.py`). An empty root means no engines and
  voice reports unavailable, which is the honest state for a platform with ai-voice removed, rather than
  `/v1/voice/*` failing with no error until a job ran. Dispatch deliberately stayed in the
  broker: synthesis takes the GPU gate and evicts resident models, which is arbitration only the
  broker can do. That was the last of three such couplings; `edu_media_core` and the XTTS
  reference clips had already moved to `packages/edu-media-core`.
- *"`iep-goals` is absent from `RAIL_MODEL_SLOTS`"* — closed. It is present there
  (`apps/platform/backend/platform_gateway_app/rails_models.py`), so Admin → Rails repoints it
  per-rail like every other slot-bearing rail.

Retired from this list earlier, after being re-checked against the tree (all four were stale):

- *"One concrete model pin survives in compose: `RECIPE_BOOK_LLM_MODEL`"* — it was never a pin to
  repoint, it was dead code: every call site already passed an explicit `@role`, so the fallback
  was unreachable. Removed from compose when `model` became a required argument, and the last
  copy (a baked `ENV` in `rails/recipe-book/deploy/Dockerfile`, still naming a 24 GB model and so
  actively misleading on a small card) went 2026-08-26.

- *"No rail renders model chips"* — every rail that declares a `status_route` renders
  `<ModelChips>` (`tools/tests/test_doc_claims.py` holds that pairing), `co-worker`, `gemini-cx` and
  `smb-partner-enablement` among them. This entry used to add "and the `--info` token
  exists in the palette", which was false when written (it was used, never defined) and became
  true only on 2026-10-07; see the RC007/RC008 notes above.
- *"compose pins concrete globs for six rails"* — finance, terminal-fun, ai-playground and three
  of recipe-book's four now use `@role`; only the one above remains.
- *"five `<id>_dist` defaults point at pre-monorepo directories"* — every default is now
  `RAILS / <rail> / frontend / dist`.
