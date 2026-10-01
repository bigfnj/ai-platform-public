# Model budget for the Gemini Enterprise CX rail

## The steady state

This rail holds **two broker models resident at once** while it is in use:

| Role | Class | Job |
|---|---|---|
| `@gemini-cx-rag` | heavy (generative) | grounds and writes the cited answer |
| `@embed` | light (embedder) | retrieval over the GECX corpus |

That works because the broker's one-heavy-model policy **exempts embedders** — an embedder may
stay resident alongside one heavy model. So a *follow-up* question costs no model swap: every
chat this rail sends carries `keep_alive: 30m`, which is what holds both models through a
session of rapid deck clicks.

**What the steady state does NOT include is boot.** `broker.warm()` runs at container start only
when `GEMINI_CX_WARM_ON_BOOT=1` is set in `deploy/.env`, and it is **off by default** since
2026-10-01. Nothing at boot needs a model resident: the seed ingest compares collection
fingerprints first and loads the embedder itself, through `/v1/embed`, only for a collection
whose markdown actually changed. A warm therefore only ever spared the **first** question one
cold load, while holding both models for 30 minutes whether or not anyone opened the rail.

⚠ **Why the default flipped, measured on two cards.** On an 8 GB card the pair held ~3.5 GB of
VRAM plus the runner's host RAM for 30 minutes after every stack start — when memory is tightest.
On a 24 GB card with the full-stack roles, every container start parked `mistral-small3.2:24b` —
**19.6 GB** — on the GPU for 30 minutes, with nothing in the broker queue to show why. And there
the co-residency this section is built on did not even hold: at Ollama's VRAM-derived default
context of 32768 the 24B model is predicted at 20.4 GiB, so Ollama's own scheduler **evicted
`bge-m3`** to fit it, every time.

## The 8 GB arithmetic on this workstation

The practical generative budget on the RTX PRO 1000 (~8 GB) is about **4.4 GB** once the
embedder is resident. Measured figures from the sibling SMB Partner rail:

- `bge-m3` — **0.66 GB**
- `llama3.2:3b` — **2.55 GB**
- `gemma3:4b` — **3.34 GB**

`gemma3:4b` + `bge-m3` is roughly **4.0 GB**, which fits. That is why this rail runs on
`gemma3:4b` on an 8 GB box (`deploy/installer/roles.lean.json`) rather than the 3B-class model the
SMB Partner rail is pinned to.

**The reason this rail can afford the larger model is that it has no voice component.** The SMB
Partner rail must leave room for a TTS model, so it is pinned to 3B-class. This rail is
text-only, so the whole generative budget goes to answer quality — which matters, because the
corpus is dense prose full of near-identical distinctions the model has to keep straight.

⚠ **Corrected 2026-09-17.** This file used to say the full-stack default was `gemma4*:12b`. It
is `mistral-small3*:24b`, in both `DEFAULT_ROLES` (`services/broker/app/config.py:99`) and
`services/broker/roles.json`. `gemma4*:12b` was never actually exercised — the public repo always
overrode this role down to a non-thinking `gemma3:4b` — and when it was finally run here it
produced **nothing**. gemma4 is a thinking model, the rail asks for `num_predict=800`
(`GEMINI_CX_MAX_TOKENS`), and on the real 8-chunk prompt the whole budget went to the thinking
phase: `done_reason: length`, **0 characters of answer** and 3066 of thinking. It needs roughly
4000 to emit anything. mistral-small3.2 answers the same prompt in 233 eval tokens with no
thinking at all. The 8 GB override lives in `deploy/installer/roles.lean.json`.

## The swap-avoidance tradeoff, stated honestly

If both knowledge rails are in active use in the same session, pointing `@gemini-cx-rag` at the
same model `@smb-partner-rag` resolves to means moving between the two rails costs **no model
swap at all**, because the resident heavy model is already the right one. Pointing it at a
different model gives you a per-rail choice but makes each rail switch a swap.

On a **24 GB** card with the full-stack roles the trade is already taken: both roles are
`mistral-small3*:24b`, so switching rails is free and the model is the one measured to actually
answer. On an **8 GB** card
`roles.lean.json` points both at `gemma3:4b`, for the same reason.

Repoint it live from **Admin → Rails** (the `gemini-cx-rag` role, hot-read from `roles.json`, no
restart needed) if that trade turns out to be wrong in practice.

## Gotchas that will bite

**The live broker runs from the INSTALL clone** (`%USERPROFILE%\ai-platform`), not from any
separate editing checkout. Editing that checkout's `roles.json` does nothing to the running system.
`roles.json` is hot-read, and `roles()` is `DEFAULT_ROLES | json` — so a role present only in
`DEFAULT_ROLES` still resolves, and a role present only in `roles.json` also resolves.

**Ollama's implicit `:latest` breaks naive residency checks.** `@embed` resolves to `bge-m3` but
Ollama reports the loaded model as `bge-m3:latest`, so `resolved in loaded_names` is always
False. `api._same_model()` is tag-tolerant for exactly this reason — do not "simplify" it into an
equality check, or the health endpoint will report both models cold forever.

**Ingest needs the embedder.** If the broker is unreachable at boot, ingest fails per collection,
logs a warning, and the rail still serves `/api/health` with an empty corpus. That is deliberate
— the rail must not fail to start because the GPU layer is down — but it means an empty corpus is
a broker problem far more often than a content problem. The UI says so explicitly.

## Voice — Read aloud, and why it costs latency rather than VRAM

Every answer carries a **Read aloud** button. Server-side synthesis is **Kokoro-82M** via the
broker's **`/v1/tts_light`**, with the voice `af_heart` (American female).

**The endpoint choice is the whole design.** `/v1/tts` (XTTS) takes the full GPU gate and calls
`_evict_other_heavy()` with **no `keep`** — using it would evict this rail's answer model on every
utterance and destroy the co-residency everything else here depends on. `tts_light` skips both the
gate and the eviction, following the `embed_image()` precedent. Never "simplify" the voice path
onto `/v1/tts`.

**Kokoro is transient, not co-resident.** `media.run_media_job()` spawns a **subprocess per
call** which exits when the job finishes, so Kokoro's ~350 MB is a brief spike during synthesis
rather than a permanent tenant. Steady-state footprint is unchanged at `gemma3:4b` (3.34 GB) +
`bge-m3` (0.66 GB) ≈ **4.0 GB**, and there is **no need to drop the generative model to 3B-class**
to afford voice. (An earlier draft of this file claimed otherwise; it was wrong.)

The real cost is **latency**: because the model loads per call, a Read aloud takes **~6.7 s**
before the first word (measured on this box for a one-sentence answer). That is acceptable for a
button the user chose to press, and it is why voice is not on the answer path itself.

**It degrades rather than failing.** `voice.py` is a seam with four backends — `auto` (probe the
broker, 300 s cached), `broker`, `browser`, `off`. If the media worker is unavailable or the call
raises, the payload comes back as `browser` mode with a `degraded` note and the client speaks it
via the Web Speech API — no GPU, and the button still works. The browser fallback also prefers a
female neural voice so the voice does not change gender when the broker is unavailable.

**Requires on the broker:** `BROKER_MEDIA_ENABLED=true`, `BROKER_KOKORO_MODEL_PATH` and
`BROKER_KOKORO_VOICES_PATH`. Verified live on this box: `media.enabled = true`, synthesis returns
a valid 24 kHz RIFF/WAVE payload.
