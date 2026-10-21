# OpenMAIC — interactive classroom rail

Turns a topic or an uploaded document into a class: generated slides, quizzes and simulations
delivered by an AI teacher with narration and a live whiteboard.

## What this rail actually is

**A wrapper, not the application.** OpenMAIC itself is an upstream Next.js 16 app
([THU-MAIC/OpenMAIC](https://github.com/THU-MAIC/OpenMAIC)) that cannot be a federated remote —
it owns its own router, middleware and SSR. It runs as a second container and this rail gives it
a contract-shaped presence on the platform: one catalog tile, one identity gate, one set of model
chips, and one place where `@role` becomes a real model.

```
browser ─► caddy :1111 ─► gateway :8700 ─┬─► /openmaic/assets/*   federated remote (baked into the gateway image)
                                         └─► /openmaic/api/*  ─►  openmaic :8900
                                                                  ├─ /api/capabilities   chips
                                                                  ├─ /api/llm/v1/*       OpenAI shim ─► broker :11500
                                                                  └─ /api/app/*          reverse proxy ─► openmaic-app :3000
```

Two containers, and the names matter: the **rail** must be the compose service `openmaic`
(RC026 derives its env-var stem from it); the app is `openmaic-app`.

## The two decisions worth knowing

**1. The shim exists because the broker is not OpenAI-compatible.** The broker speaks Ollama —
`POST /v1/chat`, NDJSON frames, text at `message.content`. OpenMAIC speaks OpenAI —
`/v1/chat/completions`, SSE, `choices[0].delta.content`. [`api/llm.py`](backend/openmaic_app/api/llm.py)
translates.

Pointing OpenMAIC straight at Ollama would work and would need none of this. It is the wrong
answer here: generating one course is a burst of dozens of LLM calls, and bypassing the broker's
gate means those calls evict whatever another rail had resident, repeatedly, on a card with room
for one heavy model.

**2. The app is proxied same-origin, not iframed cross-origin.** That keeps the gateway's session
cookie and `X-Platform-User` in front of every request the app makes, and lets the iframe keep
`allow-same-origin` — an opaque origin withholds the cookie and every gated subresource 401s.

The session cookie stops at THIS rail, though. `openmaic-app` is third-party code on the
platform's own origin, so `api/proxy.py` strips `platform_session` (and Cloudflare Access's
`CF_Authorization`), `Authorization`, the `cf-access-*` headers and every `x-platform-*` the
gateway set before relaying, passing on only the identity this rail resolved; and it refuses any
upstream `Set-Cookie` naming a platform cookie, which would otherwise replace the caller's
session in the browser. `tests/test_proxy_credentials.py` pins both directions.

The price: OpenMAIC must be **built** with a matching `basePath`. That is a build argument, not a
runtime setting — an image built without it cannot be re-pointed later.

**Whose identity is believed.** `X-Platform-User` counts only when it arrives from the gateway's
own address. `api/gateway_only.py` resolves `OPENMAIC_GATEWAY_HOST` (default `gateway`, the
compose service name) and drops every `x-platform-*` header a request from any other peer
carries, openmaic-app's included, before a route, the shim or the root-asset catch-all sees it.
Such a request is judged as one with no identity: every gated route answers 401, and the shim's
service-token check decides, which is how openmaic-app keeps generating. terminal-fun has the
same check (its `GatewayOnlyIdentity`) but answers 403, because nothing but the gateway ever has
a reason to call it; here the app container does. The image runs uvicorn with
`--no-proxy-headers`, since uvicorn otherwise rewrites a `127.0.0.1` peer's address from
`X-Forwarded-For`. `/api/healthz` reports the check as `peer_check`: `on` in a deployment,
`degraded` where the gateway's name resolves to this container (a dev box with the gateway on
localhost), `off (PLATFORM_STANDALONE)` in standalone dev. `tests/test_gateway_peer.py` pins it, and
`tests/mutate_peer_guard.py` (not a pytest file) proves those tests fail when any of it is broken.

**Where the app can reach.** That check protects this rail. The other rails believe an identity
header from anything on the compose `default` network, so `openmaic-app` is not on it: it joins
only `openmaic_app_net`, shared with this rail and nothing else, in both compose files, and this
rail joins `default` as well for the gateway. From inside the app the one service that resolves is
`openmaic`, at `http://openmaic:8900` (the shim, the app's default `OLLAMA_BASE_URL`), and this
rail reaches the app as `http://openmaic-app:3000` over the same network. It is an ordinary bridge,
so an `OPENMAIC_LLM_BASE_URL` endpoint on the LAN or the internet is still reachable; another
service of this stack is not. The repo root's `tools/tests/test_openmaic_app_network.py` pins the
layout, the pair's URLs and a box override that only adds `build:` to the app, and
`tools/tests/mutate_openmaic_app_network.py` beside it proves it fails when any of that is broken.

On a box, a compose file with this layout takes effect only when both containers are recreated,
**`openmaic` first**: the recreated rail joins the new network and still reaches the old app on
`default`, then the recreated app joins only the new one. The other order leaves the rail unable
to resolve the app, and the app its shim, until the rail is recreated too. The layout changes no
image, so neither needs a build for it.

## Building the app image

The app source is not in this repo. The patch script's anchors are upstream's **v1.1.3** release
(`git checkout v1.1.3` in a clone of THU-MAIC/OpenMAIC). From that checkout:

```bash
python <ai-platform>/rails/openmaic/tools/platform-basepath.py .
```

then build with the prefix baked in:

```bash
docker build -t docker.io/library/openmaic-app:latest --build-arg NEXT_BASE_PATH=/openmaic/api/app --build-arg NEXT_PUBLIC_PI_CHAT_ENABLED=false .
```

On a podman box, run the same line as `podman build`. The tag is spelled in full on purpose:
compose's `openmaic-app:latest` (the `OPENMAIC_APP_IMAGE` default) is
`docker.io/library/openmaic-app:latest` to Docker and, on a podman box, to the Docker-compatible API
compose goes through, while `podman build` given the bare name stores
`localhost/openmaic-app:latest`, which that lookup misses. Docker shows the full spelling as plain
`openmaic-app:latest`, so the one line suits both. Every tag below is spelled the same way.

Both build arguments matter. `NEXT_BASE_PATH` is the prefix. `NEXT_PUBLIC_PI_CHAT_ENABLED=false`
keeps the classroom chat on upstream's legacy runtime, the one v1.0.x used by default: from v1.1.0
upstream turns its Pi chat runtime on unless this is set, Pi needs tool calling, and this rail's
shim (`api/llm.py`) does not forward `tools`. The script prints the same command after a run, and
`tests/test_platform_basepath.py` fails if this line and the script's drift apart. From Git Bash,
prefix it with `MSYS_NO_PATHCONV=1`: otherwise Git Bash rewrites `/openmaic/api/app` into a Windows
path and `pnpm build` stops with "Specified basePath has to start with a /".

`platform-basepath.py` is idempotent and has a `--check` dry run. It makes `basePath`/`assetPrefix`
env-driven, so the same tree still builds standalone when `NEXT_BASE_PATH` is unset. It also
writes the request shim described under *Root-relative API calls* as `instrumentation-client.ts`,
adds `PLATFORM_BASE_PATH`, the key that shim reads, to upstream's own `env` object in
`next.config.ts`, and sets the app's `defaultLocale` (see *Language*). It checks that the Dockerfile
creates `/app/data` owned by the runtime user before its `USER` line, which upstream v1.1.3 does
itself (THU-MAIC/OpenMAIC#1438), and adds that line, marked, only to a Dockerfile without it (see
*The app's data volume* below); a marked line that no longer sits in the runtime stage before its
`USER` line, or whose user that line no longer names, is refused (`--revert`, then a run, writes it
again for the current stage and user). Each edit carries its own
marker, so a checkout patched by an older version of the script gets the newer edits on the next
run, and a checkout that already has every edit is left alone. Each anchor must occur exactly once,
or the run refuses. `--revert` undoes all of them and refuses, writing nothing, if a patched block
was edited by hand afterwards. Files keep their line endings. A CLEAN v1.0.x checkout is refused:
it has no `env` object for the key to go into. One that already carries the old patch is not: its
three old edits read as done by their markers and only the locale edit is added, which builds a
v1.0.x image with an English default, and `--revert` then refuses it. Put such a tree back with
git (step 2 below) or with the version of this script that patched it (step 1 below keeps that
version reachable).

### Upgrading upstream, and rolling back

This procedure was written for the move from upstream main at package version 1.0.3 to v1.1.3, and
the next upgrade should follow it too:

1. In the OpenMAIC checkout, tag the commit the running image was built from (for example
   `platform-pre-<date>`) and keep a copy of the patched files (`Dockerfile`, `next.config.ts`,
   `instrumentation-client.ts`, and from v1.1.3 on `lib/i18n/types.ts`). Keep the version of
   `tools/platform-basepath.py` that patched that tree reachable too (its commit, or a copy), since
   a newer script may refuse the older tree.
2. Put the tree back to clean upstream (`git checkout -- .` and delete `instrumentation-client.ts`),
   check out the new release by its upstream tag (for example `v1.1.3`), and run the patch script.
   A refusal means upstream moved an anchor: re-derive the edit, and copy the new release's files
   into `tests/fixtures/` (its `UPSTREAM.txt` says how).
3. Build with the command above, tagged as a candidate (for example
   `-t docker.io/library/openmaic-app:candidate-1.1.3`) rather than `:latest`, so the running
   image's tag is untouched until deploy.
4. Smoke the candidate with `tools/smoke-app-image.py <candidate tag>`, run by a Python that has
   Playwright and its Chromium. It runs the image in a throwaway container on its own network and
   needs every check to pass; its header says what each one is and which control run must fail it.
   It drives docker or podman: `CONTAINER_CLI` when that is set, else `docker` if it is on PATH,
   else `podman`, and asks the engine's own `version` whether it is podman, so a `docker` CLI on
   podman's API counts as podman. It finds a registry-less name under either spelling, compose's
   `docker.io/library/` first, then `localhost/`, never the bare name (podman would resolve that
   to a stale `localhost/` build first), and says which one it ran.
5. At deploy, tag the running image as the rollback (for example
   `docker.io/library/openmaic-app:pre-<date>`), retag the candidate as
   `docker.io/library/openmaic-app:latest`, and recreate only `openmaic-app`. On a podman box whose
   volume predates v1.1.3, give it to the app's user first (*The app's data volume*, below).

To roll back: `docker tag docker.io/library/openmaic-app:pre-<date>
docker.io/library/openmaic-app:latest` (`podman tag` on a podman box), then recreate
`openmaic-app` the same way. The rail does not change between v1.0.3 and v1.1.3, but the image is
not the whole rollback: v1.1.3 writes classrooms, usage and materials to the volume, and a
rolled-back v1.0.3, which runs as the same user, finds it writable and those files in it. To
rebuild the old tree instead, first put the checkout back to clean upstream as in step 2 (git
refuses to check out a tag over the patched files), then check out the tag from step 1 and run the
version of the patch script that step 1 kept.

### The app's data volume

v1.1.3 runs as `nextjs` (uid 1001, primary group `nogroup`, 65533, as alpine's `adduser --system`
makes it) and writes classrooms, usage and materials to `/app/data`, which compose mounts as the
`platform_openmaic_app_data` volume. Its image creates that directory owned by `nextjs`, and a
volume comes to share that owner differently under each runtime:

- **Docker** copies the image directory's owner onto an EMPTY volume at every mount, so the first
  v1.1.3 start also makes the empty, root-owned volume v1.0.3 left behind the app's.
- **Podman** gives a volume its owner once, at the volume's first mount. A fresh volume gets the
  image directory's owner, but one that already exists keeps the owner it has, so the root-owned
  volume v1.0.3 left is not writable by v1.1.3 (the live upgrade of 2026-10-14 met exactly this).

On a podman box, give an existing volume to the app's user once, before (or after) the first
v1.1.3 start, with a throwaway root container from the new image:

```bash
podman run --rm --user 0 --entrypoint chown -v platform_openmaic_app_data:/app/data docker.io/library/openmaic-app:latest -R 1001:65533 /app/data
```

A fresh volume needs nothing. `tools/smoke-app-image.py`'s data-dir check reads both halves: the
image's own `/app/data` owner, and whether the app's user can write the volume it mounts (under
podman, a fresh one).

## Root-origin assets

OpenMAIC's source carries ~124 hand-written absolute asset paths (`<img src="/logos/openai.svg">`).
Next's `basePath` does **not** rewrite those — it only touches URLs Next itself generates — so the
browser resolves them against the origin root and they leave this rail's namespace. `rail.json`
declares the prefixes, the gateway routes them here, and RC029 checks nobody else claims them.

**Deriving the set — do not do it by eye.** It was got wrong the first time: `/vendor/` was
missed, and it is the one that matters most, because `lib/import/use-import-pptx.ts` loads
`/vendor/maic-importer/index.js` at runtime — so PPTX import breaks, not a logo. From an OpenMAIC
checkout:

```bash
grep -rohE "['\"\`]/[A-Za-z0-9._-]+(/[^'\"\`]*)?" app lib components --include=*.ts --include=*.tsx | sort -u
```

Cross-check the result against `ls public/`: every top-level entry there that the source
references absolutely needs a prefix. Two traps in the output — bare SVG names like
`/openai.svg` appear only inside doc comments (the real files are under `/logos/`), and a
`.json` hit can be a regex artefact rather than a reference. Confirm each with `grep -F`.

After changing the set, update **three** places or RC029 fails: `rail.json`, the gateway's
`ROOT_ASSETS` mirror, and `OPENMAIC_ROOT_ASSETS` in this rail's config.

The grep cannot see a path the code builds at run time. `public/comfyui-workflow.json` is one: the
ComfyUI image adapter fetches `${window.location.origin}/${model}`, and it is not in the set today.

## Root-relative API calls

The same gap applies to the app's own API. OpenMAIC calls its route handlers with hand-written
root-relative URLs, `fetch('/api/server-providers')`, about seventy of them. `basePath` does not
rewrite those either, and `/api/` cannot be a root asset: the gateway owns it at the origin root
(`RESERVED_ROOT_PREFIXES`). Every such call used to reach the gateway and 404 there. Three of them
run on every page load (`/api/server-providers`, `/api/comfyui-workflows`,
`/api/access-code/status`), and losing the first left the app without its server-side model
configuration, showing "Set up model".

`tools/platform-api-prefix.ts` fixes this in the app's own bundle. `platform-basepath.py` writes it
into the checkout as `instrumentation-client.ts`, which Next runs before React hydration, and
`next.config.ts` gets `env.PLATFORM_BASE_PATH`, the build's `NEXT_BASE_PATH`, inlined for it. It
wraps `fetch`, `XMLHttpRequest.open` and `EventSource`, and moves a URL under the basePath only
when it is same-origin and its path starts with `/api/`. Everything else passes through
unchanged. With `NEXT_BASE_PATH` unset the inlined value is `''` and nothing is wrapped. The gateway
is not involved, and no root `/api/` path is opened to this rail.

What it does not cover: an `/api/` URL the browser loads from markup or a navigation rather than
from script. `<img src>`, `<audio src>`, `<a href>` and `window.open` never pass through those three
APIs. OpenMAIC stores media generated on the server as relative `/api/classroom-media/...` paths
(`lib/server/classroom-media-bytes.ts`), so a class whose slides render those directly as an
element `src` will still miss.

`tests/test_platform_basepath.py` covers the patch script and `frontend/tests/api-prefix.test.js`
the shim. Neither can show that Next runs the shim before hydration and inlines the value; that
was checked against a real Next 16.3.3 build on 2026-10-08, and `tools/smoke-app-image.py` checks
it for any built image (its `api-prefix` check, which the 2026-09-17 image without the shim fails
on exactly the three per-load calls above).

## Light and dark mode in the frame

The shell publishes its mode as `data-theme` on its own `<html>`, and the framed app never looks
there. Its theme hook reads `localStorage.theme` once on mount and otherwise follows the OS. So
`src/frameTheme.ts` hands the mode across, same-origin, at three moments:

- Before the frame's app mounts it writes `localStorage.theme`, passing `system` through as
  `system`.
- Before each new frame document first paints it puts the `dark` class on that document's
  `<html>` (`watchFrameDocuments`), and holds it there until the app applies the same mode itself
  (`holdFrameClass`). Without this the app's server-rendered page painted light until it hydrated,
  the first 2-3 s of every load under a dark shell. The hold exists because the app's first render
  takes the class away for one render: its theme hook starts from `system` with the OS taken as
  light.
- When the shell's mode changes with the app open it toggles the class and calls the app's own
  `setTheme`, found in the app's React tree (`findAppTheme`), so the app's theme button and its
  stored choice follow the colours. If that context is not found (not hydrated, or reshaped by an
  upstream change), only the class moves, as before.

The palette is not handed across: the app has its own stylesheet, and this rail leaves it alone.

## Language

Language is not handed across, and cannot be from the frame side. The app's server render and its
first client render use upstream's `defaultLocale` (`lib/i18n/types.ts`), and its `I18nProvider`
picks the reader's locale (`localStorage.locale`, else `navigator.language`) only in an effect after
hydration, whatever this rail stores beforehand. Upstream's is `zh-CN`, so every load showed Chinese
strings and a `CN` toggle until the app had hydrated.

So the patch script sets it at build time: `en-US` by default, or `--default-locale <code>` for
any locale upstream ships (`zh-CN` keeps upstream's). A re-run with another code changes it in
place. `defaultLocale` drives more than the first paint. It is i18next's `lng` at init, so it is the
language of the prerendered HTML. It is also `fallbackLng`, so a string missing from a reader's
locale shows in English rather than Chinese. And it is what `use-i18n.tsx` picks for a browser
language upstream does not ship. A reader whose own locale is Chinese (or any other) now sees
English until hydration, then their own.

## Configuration

Everything except the last two rows is a field of `Settings` (`backend/openmaic_app/config.py`)
under `env_prefix="OPENMAIC_"`; unknown `OPENMAIC_*` names are ignored, so a typo is silent.
**Secret** marks a value that is itself a credential.

| Variable | Default | Secret | Notes |
|---|---|---|---|
| `OPENMAIC_LLM_MODEL` | `@openmaic` | | Keep it an `@role` — a pinned name makes Admin → Rails decorative. |
| `OPENMAIC_EMBED_MODEL` | `@embed` | | Shared with the RAG rails, so it stays resident. |
| `OPENMAIC_APP_URL` | `http://openmaic-app:3000` | | The app container, on the network only this rail shares with it (see *Where the app can reach*). |
| `OPENMAIC_APP_TIMEOUT` | `1320.0` | | Seconds, for the reverse proxy to the app. Compose passes `1320` too: a generating request waits on the shim, which may wait out a Claude CLI call and then a local answer (at `300` it was exactly the owner's `BROKER_CLI_TIMEOUT`). |
| `OPENMAIC_PUBLIC_PREFIX` | `/openmaic/api/app` | | Must equal the `basePath` the app image was BUILT with, or every page 404s from Next itself. |
| `OPENMAIC_ROOT_ASSETS` | `/logos/,/avatars/,/vendor/,/logo-horizontal.png,/openmaic-mark.png` | | Origin-root paths this rail serves. Change it in three places or RC029 fails — see *Root-origin assets*. |
| `OPENMAIC_BROKER_URL` | `http://127.0.0.1:11500` | | Compose sets `http://host.docker.internal:11500`. |
| `OPENMAIC_BROKER_TIMEOUT` | `600.0` | | Seconds, for broker calls. Compose passes `1320`: a call that goes to the Claude CLI may fail and fall back to the local model inside the same request. |
| `OPENMAIC_LLM_KEEP_ALIVE` | `30m` | | Holds the model resident across the many small calls one course generation makes. |
| `OPENMAIC_LLM_BASE_URL` | *(unset)* | | External OpenAI-compatible endpoint to use **instead** of the broker — e.g. a larger-VRAM host on the LAN. Compose hands it to the app as `OLLAMA_BASE_URL`. Outside the stack: on the app's network no compose service but this rail resolves. |
| `OPENMAIC_SHIM_TOKEN` | **has one**: the placeholder `openmaic-local-dev`, from `deploy/docker-compose.yml` | **yes** | Shared secret for app→rail calls, passed to both containers. A published default is a secret in form only — harmless on a single-host compose network, wrong the moment the sidecar is reachable from elsewhere. **Override it on any box that is not single-user**, and on any box that sets `OPENMAIC_CLI_REQUESTER`: with the default the shim names nobody. Empty does not open the gate: the token check simply cannot pass, and only a platform identity (from the gateway's address) gets in. |
| `OPENMAIC_LLM_API_KEY` | *(unset → falls back to `OPENMAIC_SHIM_TOKEN`)* | **yes** | Credential for `OPENMAIC_LLM_BASE_URL`. Consumed by compose, not by this rail. Setting the base URL without this sends an unauthenticated request to the far side. |
| `OPENMAIC_CLI_REQUESTER` | *(unset)* | | **Single-user boxes only.** The platform username every generation the app makes is attributed to, so the broker may answer it from that owner's Claude CLI. See *The Claude CLI* below. Unset names nobody. |
| `OPENMAIC_CLI_LEASE_S` | `1800.0` | | How recently the proxy must have seen `OPENMAIC_CLI_REQUESTER`, and how long any other user's visit keeps the name off. Not passed by compose. |
| `OPENMAIC_GATEWAY_HOST` | `gateway` | | The name whose addresses may assert a platform identity (see *Whose identity is believed*). The compose service name is right on both stacks, so compose does not set it. A dev box with the gateway on the host sets `localhost`, and `peer_check` then reads `degraded`. |
| `OPENMAIC_GATEWAY_RESOLVE_INTERVAL` | `5.0` | | Seconds: at most one re-lookup of that name per interval, on a request from an address not in the last answer. |
| `OPENMAIC_APP_IMAGE` | `openmaic-app:latest` | | Compose-only: the app image tag. Built by hand — see *Building the app image*. |
| `OPENMAIC_DEFAULT_MODEL` | `ollama:@openmaic` | | Compose-only: becomes the app's **mandatory** `DEFAULT_MODEL`. Needs the provider prefix. |
| `OPENMAIC_APP_NAME` | `openmaic` | | Rarely set; the rail's own name. |
| `BROKER_AUTH_TOKEN` | *(unset)* | **yes** | Unprefixed and platform-wide, read straight from the environment. `OPENMAIC_BROKER_AUTH_TOKEN` is NOT a variable — nothing in `deploy/` sets it, so spelling it that way means the rail sends no `Authorization` header at all. |
| `BROKER_AUTH_TOKEN_FILE` | *(unset)* | **yes** | A path to a file holding the same token; when set it WINS over `BROKER_AUTH_TOKEN`, and an unreadable path yields no token (the broker 401s visibly) rather than a fall-back to the variable. See `docs/RAIL_CONTRACT.md`. |
| `PLATFORM_STANDALONE` | *(unset)* | | Dev only, `1`/`true`/`yes`: lets the rail run without the gateway's identity header. |

On the app container, `DEFAULT_MODEL` is **mandatory** and needs a provider prefix
(`ollama:@openmaic`). OpenMAIC has no hardcoded fallback: unset, generation fails outright rather
than picking something.

### Pointing the LLM slot at another machine

Set `OPENMAIC_LLM_BASE_URL` to any OpenAI-compatible endpoint. The reasoning chip then reports the
override host rather than broker residency — this rail has no visibility into what is resident on
the far side, and reporting `loaded` for a model it cannot see would be exactly the lie the
four-state contract exists to prevent. The entry carries `backend` and `upstream` `"external"`
and `busy: false` beside the other chip keys (`docs/RAIL_CONTRACT.md`, "Who answers a slot"), so
the shell's chip reads "external" with a neutral dot; `tests/test_capabilities_override.py` pins
the entry, and `tests/mutate_capabilities.py` (not a pytest file) proves those tests fail when a
key goes or lies.

### The Claude CLI (single-user boxes)

The broker can answer a platform owner's own calls from that owner's Claude CLI subscription
(`services/broker/README.md`), but only a call whose `requester` names them. The app calls the
shim server to server with the service token and no platform identity, so nothing on a call says
who is waiting on it, and the shim reads no name from a call: not `X-Platform-User`, not the
OpenAI `user` field, not a `requester` key in the body.

`OPENMAIC_CLI_REQUESTER` is the box owner saying "every generation here is mine". Set, the shim
names that account on the app's calls on the reasoning role (`OPENMAIC_LLM_MODEL`; a model picked
off that role in the app's selector names nobody), and only while that account is the **sole**
user the rail's proxy has seen within `OPENMAIC_CLI_LEASE_S` (30 minutes). Every request the
browser makes inside the frame comes through the proxy behind the gateway's identity gate, so
that is who is using the app (`api/proxy.py` `sole_user`). Anyone else in the app in that window,
or the owner not seen in it, and the shim names nobody: the call runs on the local model whatever
the switch says. The ledger is in memory, so after a restart the name stays off until the owner's
next request in the frame. It reads the `X-Platform-User` the rail is handed, which counts only
from the gateway's address (*Whose identity is believed*), so no container beside the rail can
keep the name on. It is still a single-user setting: the ledger knows who is using the app, not
who started a given generation. And it needs `OPENMAIC_SHIM_TOKEN` set to this box's own secret:
with the published `openmaic-local-dev` any container that reaches the rail can call the shim as
the app does, so the shim names nobody and logs why, once.

Then switch `@openmaic` on in Admin → Rails. `@openmaic` is shared with another rail's condense
slot, so on a box with this set the switch moves both. The reasoning chip reads `Claude` for the
configured account exactly when the shim would name it, and `ollama` for everyone else. A call the
CLI answers can be silent until its first token, so the streamed shim sends an SSE comment
(`: ping`) every 15 s while the broker says nothing, and a CLI that stops mid-answer
(`cli_interrupted`) reaches the app as an SSE error, never as a finished answer.
`tests/test_cli_requester.py` pins all of it, and `tests/mutate_cli_requester.py` (not a pytest
file) proves those tests fail when any of it is broken; unset, `tests/test_no_requester.py` holds
that nobody is named.

## Model slots

| Slot | Role | Kind | In Admin → Rails |
|---|---|---|---|
| `reasoning` | `@openmaic` | chat | yes |
| `embed` | `@embed` | embed | no — that panel is chat/vision/image only |

## Tests

```bash
.venv\Scripts\python.exe -m pytest rails/openmaic/tests -q
```

`test_auth.py` covers the fail-closed gate, including the shim mount — a mount is not a route, so
the app-level dependency never runs for it, and those tests are what keep "has its own gate" from
quietly becoming "has no gate". `test_gateway_peer.py` covers whose identity reaches that gate at
all. `test_llm_shim.py` covers the translation, which has no visible
failure mode: a wrong `options` key does not raise, it silently uses the model's default.

`test_frontend_helpers.py` runs the node tests in `frontend/tests/`, the request shim's among
them, so they fail this target when they fail. `tests/mutate_audit_guards.py` (not a pytest file)
breaks each guarded line on purpose and checks the named test fails. The frame's light/dark
hand-off is also checked in a real browser against the built bundle:
`web/tests/browser/railcheck.py om-frame-theme`, with `mutate_railcheck.py om-` as its mutation run
(the cases are in `web/tests/browser/railmutations/openmaic.py`).

`test_platform_basepath.py` patches verbatim copies of upstream v1.1.3's files
(`tests/fixtures/openmaic-v1.1.3/`). None of the above runs the real app. `tools/smoke-app-image.py`
does, against a built image: the basePath, the language of the first paint, the request shim, the
frame's light/dark hand-off with the rail's own `frameTheme.ts` against the app's real theme
context, the Pi chat build flag, and whether `/app/data` is writable. It is not a test target,
because it needs a container CLI (docker or podman) and a browser. `test_smoke_app_image_cli.py`
is: it checks which CLI the tool picks and that every container command goes through it, with no
container started.
