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
