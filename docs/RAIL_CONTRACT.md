# The rail contract

A rail is an **independent deployable**: its own image, its own dependency set, its own config
idiom, its own release cadence. That is deliberate and this document does not erode it. There is
no `rail_core` package every rail imports, and there is not going to be one.

What *is* unified is the set of **contracts** a rail has to honour to be part of the platform.
Every one of them is machine-checked:

```bash
python tools/rail_conformance.py            # exit 1 on any violation
python tools/rail_conformance.py --rules    # the enforced rule list
python tools/rail_conformance.py --rail co-worker --json
```

`--rules` is the authoritative index. This document explains the *why* behind each rule; the tool
is what the rules actually say.

## Why contracts and not shared code

Six facts about a rail are currently restated in six places: the rail's own source, its
`vite.config.ts`, its Dockerfile, its compose service, the gateway's routing and dist registries,
and the admin model panel. Sharing code would collapse those, at the cost of the independence the
rails were built for. So instead each rail **declares itself once** in `rails/<id>/rail.json`
(schema: [`rail-manifest.schema.json`](./rail-manifest.schema.json)) and the checker asserts that
every restatement agrees.

The failure mode this targets is not a broken build. Every drift found when the contract was first
written was **silent**:

- co-worker read its broker token *only* as `CO_WORKER_BROKER_AUTH_TOKEN`. So whoever wired the
  installer compose bent that one file to spell it the same way, while `docker-compose.yml` passes
  the unprefixed name to all nine services — the same rail got a token on one path and silently
  none on the other. Invisible only because the deployment's token is currently empty; the day one
  is configured, the main-compose path 401s on every chip and every synthesis pass. Fixing the rail
  alone would have moved the disagreement rather than closed it, which is why RC005 (what the rail
  reads) and RC014 (what deploy/ writes) are separate rules.
- Three of four chip rails named their model slot something the admin panel didn't. An operator
  repointed "synthesis" and watched a chip called "llm".
- Two pairs of rails claimed the same vite dev port. The second `npm run dev` to start loses, and
  its `/api` proxy quietly belongs to the other rail.
- terminal-fun and all three recipe-book slots pinned concrete model names as their in-code
  default. Compose overrode them, so the container obeyed Admin → Rails and standalone dev did
  not — which is why this bug was reported closed after only the compose half was fixed.

None of those break anything loudly. That is exactly why they need a checker rather than a review.

## When a check fails

**The manifest is the contract; the code is the defect.** Fix the code.

Unless the manifest is what drifted — a rail that legitimately moved its port, renamed a slot,
changed its wrapper class. That is a human judgement and the tool deliberately does not guess.
Update the manifest and say why in its `notes`.

## The contracts

### Registration — RC001, RC003, RC004, RC011

A rail is reachable only if the gateway agrees it exists, in four separate places: `APP_CATALOG`
(so the shell draws a rail item), `app_<id>_url` (so `/​<id>/api/*` proxies somewhere),
`<id>_dist` plus both mapping helpers (so the federated bundle is served), and a compose service
on the declared port. Miss one and the rail is registered but unreachable, with no error anywhere.

### Broker access — RC005, RC013, RC014

**The broker token is `BROKER_AUTH_TOKEN`, unprefixed.** It is one platform-wide shared secret,
not a per-rail setting; compose hands every rail the same value. A prefixed alias is fine — and
should be listed *first* so a rail-specific override still wins — but the unprefixed name must be
among the names actually consulted (RC005), **and** every compose file must pass it under that
canonical name (RC014). Both halves are required: a rail that only accepts its prefixed spelling
invites a compose file to be bent to match, and then the rail is correct under that file and
tokenless under the other.

**Model references are `@role`, in the in-code default, not just in compose.** Admin → Rails
repoints a role in `roles.json` (hot-read, no restart). A rail that pins a concrete name silently
ignores the panel: the admin repoints the role, the rail keeps its pin, and nothing reports the
disagreement. Checking only the compose value leaves standalone dev pinned, which is how this
class of bug survives its own fix. Deliberate exceptions set `pinned_default_ok` with a `note`;
the one shipped exception is `RECIPE_BOOK_LLM_MODEL`, a fallback with no role of its own.

### Model slots and the chip contract — RC006, RC007, RC008

A slot id is one identity with three views: the manifest, the rail's own chip code, and the
gateway's `RAIL_MODEL_SLOTS`. They must agree, because the slot id is the only thing connecting
the control an operator changes to the readout they then look at.

Embedders, TTS and STT set `admin_panel: false` — that panel only surfaces chat/vision/image — so
they correctly have no `RAIL_MODEL_SLOTS` counterpart.

**The four states are the cross-rail visual language:**

| State | Colour | Token | Meaning | What an operator does |
|---|---|---|---|---|
| `missing` | red | `--critical` | not installed in Ollama | `ollama pull` |
| `cold` | blue | `--info` | installed, not resident | nothing — just ask a question |
| `warming` | orange | `--warning` | a broker job is waiting on it | wait |
| `loaded` | green | `--good` | resident in VRAM right now | — |

Red vs blue is the whole point: those are the two situations that need different responses. A
two-state `resident ? on : off` dot collapses them, which is why smb-partner's chips looked like
everyone else's while meaning something narrower.

**Image slots resolve against the media worker, not the model list.** An image backend
(`flux-schnell`, `sdxl-turbo`) is a HuggingFace pipeline on the broker's media worker, not an
Ollama tag, so it never appears in `/v1/models`. Resolving one the normal way reports `missing`
forever — a red dot on a feature that works. recipe-book's slots therefore carry a `kind` and
image slots map like this:

| State | Image-slot meaning |
|---|---|
| `missing` | media is disabled on the broker (`BROKER_MEDIA_ENABLED=false`) — nothing can render until an operator changes that |
| `cold` | media enabled, nothing rendering |
| `warming` | a job naming this backend is queued on the gate but not yet running |
| `loaded` | a render for this backend is running *right now* |

**For an image slot, `cold` is the healthy steady state.** The media worker is a short-lived
subprocess that *exits to reclaim VRAM* after every render, so an image backend is green only
during a render and blue the rest of the time. A chat slot sitting cold means "nobody has asked
yet"; an image slot sitting cold means "working as designed". Say so in the tooltip — an
unexplained permanently-blue dot is indistinguishable from something broken.

**Resolution order is part of the contract.** `loaded` is checked *before* `warming`: a resident
model that also has a job in flight is loaded-and-busy, not warming. And `:latest` tolerance is
not optional — Ollama reports an untagged pull as `:latest`, so a role resolving to `bge-m3` must
match a loaded `bge-m3:latest`. The broker's own `roles` payload gets this wrong and reports
`installed: false` for a model that is installed and resident, which is why each rail compares for
itself rather than trusting that flag.

#### The envelope

The **route** is not standardised — a rail with more to report than models legitimately serves a
richer `/api/capabilities` (gemini-cx and smb-partner both add corpus and voice state). The
manifest's `status_route` records which. What *is* standardised is the payload:

```json
{
  "broker": "ok",
  "models": [
    { "slot": "synthesis", "label": "Synthesis",
      "role": "@co-worker-synthesis", "model": "gemma3:4b", "state": "cold" }
  ]
}
```

`broker` is `"ok"` or `"unreachable"` — never a boolean. `state` is one of the four names — never a
boolean `resident`. A rail may add sibling keys (`corpus`, `voice`, `items`); it may not rename
these two or change their shapes. When the broker is unreachable the endpoint still returns 200
with `broker: "unreachable"`, because a header must render with the GPU layer down.

`recipe-book`'s `GET /api/models` used to be a passthrough of the broker's installed-model
inventory — the same route name as the chip endpoint, meaning something entirely different. It
had no callers anywhere in the repo, so it now serves the chip contract like every other rail's
and the inventory moved to `/api/broker/models`. One fewer name meaning two things.

#### Liveness — RC015

**The payload being right is only half of it. The rail has to keep asking.** The four states
describe residency *right now*, and residency changes with nobody touching the UI: the broker
evicts on a `keep_alive` expiry, and asking a question warms a model back up. A one-shot fetch in
a mount effect renders a state that is correct for about a second and silently wrong from then on.

smb-partner shipped exactly that: `getCapabilities()` in a `useEffect` with an empty dep array and
no interval. Its chips sat on `cold` for a model that was loaded and actively answering, while the
shell's own top-bar widget — which does poll — correctly showed it resident. **Every other rule
here passed on that rail**, because the envelope shape was perfect. Liveness is a property of the
caller, not the payload, so no amount of shape-checking would have found it.

Poll the status route on a **6 s** interval, and clear it on unmount. That cadence matches the
shell's own status polling, and the warming window is ~7 s on this box, so an orange transition is
actually visible rather than stepped over.

### Ports — RC002, RC009

Backend and dev ports are unique across **every** rail in the tree, manifested or not. Checking
manifests only against each other would leave the tool blind to exactly the rails not yet under
contract, and report green while doing it — the first attempt at fixing the 5240 collision moved
recipe-book onto 5250, which unmanifested ai-playground already had.

| Rail | Backend | Dev |
|---|---|---|
| edu-suite | 8800 | 5210 |
| recipe-book | 8830 | 5220 |
| workstation | 8720 | 5230 |
| terminal-fun | 8730 | 5240 |
| ai-playground | 8850 | 5250 |
| co-worker | 8860 | 5260 |
| smb-partner-enablement | 8870 | 5270 (mobile 5261) |
| gemini-cx | 8880 | 5280 |

### Federation — RC010

`federation_name` is the JS identifier the shell imports as `<name>/module`; `base` is `/<id>/`,
matching where the gateway serves the bundle. A mismatch means the rail cannot mount.

### Theming — RC012

Full rules in [`web/THEMING.md`](../web/THEMING.md). The checker enforces the two failures that
actually break a palette:

1. **Never redefine `--accent`, `--muted`, `--good`.** They inherit the chosen palette. Derive a
   *local alias* instead (`--ac`, `--mut`) with a standalone fallback. Beyond breaking
   inheritance, `--accent: var(--accent, …)` is a self-referential custom property — a dependency
   cycle the CSS Variables spec makes invalid at computed-value time, so the token becomes
   unreliable throughout the rail rather than merely unpalettable.
2. **Status colours come from semantic tokens, not literals.** `--critical` / `--info` /
   `--warning` / `--good` are defined once on `:root` and deliberately not redefined per palette
   or per mode: status must mean the same thing and read the same way on all 63 palettes.

`--info` was added to the shared token set for this contract. The four-state chip needs four
stable colours and the design system only had three, which is how four copies of the same literal
blue ended up outside the palette system.

## Adding a rail

1. Write `rails/<id>/rail.json`.
2. Register it: `APP_CATALOG`, `app_<id>_url`, `<id>_dist`, both mapping helpers in the gateway
   config, `RAIL_MODEL_SLOTS` if it has panel slots, a compose service, and the shell's
   `lazy()` + `remotes.d.ts` + mount branch.
3. Read `BROKER_AUTH_TOKEN` unprefixed; reference models as `@role`.
4. Serve the model-status envelope if the rail has model slots.
5. Scope styles under the declared wrapper; derive local aliases.
6. `python tools/rail_conformance.py` until clean.

## What is deliberately not unified

- **`modelstate.py` is duplicated per rail, on purpose.** Four near-identical copies, each on its
  rail's own transport. They are held in agreement by RC008, not by a shared import. If a shared
  Python package is ever introduced, this is the first thing that should move into it — but the
  cost of that package is a dependency every rail must install, and that is a bigger change than
  the duplication it removes.
- **Config idioms differ** (pydantic-settings with a rail prefix; bare `os.environ` module
  constants). Both are fine. The contract governs the *names and values*, not the mechanism.
- **Backend layouts differ** (`src/<pkg>/` with a `create_api()` factory vs
  `backend/<pkg>_app/` with a module-level `app`). The manifest's `layout` records which so the
  checker looks in the right place.
- **Per-rail `broker.py` clients**, `api.ts` fetch helpers, and `theme.css` are still forked. That
  is real duplication (~940 lines of broker client alone) and a candidate for a future shared
  layer — but it is *consistent* duplication now, which is the prerequisite for extracting it
  safely later.

## Known gaps

- **`ai-playground` renders no model chips.** Unlike recipe-book's former gap this one is at
  least defensible: the RAG demo can route generation to NVIDIA's cloud NIM instead of the local
  broker, so a chip reporting local residency would be actively misleading while the NIM toggle
  is on. Wiring chips there means teaching them to say "not this rail's problem right now",
  which is a design question rather than an oversight.
- **`ai-playground`'s generation slot keeps a pinned in-code default** (`nemotron-3-nano:4b`
  rather than `@ai-playground`) under `pinned_default_ok`. Deliberate: the standalone demo is
  meant to be end-to-end NVIDIA before anyone touches the NIM toggle, and the container
  overrides it to the role anyway. Waived rather than "fixed", because changing a demo's
  intended behaviour to satisfy a checker is the wrong direction.
- **`terminal-fun` overloads `.status`** for both its model-chip row and its terminal status text,
  with conflicting rules; the later one leaks into the chip row. Cosmetic, and left alone because
  verifying the fix needs eyes on the running rail.
