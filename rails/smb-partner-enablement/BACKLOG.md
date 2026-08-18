# Backlog

Deferred work on this rail, roughly in dependency order. Written down so the scaffolding
decisions that were *deliberate* don't get mistaken for things that were forgotten.

---

## 1. Voice: Kokoro TTS through the broker — DONE (2026-08-18)

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

- [x] Stand up a torch/onnxruntime venv — `services/broker/media-venv` (py3.11, kokoro-onnx
      0.5.0, onnxruntime-directml 1.24.4, DmlExecutionProvider confirmed active)
- [x] Add `do_kokoro` op to `media_worker.py` + register `"kokoro_tts"` in `_OPS`
- [x] Add `Broker.tts_light()` — no gate, no eviction, embeds model_path/voices_path in spec.
      `BrokerSettings` gets `kokoro_model_path` + `kokoro_voices_path` (env: `BROKER_KOKORO_*`).
- [x] NSSM AppEnvironmentExtra updated — `BROKER_MEDIA_ENABLED=true`, `BROKER_MEDIA_PYTHON`,
      `BROKER_KOKORO_MODEL_PATH` (fp16-gpu.onnx), `BROKER_KOKORO_VOICES_PATH` (v1.0.bin)
- [x] `/v1/tts_light` route + `TtsLightRequest` schema in broker main.py
- [x] `voice.py` `speak()` → `broker.tts_light()` with BCP-47 → Kokoro lang_code map
- [x] End-to-end verified: container → broker → Kokoro ONNX → WAV 24kHz; `effective: broker`;
      no heavy model eviction; first-call ~6-8s (subprocess cold load per utterance)
- [ ] **Keep the worker warm** — spawn a long-running media worker process, reuse across calls.
      Removes the per-utterance cold-load cost. Now worth more than when it was written: **both**
      voice directions pay it. STT measures 4.8s end-to-end of which only ~1.7s is transcription;
      the rest is subprocess spawn + model load. Warming would put both paths near 1.5s.
      Not blocking; current path works.

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
- [x] **Server-side STT — done 2026-08-18.** `do_transcribe` op in `media_worker.py`,
      `Broker.transcribe()`, `POST /v1/transcribe`, rail `POST /api/transcribe`, and a
      `MediaRecorder` capture path in `voice.ts`. faster-whisper `small.en` on **CPU/int8**: no
      GPU gate, no eviction, ~1.7s for a 4s utterance. Closes the Firefox gap and stops sending
      audio to Google.
      **The forcing bug was not privacy.** `SpeechRecognition` has no device API — it always uses
      the OS default input. With a headset chosen in our own picker, Chrome kept listening to the
      default (silent) device and returned `no-speech`. `getUserMedia({ deviceId })` honours the
      choice; Web Speech never could.
      **Fresh-install step:** the media venv needs `faster-whisper`
      (`uv pip install --python <media-venv>/Scripts/python.exe faster-whisper`). Weights come
      from the HuggingFace cache on first call (`Systran/faster-whisper-small.en`, ~484 MB), so a
      cold box needs one online call. Override with `BROKER_WHISPER_MODEL` /
      `BROKER_WHISPER_DEVICE` / `BROKER_WHISPER_COMPUTE_TYPE`.
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
