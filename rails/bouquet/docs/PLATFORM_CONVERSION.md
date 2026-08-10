# Platform conversion — bouquet-builder → the "Bouquet Builder" rail

Converting the standalone `bouquet-builder` project (a florist-grade flower knowledge
base + a photo→report goal) into a federated rail of the platform shell, like the
other rails: a FastAPI `/api` backend over a small bouquet core, a React
module-federation remote, vision + LLM via the platform broker, containerized behind
the gateway, entitlement-gated. Built 2026-07-31.

## Decisions (confirmed with the user)

1. **Fresh extract, not a subtree.** Only the flower knowledge base was copied in
   (`seed/knowledge-base/`: 50 profiles + 4 cross-cutting references + 200 licensed
   reference photos + manifest + the analysis playbook). The standalone repo's
   women-in-business **grants report/tracker** and the **Frenchies Flowers custom-GPT
   package** are NOT part of the rail and stay in `bouquet-builder`. The Frenchies
   persona itself is re-embedded as a prompt in `prompts.py` so the rail is
   self-contained.
2. **Both output modes, selectable.** *Analysis* (the expert report from the analysis
   playbook) and *Florist copy* (the Frenchies persona: one description paragraph + a
   fun fact). Chosen per analysis in the UI.
3. **Single-tenant, owner-only** (like finance). Saved analyses are one shared library
   gated by the platform entitlement — no per-row owner column.

## Architecture

> **Note — partly superseded.** This records the *initial* conversion (a one-shot
> analyze). The analyze flow was later reworked into a **two-step human-in-the-loop**
> pipeline (identify → review/correct → generate) with the writer on **`@chat-large`
> → qwen3.6:27b**, and the long calls are now **polled background jobs**. The KB, KB
> gotchas, and platform wiring below still hold. Current design: `../README.md` +
> `../PLAN.md`.

Two broker round-trips, no broker changes needed:

1. **Identify** (`@vision` → gemma3:27b): the uploaded photo is downscaled and sent as
   a chat message with `images=[b64]`; the model returns a structured inventory
   (flowers + colors + confidence, greenery, palette, arrangement, context).
2. **Report** (originally `@chat` → mistral-small3.2:24b; now `@chat-large` → qwen3.6:27b):
   each identified flower is resolved to its KB profile (slug + alias map); the profiles +
   the four reference lenses are the context; the chat model writes the report in the
   chosen voice.

The KB is **read-only reference data baked into the image** (`COPY seed`, `BOUQUET_KB_DIR`)
and read directly — no volume hydration. Only the analyses DB + uploaded photos are
mutable (`/srv/var`, the `bouquet_data` volume). Port **8840**.

## Two gotchas found during the build (verified against the live broker)

- **Vision image size must be ~896px, not larger.** gemma3's SigLIP vision encoder is
  natively 896×896. Feeding a 1280px image made the model return **empty content**;
  896px identifies cleanly. Pinned in `config.MAX_IMAGE_EDGE = 896`.
- **Loose `format="json"`, NOT a strict JSON Schema.** gemma3 + Ollama returns empty
  content when a `format=<schema>` is combined with an image; under loose `"json"` mode
  (with the shape described in the prompt) it produces correct JSON. `broker.chat_json`
  parses it (fence-tolerant). A hard guard also prevents an empty inventory from
  reaching the writer model, which otherwise confabulates a whole bouquet.

## Verification

- Backend offline suite (fake broker): **16 pass** — KB load/alias-resolve, empty-inventory
  guard, fence parsing, full API via TestClient.
- Live pipeline against the real broker: rose reference photo → identified `rose` (high) →
  grounded report; empty/rectangle → clean "no flowers identified".
- **Hosted E2E through Caddy → gateway → container (Playwright, headless): PASS.** Login as
  admin → Bouquet Builder on the rail → federated module mounts → Flower Library shows
  50 cards with reference thumbnails served through the gateway → uploaded a bouquet photo
  → report rendered with "Rose" matched to the library → saved to History → 0 console errors.

## Platform wiring touched

- Gateway: `catalog.py` (entry, 💐), `config.py` (`app_bouquet_url`, `bouquet_dist`,
  `enabled_apps`, both dict methods). Proxy/mount/gate are generic — unchanged.
- Shell: `vite.config.ts` (remote + dev proxy), `remotes.d.ts`, `App.tsx` (lazy import +
  render branch).
- Deploy: `docker-compose.yml` (`bouquet` service, gateway `depends_on` + env + dist mount,
  `bouquet_data` volume).
