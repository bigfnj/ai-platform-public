# Models — two resident at once, and what that costs on this platform

The original prototype's AI Status popover claimed two models live simultaneously:

```
LLM (llama3.2:3b)      ● GPU · ready
Voice TTS (Kokoro)     ● GPU · ready
```

This rail reproduces that. Doing so on *this* platform is not free, because the broker's VRAM
policy was written for a different shape of workload. This file records what runs, what fits,
and what had to change.

## What the rail asks the broker for

| Slot | Role | Model | Class | Resident |
|---|---|---|---|---|
| Reasoning / RAG | `@smb-partner-rag` | `llama3.2:3b` | heavy | yes, `keep_alive=30m` |
| Retrieval | `@embed` | `bge-m3` | embed | yes, always |
| Voice | *(no role — platform capability)* | Kokoro-82M | media | see below |

The voice row deliberately has no `@role`. Voice is served platform-wide by the broker's
`/v1/tts_light`, and the default speaker is a broker setting (`BROKER_KOKORO_VOICE`), so it
is changeable for every rail at once without a rebuild. There was once an
`@smb-partner-voice` role here that resolved to `kokoro` — the only light TTS backend — so
it could not select anything, nothing read it, and the Admin → Rails panel excludes TTS by
design. It has been removed rather than wired up.

Note there are really **three** models, not two. The original counted the LLM and the TTS; it
had no RAG corpus, so it had no embedder. This rebuild does, which is the whole point of
`seed/knowledge-base/`.

## Does it fit? — measured on this workstation

RTX PRO 1000 Blackwell Laptop GPU, **8151 MiB total**. Baseline occupancy with the desktop and
a browser running is ~3.7 GB, so the practical model budget is **~4.4 GB**, not 8.

Both models were loaded through the broker and confirmed co-resident via `/v1/status`:

| | VRAM | Source |
|---|---|---|
| `llama3.2:3b` (heavy) | **2.55 GB** | measured, resident |
| `bge-m3` (embed) | **0.66 GB** | measured, resident |
| **Both, together** | **3.21 GB** | `evicted: []` — neither displaced the other |
| Remaining free | **794 MiB** | after both are warm |
| Kokoro-82M (ONNX, GPU) | ~0.35 GB | would fit in the 794 MiB, with little margin |

So the two-model claim holds on this hardware, and the broker permits it with no change — the
load reported `"evicted": []`, meaning the embedder was untouched.

The margin is thin, though. 794 MiB free is enough for Kokoro on paper but leaves nothing for a
context-length spike or another rail's model. Two consequences:

- **Keep `@smb-partner-rag` at 3B-class on this box.** The platform default `gemma3:4b` (3.34 GB)
  plus the embedder plus a voice model does not fit. This is very likely why the original picked
  a 3B model, and it is worth not re-learning.
- **Prefer Kokoro on CPU here.** It is realtime-capable on CPU, and spending the last 794 MiB of
  a shared card on a 350 MB voice model — on a workstation where other rails also want the GPU —
  buys latency we do not need. Run it on GPU only on a box with real headroom.

## The broker gap

Two policies in `services/broker/app/broker.py` stand between us and the screenshot:

**1. One heavy model at a time.** `_evict_other_heavy(keep=model)` runs before every chat and
load, unloading any other *heavy* (generative) model. This is not a problem for us: only
`llama3.2:3b` is heavy, and embedders are explicitly exempt (`embed` class models "are light and
may stay resident alongside", per `Broker.embed`). **The LLM + embedder pair works today, as
designed, with no change.** Verified on this box.

**2. Media takes the whole card.** `_run_media()` — the path behind `/v1/tts` — calls
`_evict_other_heavy()` with **no** `keep` argument, unloading *every* heavy model before the
worker starts, then runs the worker in a short-lived subprocess that exits to reclaim VRAM.

Policy 2 is the blocker. It was written for XTTS v2 and SDXL/FLUX, which are large enough that
they genuinely need the whole card. Under it, one spoken answer costs:

```
evict llama3.2:3b → spawn worker → load Kokoro → speak → exit worker → reload llama3.2:3b
```

That is a model swap per utterance, on a rail whose entire premise is an always-on voice agent.
The status popover could never show both green, because by construction they are never both
loaded.

## The fix — a non-evicting voice path

Kokoro is ~0.35 GB and fast. It does not need the whole card, so it should not take it. The
broker already has a precedent for exactly this: `Broker.embed_image()` runs in the media worker
but deliberately skips the GPU gate and the eviction, with the comment *"it never touches the
GPU, so it must not disturb a resident heavy model — evicting gemma3 to embed on CPU and then
reloading it would be pure thrash."* The same reasoning applies to a small TTS model, and the
same escape hatch should be used.

Proposed, in order of increasing invasiveness:

1. **`ollama pull llama3.2:3b`** and point `@smb-partner-rag` at it. *(Done — see below.)*
2. **Add a `kokoro` op to the media worker** and a `Broker.tts_light()` that runs it *without*
   `_evict_other_heavy()`, gated on the model being in a small-media allowlist. Kokoro then
   loads once and stays warm alongside the LLM — both green, as in the screenshot.
3. **Keep the worker persistent** for the voice op rather than spawning per utterance. The
   spawn-and-exit design exists to reclaim VRAM from torch, which is the right call for a 6 GB
   image model and the wrong one for a 350 MB voice model that we *want* resident.

Until (2) lands, `voice.py` resolves to the **browser** backend: the Web Speech API synthesizes
client-side, costs no VRAM, works on a phone, and leaves both models undisturbed. That is the
current default and it is a genuinely good fallback — but it is a different product from the
demo, because the voice is the platform's, not ours. `VOICE_BACKEND=broker` flips to server-side
synthesis the moment the broker can serve it.

## Current state on this workstation

- `bge-m3` — **installed**, resident at 0.66 GB, serving `@embed`. Verified.
- `llama3.2:3b` — **pulled and verified resident** at 2.55 GB alongside the embedder, serving
  `@smb-partner-rag`. The broker reported `evicted: []`, confirming policy 1 is not in our way.
- Kokoro — **not available.** It is not an Ollama model (`ollama.com/library/kokoro` → 404); it
  needs its own ONNX runtime, so it can only arrive via the broker's media worker.
- Broker media worker — **disabled** (`media.enabled: false`). `BrokerSettings.media_python`
  points at `D:\.ai-work\projects\edu-suite\.venv\...`, a path that does not exist on this
  machine. This is a pre-existing platform issue, not one this rail introduced, and it means
  server-side TTS of *any* kind is currently unavailable regardless of policy.

So step (2) above is blocked behind standing up a media venv on this box. The rail is built to
run correctly either way, and `/api/capabilities` reports which voice backend is actually live
so the UI never claims a capability it does not have.
