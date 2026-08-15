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

## 2. Scenario Builder — the four-question diagnostic

The rail's headline flow, intentionally left unwired pending guided rebuild.

- [ ] Author the 4 questions per scenario in `frontend/src/scenarios.ts` (only Retail Chain Q1
      was recovered verbatim from the demo capture)
- [ ] Generation pass: answers + scenario → the four outputs (Scenario Card, Discovery
      Playbook, Customer Q&A, ROI Summary) grounded in the SME corpus
- [ ] The "Your next move" directional close — the actual payload, per `reference/README.md`
- [ ] The generation checklist UI (each line is a real stage, not decoration)
- [ ] Read-aloud on each output tab

**Design constraint:** the ROI figures in the original were 3B-model output and one tile was
visibly broken. Any numbers surfaced here must come from the corpus or be labelled as
partner-supplied inputs, never generated.

---

## 3. Knowledge base — replace the scaffolding

- [ ] Every `00-overview.md` under `seed/knowledge-base/` is placeholder and says so
- [ ] Real material must carry a source and an "As of" date — SMB program mechanics reset each
      Microsoft fiscal year, and the assistant repeats whatever is written verbatim
- [ ] Consider a freshness check that surfaces content older than one fiscal year

---

## 4. Deployment

- [ ] Add `smb-partner-enablement` to `PLATFORM_ENABLED_APPS` in the install clone's
      `deploy/.env` (currently `terminal-fun,recipe-book,co-worker`)
- [ ] `docker-compose build smb-partner-enablement && up -d`, rebuild the gateway (bundled
      image — any frontend change needs it)
- [ ] Grant the entitlement to the relevant users in Admin → Users

---

## 5. Smaller items

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
