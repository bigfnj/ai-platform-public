# Model budget for the Gemini Enterprise CX rail

## The steady state

This rail holds **two broker models resident at once**:

| Role | Class | Job |
|---|---|---|
| `@gemini-cx-rag` | heavy (generative) | grounds and writes the cited answer |
| `@embed` | light (embedder) | retrieval over the GECX corpus |

That works because the broker's one-heavy-model policy **exempts embedders** — an embedder may
stay resident alongside one heavy model. So a question costs no model swap: both models are
already warm. `broker.warm()` is called for both at boot, and `keep_alive` defaults to `30m`
because a question deck invites rapid successive clicks and a cold load per click would dominate
the response time.

## The 8 GB arithmetic on this workstation

The practical generative budget on the RTX PRO 1000 (~8 GB) is about **4.4 GB** once the
embedder is resident. Measured figures from the sibling SMB Partner rail:

- `bge-m3` — **0.66 GB**
- `llama3.2:3b` — **2.55 GB**
- `gemma3:4b` — **3.34 GB**

`gemma3:4b` + `bge-m3` is roughly **4.0 GB**, which fits. That is why this rail defaults to
`gemma3:4b` on this box (`services/broker/roles.json`) rather than the 3B-class model the SMB
Partner rail is pinned to.

**The reason this rail can afford the larger model is that it has no voice component.** The SMB
Partner rail must leave room for a TTS model, so it is pinned to 3B-class. This rail is
text-only, so the whole generative budget goes to answer quality — which matters, because the
corpus is dense prose full of near-identical distinctions the model has to keep straight.

The full-stack default in `services/broker/app/config.py` `DEFAULT_ROLES` is `gemma4*:12b`, for
a 24 GB card. The 8 GB override lives in `roles.json` and `roles.lean.json`.

## The swap-avoidance tradeoff, stated honestly

If both knowledge rails are in active use in the same session, pointing `@gemini-cx-rag` at
**`llama3.2:3b`** — the same model `@smb-partner-rag` resolves to — means moving between the two
rails costs **no model swap at all**, because the resident heavy model is already the right one.
Pointing it at `gemma3:4b` gives better answers but makes each rail switch a swap.

Neither choice is wrong; it depends on whether you optimise for answer quality or for
rail-switching latency. The default here is **quality** (`gemma3:4b`), on the reasoning that a
knowledge assistant is judged on its answers and a rail switch is an occasional event.

Repoint it live from **Admin → Rails** (the `gemini-cx-rag` role, hot-read from `roles.json`, no
restart needed) if that trade turns out to be wrong in practice.

## Gotchas that will bite

**The live broker runs from the INSTALL clone** (`%USERPROFILE%\ai-platform`), not the Grimoire
editing copy. Editing the editing copy's `roles.json` does nothing to the running system.
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
