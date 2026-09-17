# OpenMAIC rail — backlog

Findings from building this rail (2026-09-16). Items marked **[platform]** are not about this
rail; they were found on the way past and belong to whoever owns that area.

---

## Open

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

### P4 [platform] — `@recipe-icon → flux-schnell` is reported as not installed

`/v1/roles` says `installed: false`. It is a media-worker backend, not an Ollama model, and the
lean profile runs with media off — so this is arguably correct and permanently red. Either way it
makes "every role resolves" unusable as a health assertion without an exception list, which is
what `verify-platform.ps1` had to do.

### P6 [platform] — `test_config_enabled_apps.py:42` asserts `len(apps) >= 14`

The default tuple holds 10 after this branch (8 before). The assertion was already failing on a
clean checkout before any of this work.

### P7 — the app image is built by hand

`openmaic-app` has no `build:` stanza because its source lives outside this repo, so
`docker compose up` will not produce it. Someone has to run the `podman build` in the README
first, and nothing says so until the rail loads and the iframe reports the app unreachable.

Options: vendor a thin build context, publish the image to a registry, or have the installer
detect the missing image and say so plainly. The third is cheapest and the most honest.

### P8 — the end-to-end check lives outside the repo

`verify-platform.ps1 -Live` in the deployment tree now drives one real completion through
shim → broker → Ollama and asserts non-empty content, and checks that the shim refuses an
un-credentialed caller. That covers the path, but it is local tooling: a clean clone has no such
test, and `run-tests.ps1` still only reaches the canned-frame unit tests.

Wants a `LiveTests`-marked pytest in `rails/openmaic/tests/` so the deselect machinery
`run-tests.ps1` already has (`-k 'not LiveTests'`) picks it up like every other rail's.

### P10 — streaming cannot reach the browser incrementally

The shim streams SSE and sets `X-Accel-Buffering: no`, but the gateway's proxy buffers whole
responses (`main.py`: `await request.body()`, non-streaming `.request()`, `Response(content=...)`).
The broker's own docstring names this trap and says rails consume `/v1/chat/stream` directly and
relay over the gateway's **WebSocket** proxy — which this rail has no surface for, since the app
it fronts is an iframe, not a React component this rail controls.

Consequence today: tokens arrive in one burst rather than progressively, and a generation longer
than the gateway's 600s client timeout 502s. Fixing it properly means either a streaming path in
the gateway proxy or a WebSocket relay; neither is a rail-local change.

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

### E5 — `127.0.0.1:1111` is not a Caddy site, and the failure is an empty 200

`deploy/Caddyfile` matches `localhost`, `platform.localhost` and the public name. A bare
`127.0.0.1` matches **no site block**, and Caddy then answers **200 with an empty body** for every
path — which the Caddyfile's own comment calls out as "the worst possible failure mode".

It cost real time twice. Probing `127.0.0.1` made every gated path look like a 200, which read as
"the SPA catch-all is winning" when the gateway was in fact returning a correct
`401 application/json` the whole time. Always probe `http://localhost:1111`.

And it made a check in `verify-platform.ps1` **vacuous for the entire session**: against an empty
body, `$r.Content -notmatch 'assets/index-'` evaluates an EMPTY ARRAY, PowerShell treats that as
false, the `throw` never runs, and "gateway serves the shell bundle" reported green while
verifying nothing. Fixed by probing a matched hostname, casting `[string]` before `-notmatch`,
asserting a non-zero body length, and giving the root-asset check a known-gated control so a
broken probe fails loudly instead of passing.

The general lesson is the one this repo already keeps learning: a check that cannot fail is worse
than no check. It applies to the gate as much as to the code the gate covers.

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
| Thinking models unhandled | A thinking model asked for JSON spends its budget reasoning and returns EMPTY content ~33% of the time. The reasoning slot is admin-repointable and `roles.json` already ships one, so this was a single panel click from live. `think=False` is now set whenever a format is requested, and left at the model's default otherwise. |
| `_same()` over-matched in the generated `modelstate.py` | `"latest" in (a + b)` asked whether the word appeared anywhere in the two names CONCATENATED, then compared only the part before the colon — so a resident `gemma3:27b-latest` turned a `gemma3:4b` chip green. Fixed in `tools/rail_templates/`, synced to all 7 rails. |
| `ai-playground` missing from `DEFAULT_ROLES` | Present in `roles.json` only, so a box with no overlay resolved `@ai-playground` to the literal string and got a 404 wrapped in a 502. |
| **P1: two rails answered `/api/capabilities` un-gated while RC021 reported green** | The rule's first check was `"401" not in blob` — a substring over ALL of a rail's Python. Any rail containing those three characters anywhere passed, whatever its routes actually did. RC021 now also locates the manifest's `status_route` handler by AST and verifies a real gate on it (app-wide, router-level, decorator, or signature). recipe-book and ai-playground are gated across ~15 routes; both websockets in ai-playground now refuse *before* `accept()`. |
| `gemini-cx/frontend` had no `src/vite-env.d.ts` | Four sibling rails ship it; without it `tsc` rejects a CSS import. Latent, one CSS import from breaking that rail's build. |

---

## Root-origin assets (2026-09-17)

Spotted from a screenshot: one broken image on the OpenMAIC landing page. It was systemic.

Next's `basePath` only rewrites URLs Next itself generates, so ~124 hand-written absolute asset
literals in the wrapped app resolved against the ORIGIN root and left the rail's namespace. They
did not 404 — the shell's client-side-routing catch-all answered `index.html` with status **200**,
so the browser showed a broken-image glyph while the network tab showed success.

Fixed by adding `root_assets` to the rail contract rather than four Caddy rules, because the
origin root is an exhaustible shared resource with a silent failure mode: two rails claiming
`/avatars/` would not error, one would simply serve the other's images — the same failure RC002
guards for ports. RC028 now checks the gateway mirror agrees, that no two rails overlap, and that
nothing claims a reserved prefix. Verified non-vacuous against all three.

Three things worth remembering:

- **The declared set must be derived, not eyeballed.** The first pass missed `/vendor/`, which is
  the only entry that was not cosmetic: `use-import-pptx.ts` loads `/vendor/maic-importer/index.js`
  at runtime, so PPTX import and video export were broken, not a logo. The README now carries the
  grep and the two traps in its output.
- **Capturing a loop variable as a default argument is a security bug in FastAPI.** Registering
  one route per prefix with `async def handler(request, _app_id: str = app_id)` makes `_app_id` a
  QUERY PARAMETER, so `?_app_id=other-rail` re-pointed the request after the entitlement gate had
  already authorised it on the path. Bound in a closure; three tests assert the routes expose no
  query params, exist per prefix, and are GET/HEAD only.
- **Root paths do not start with an app id**, which is the whole point of them — so they missed
  `app_access_gate` entirely and would have been served to anyone. The gate now resolves them to
  their owning rail first.

## Surfaced while fixing P1 — not yet actioned

### P14 [platform] — `/docs` and `/openapi.json` are open on four rails

recipe-book, ai-playground, gemini-cx and smb-partner-enablement all gate per route. FastAPI adds
its documentation routes itself, and a per-route dependency never reaches them — so the full route
inventory of each rail is readable with no identity. terminal-fun and openmaic are unaffected:
both gate app-wide and set `docs_url`/`redoc_url`/`openapi_url` to `None`.

It wants one consistent answer across four rails plus a line in the contract, which is why it was
not folded into the fail-closed fix.

### P15 [platform] — recipe-book's URL extractor is a request-forgery primitive

`POST /api/recipes/extract/url` made the rail fetch a caller-supplied URL and report the result,
reachable with no identity. Now gated, so it is no longer anonymous — but a *named* caller can
still aim it at the compose network. It wants an egress allowlist, not just a gate.

### P16 [platform] — `POST /api/nim/probe` spent credentials on demand

ai-playground's probe called the deployment's NVIDIA endpoint and reported whether the key worked.
Now gated. Worth a rate limit regardless.

### P17 [platform] — the repo venv drifts from the rails' own manifests

`python-multipart`, `cryptography`, `numpy` and `openai` are declared in rails' `pyproject.toml`
but were absent from `.venv`, so five recipe-book test files and all four ai-playground ones
errored during collection — at HEAD, before any change. `run-tests.ps1` reported those rails as
failures rather than as an environment problem, which is how it stayed unnoticed.
