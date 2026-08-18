# Backlog

Deferred work on this rail, roughly in dependency order. Written down so the scaffolding
decisions that were *deliberate* don't get mistaken for things that were forgotten.

---

## 1. Voice: Kokoro TTS through the broker

**The model:** <https://github.com/hexgrad/kokoro> — Kokoro-82M, Apache-2.0, ~82M params.
Live demo / voice sampling: <https://huggingface.co/spaces/hexgrad/Kokoro-TTS> (useful for
picking a default voice before wiring anything).
Small and fast enough to stay resident beside the RAG model, which is the entire reason the
original prototype could show both as "GPU · ready" at once. `kokoro-onnx` runs it under
onnxruntime on CPU or GPU.

**Why it isn't wired yet — two independent blockers:**

1. **The broker's media path evicts everything.** `_run_media()` in
   `services/broker/app/broker.py` calls `_evict_other_heavy()` with no `keep` argument,
   unloading *every* heavy model before the worker runs, then runs the worker as a
   short-lived subprocess that exits to reclaim VRAM. Correct for XTTS v2 and SDXL. Wrong for
   a 350 MB voice model: it makes every spoken answer cost a full model swap.
2. **The media worker is disabled on this workstation.** `media.enabled: false`, because
   `BrokerSettings.media_python` points at `D:\.claude\projects\edu-suite\.venv\...`, which
   does not exist here. Pre-existing platform issue, not this rail's.

**The work:**

- [ ] Stand up a torch/onnxruntime venv on the broker box and point `BROKER_MEDIA_PYTHON` at it
- [ ] Add a `kokoro` op to the media worker (`edu_media_core`), loading via `kokoro-onnx`
- [ ] Add `Broker.tts_light()` — same worker, but **skips `_evict_other_heavy()`**, gated on a
      small-media allowlist. Follow the `Broker.embed_image()` precedent, which already skips
      the gate for exactly this reason ("evicting gemma3 to embed on CPU and then reloading it
      would be pure thrash")
- [ ] Keep the worker warm for voice instead of spawning per utterance
- [ ] Flip `SMB_PARTNER_VOICE_BACKEND=broker` and confirm `/api/capabilities` reports it

**Decide when implementing:** CPU or GPU. On this box, GPU leaves only ~794 MiB free once the
RAG model and embedder are resident — enough for Kokoro on paper, nothing to spare. CPU is
realtime-capable and the safer default here. See [`MODELS.md`](MODELS.md).

**Until then:** `voice.py` resolves to browser Web Speech API. No VRAM, works on a phone, both
models stay warm. The seam is already in place — this is a config flip, not a rewrite.

---

## 2. Scenario Builder — DONE (2026-08-17)

Six scenarios, 6–8 questions each, five-part grounded package, live reasoning trace. See the
README for the three honesty mechanisms (computed constraints, two output guards, deterministic
scenario card).

Remaining polish, none blocking:

- [ ] **Restaurant Group is the least-grounded scenario.** Microsoft publishes frontline material
      for exactly four industries — Retail, Healthcare, Financial Services, Manufacturing — and
      none for restaurants or field services, so this one leans on Retail's content. Kept for
      fidelity to the original hackathon demo. Healthcare and Financial Services are the
      remaining grounded industries if more breadth is wanted.
- [ ] **`_HARD_RULES` has no test.** It is the highest-consequence table in the rail — a wrong
      entry produces confident, unexecutable advice. Worth a unit test per rule.
- [ ] The entitlement guard matches on the last two tokens of a product name, which is a
      heuristic. A curated product vocabulary extracted from the corpus would be tighter.

## 3. Knowledge base — DONE (2026-08-14/17)

67 sourced files, 13 collections, ~1,050 chunks. Every file carries a source URL, an as-of date
and a currency warning. Every collection now wins retrievals.

- [ ] **Freshness sweep.** `program-updates/` is the most perishable content in the corpus — every
      figure is a list price or a dated promotion. The monthly Partner Center announcements page
      (`learn.microsoft.com/en-us/partner-center/announcements/<year>-<month>`) is the best
      currency source. Re-check anything older than one fiscal year.
- [ ] The four field-earned collections are empty by design and need the team's real material.

## 4. Mobile: wire the Scenario Builder flow — DONE (2026-08-18)

Full diagnostic → generation → package flow ported to `frontend/mobile/App.tsx`. Same logic
as the desktop builder (shared api.ts / types.ts / voice.ts); mobile-specific layout: stacked
instead of runsplit, trace collapsible via toggle, output tabs scroll horizontally, Read Aloud
on Next Move and each section tab. Both the federation build and the mobile SPA build via
`npm run build` in one step; the mobile SPA lands at `/smb-partner-enablement/m/` via the
same StaticFiles mount (dist/m/ nested inside the desktop dist).

---

## 5. Deployment — DONE (2026-08-18)

- [x] `smb-partner-enablement` added to `PLATFORM_ENABLED_APPS` in install clone's `deploy/.env`
- [x] Service wired in `docker-compose.installer.yml` (profile `smb-partner-enablement`); frontend
      baked into the bundled gateway Dockerfile; `smb_partner_data` external volume declared
- [x] Backend and gateway rebuilt; both containers up (`platform-smb-partner-enablement-1` running)
- No entitlement grant needed — `bigfnj` is `is_superadmin` which grants implicit access to all
  enabled apps. The corpus is fully ingested (1,051 chunks, 9 collections with content) and the
  backend returned 401 (auth required, not 404) from the gateway proxy — rail is live.
- **Access:** `http://localhost:1111` → log in as bigfnj → SMB Partner Enablement appears in the
  rail nav. Mobile surface at `http://localhost:1111/smb-partner-enablement/m/`.

---

## 5. Smaller items

- [x] ~~Stale chunks survived re-ingest~~ — fixed 2026-08-17. An emptied collection reported
      "empty" and skipped without clearing, and a deleted folder was never iterated at all; 17
      orphaned placeholder chunks were being retrieved and cited as sourced material. Found by
      reading the live reasoning trace.
- [ ] **Ingest on a bind mount.** The seed tree is baked into the image, so content edits need a
      rebuild. Bind-mounting `seed/knowledge-base` would make authoring iterative.
- [ ] **Upload path is admin-only** and re-embeds a whole collection per upload. Fine at current
      scale; revisit if partners upload their own material.
- [ ] **No STT server-side.** Speech *input* is browser-only (`SpeechRecognition`), which means
      no speech input in Firefox. A Whisper op in the media worker would close this, with the
      same eviction caveat as Kokoro.
- [ ] **`_same_model()` in `api.py`** works around Ollama's implicit `:latest`. If other rails
      grow residency indicators, this belongs in `platform_core` rather than copied.
- [ ] **Mobile preview iframe** loads the rail's own origin. Harmless today; if the gateway ever
      sets `X-Frame-Options: DENY` globally, the preview breaks and will need a same-origin
      exemption.

---

## Not in scope, deliberately

- **`research/` and `documents/` are gitignored.** They hold the real rebuild source material —
  internal correspondence, decks, contact details. This repo is public. Do not commit them, and
  scan the staged diff before any commit that touches this rail.
