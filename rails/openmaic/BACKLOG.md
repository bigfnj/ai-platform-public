# OpenMAIC rail — backlog

Findings from building this rail (2026-09-16). Items marked **[platform]** are not about this
rail; they were found on the way past and belong to whoever owns that area.

---

## Open

### P1 [platform] — two rails answer `/api/capabilities` un-gated, and RC021 passes anyway

`recipe-book:8830` and `ai-playground:8850` return **200 with no `X-Platform-User`**, probed from
a sibling container. `terminal-fun`, `gemini-cx` and `smb-partner-enablement` correctly 401.

RC021 does not catch it because its first check is
`if "401" not in blob or not reads_identity` — a bare substring over *all* of a rail's Python. A
rail with a 401 anywhere passes, no matter which routes are actually gated. The rule's own
docstring says it checks "a 401 exists for the header-less case"; what it checks is that the
characters `401` exist.

Two things to fix, separately: the two rails, and the rule. A checker that reports green over a
live violation is the worse of the two — it is why nobody looked.

Reproduce: `local-patches/verify-platform.ps1` in the deployment tree.

### P2 [platform] — the published `deploy/docker-compose.yml` is not valid YAML

The publish step deletes every *line* containing `edu-suite`, which takes out structural lines
along with the intended ones: the `dashboard:` service key, both `edu_db:` / `edu_cache:` volume
declarations, and two comment lines. `services:` is then followed by an orphaned `build:` block
and the file cannot be parsed at all.

One line was restored on this branch so the openmaic wiring could be verified. The rest is
untouched: `docker-compose config` still fails on `service "dashboard" refers to undefined volume
edu_db`, and the snapshot has no `apps/dashboard`, so that service cannot build here regardless —
while the gateway still carries `depends_on: - dashboard` and `PLATFORM_APP_EDU_SUITE_URL`.

Whether edu-suite should be fully stripped or fully restored is a call for the publish pipeline's
owner. The line-based filter is the real defect either way.

### P3 [platform] — `ai-playground` is in `roles.json` but not in `DEFAULT_ROLES`

So on a box whose `roles.json` is missing or malformed, `@ai-playground` resolves to the literal
string `"ai-playground"` and is handed to Ollama as a model name — an Ollama 404 wrapped in a 502,
not a "no such role" error. `openmaic` was deliberately added to **both** to avoid inheriting this.

Related: an unknown `@role` degrades silently in general (`config.py` `roles()` uses
`.get(name, name)`). A role that does not exist should be a loud error, not a model name.

### P4 [platform] — `@recipe-icon → flux-schnell` is reported as not installed

`/v1/roles` says `installed: false`. It is a media-worker backend, not an Ollama model, and the
lean profile runs with media off — so this is arguably correct and permanently red. Either way it
makes "every role resolves" unusable as a health assertion without an exception list, which is
what `verify-platform.ps1` had to do.

### P5 [platform] — `gemini-cx/frontend` has no `src/vite-env.d.ts`

Four sibling rails ship it. Without it `tsc` rejects `import './theme.css'` with TS2307. Latent,
not currently breaking — but it means `npm run build` for that rail depends on nobody adding a CSS
import.

### P6 [platform] — `test_config_enabled_apps.py:42` asserts `len(apps) >= 14`

The default tuple holds 10 after this branch (8 before). The assertion was already failing on a
clean checkout before any of this work.

### P7 — the app image is built by hand

`openmaic-app` has no `build:` stanza because its source lives outside this repo, so
`docker compose up` will not produce it. Someone has to run the `podman build` in the README
first, and nothing says so until the rail loads and the iframe reports the app unreachable.

Options: vendor a thin build context, publish the image to a registry, or have the installer
detect the missing image and say so plainly. The third is cheapest and the most honest.

### P8 — no end-to-end generation test

`test_llm_shim.py` covers translation against canned frames. Nothing exercises
OpenMAIC → shim → broker → Ollama with a real model. That path has already proved to hold
surprises (see the `max_tokens`/`num_predict` and list-content cases, both of which fail silently
rather than raising).

### P9 [platform] — the gateway image cannot be rebuilt while the platform is running

`.dockerignore` excluded `.venv`, `node_modules` and `dist` but not `deploy/logs/`. The broker
runs natively and writes there continuously, so the log grows while the build context is being
tarred and the whole build dies with:

    archive/tar: write too long

That error names neither the file nor the reason, and it only reproduces while the platform is
up — i.e. exactly when you would be rebuilding. `data/` had the same exposure (SQLite under a
live gateway). Both are now excluded on this branch; worth carrying upstream rather than
rediscovering.

### P10 — streaming cannot reach the browser incrementally

The shim streams SSE and sets `X-Accel-Buffering: no`, but the gateway's proxy buffers whole
responses (`main.py`: `await request.body()`, non-streaming `.request()`, `Response(content=...)`).
The broker's own docstring names this trap and says rails consume `/v1/chat/stream` directly and
relay over the gateway's **WebSocket** proxy — which this rail has no surface for, since the app
it fronts is an iframe, not a React component this rail controls.

Consequence today: tokens arrive in one burst rather than progressively, and a generation longer
than the gateway's 600s client timeout 502s. Fixing it properly means either a streaming path in
the gateway proxy or a WebSocket relay; neither is a rail-local change.

### P11 — nothing handles thinking models

A thinking model on structured work returns empty content about a third of the time at ~8x
latency — measured, and documented in the broker's own schema comments. This rail sets no `think`
parameter and does not strip `<think>` blocks, where terminal-fun has `_strip_reasoning()` for
exactly this.

Latent: `@openmaic` resolves to a non-thinking model today. But the slot is `admin_panel: true`,
and `roles.json` already ships `qwen3.6*:27b` — so the first admin who repoints it in the panel
this rail exists to honour gets `<think>` preambles as course text.

### P12 — `_same()` in the generated `modelstate.py` over-matches

`"latest" in (a + b)` tests the concatenation of both names, then compares only the pre-colon
part. `_same("gemma3:4b", "gemma3:27b-latest")` is True, so a resident 27b turns the 4b chip
green. Every rail carries this; the fix belongs in `tools/rail_template.py`, not here.

### P13 — the installer GUI fix restored the old gap rather than widening it

Rows sit at `y = 258 + floor(i/2)*24`, so the 7th tile opens a row at y=330 and `$btnInstall` is
now at y=340 — a 10px gap, exactly what it was before. An AutoSize CheckBox at 9pt/96 DPI is
~17px, so it still overlaps by ~7px and draws on top. Worth eyeballing rather than trusting the
arithmetic; an 8th rail definitely needs a real layout pass.

---

## Environment findings (this workstation, not the code)

### E1 — `~/.docker/config.json` exists as a **directory**

Every container tool that probes for Docker CLI credentials trips over it. `docker-compose`
downgrades it to a warning; **`podman build` treats it as fatal** and exits 125 with "Incorrect
function". Worked around by setting `DOCKER_CONFIG` to a directory we control. The stray directory
should be removed.

### E2 — a failed `podman machine start` wedges every subsequent start

`podman machine set --memory 16384` failed to allocate, and the aborted start left an orphaned
AF_UNIX socket at `%TEMP%\podman\podman-machine-default-api.sock`. Windows will not delete an
orphaned socket file — not as Administrator, not with the `\\?\` prefix. gvproxy then exits
immediately on every start ("cannot access the file"), and `podman machine start` reports only
`dial tcp ... connection refused`, which points at the VM rather than at the real cause.

The fix is to move the whole `%TEMP%\podman` directory aside. Worth knowing because the symptom
says nothing about the cause and reverting the memory change does not help.

### E3 — `node_modules` is syncing to OneDrive

The OpenMAIC checkout sits under `OneDrive - Accenture`, and `pnpm install` puts ~2,500 packages
there. A `node_modules` junction to a local path is **not** a workaround — pnpm calls `mkdir` on
it and fails with `ENOTDIR`. Either exclude the folder in OneDrive settings or move the checkout
off OneDrive.

### E4 — `corepack enable` needs Administrator

It writes shims to `C:\Program Files\nodejs`. `corepack enable --install-directory <user-dir>`
does the same job without elevation; the npm global bin is already on PATH and is the natural
home. The repo pins `pnpm@10.28.0` via `packageManager`, and a plain `npm i -g pnpm` installs a
different major that then fails to self-manage down to the pinned one.

---

## Done

- Rail scaffolded, registered in all 13 gateway/shell sites and all 7 deploy/installer files.
- `rail_conformance.py`: **0 fail, 0 warn** across 9 manifests / 27 rules.
- 37 backend tests passing, including the shim-mount gate and the SSE translation.
- `openmaic` role added to `DEFAULT_ROLES`, `roles.json` and `roles.lean.json`.
- Installer GUI layout shifted 24px so the 7th optional rail does not overlap the Install button.
- `.dockerignore` now excludes `deploy/logs/` and `data/` (see P9).
- `local-patches/fit-roles-to-8gb.py` replaces anchored patching of `roles.json`: that file is
  wholly owned locally, and its anchors named the values they were meant to produce, so every
  snapshot reset made them unmatchable in both directions.

## Fixed in the audit pass (2026-09-16)

A code audit after deployment found 21 items. The ones that changed behaviour are fixed and
regression-tested (57 tests, up from 45); P10-P13 above are what was deliberately left.

| Was | Why it mattered |
|---|---|
| `response_format` dropped on **streamed** completions only | A streamed JSON request got prose back and failed to parse, while the byte-identical non-streamed one worked — so it read as a model problem, not a shim bug. |
| Blocking broker HTTP inside `async` routes | Three sequential sync GETs on the event loop of a single-worker container. A sick broker stalled the reverse proxy and any in-flight generation with it, making the rail look dead. |
| `finish_reason` hardcoded `"stop"` | A reply cut off at `num_predict` reports `done_reason: "length"`; calling that a clean stop tells the client a truncated slide is a finished one. |
| Duplicate response headers comma-joined | httpx joins repeated keys. Two `Set-Cookie` headers became one corrupt value — and Next.js sets session + CSRF together as a matter of course. |
| `json_schema` unhandled; non-dict `response_format` raised | Structured output degraded to free text with a 200; a bare string raised `AttributeError` and escaped the OpenAI error envelope as a 500. |
| Images silently discarded | Upload a scanned page, ask for a class about it, and the model answered from the prompt alone — confidently, with a 200. |
| `OPENMAIC_LLM_BASE_URL` changed only the chip | Documented in five places as redirecting generation. It redirected nothing: the app's `OLLAMA_BASE_URL` was hardcoded. The chip became precisely the lie the four-state contract exists to prevent. |
| Repeated query parameters collapsed | Starlette dedupes on `.items()`, which httpx consumes: `?tag=a&tag=b` reached the app as `?tag=b`. |
| Streamed replies had no `usage`, and named the `@role` not the model | The `done` frame carrying both was discarded one line before it was read. |
| Admin → Rails showed `gemma3:4b` as the revert target | The lean value, not `roles.json`'s. Reverting "to default" quietly downgraded by 6x the parameters. |
| Per-call broker sockets closed by the GC, not the code | `aclosing()` now unwinds at the break. A course generation is dozens of these back to back. |
| Dead: `resolved_model()`, `host`, `port`, `llm_api_key` | `broker_url` was worse than unused — `Settings` read `.env` while `broker.py` read `os.environ`, so a `.env` value was honoured by everything except the module that dials. |
| No `.dockerignore` for the rail context | Host bytecode from a 3.14 interpreter copied into a 3.11 image. |
