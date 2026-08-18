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

**No media/voice dependency.** This rail never calls `/v1/tts`, `/v1/tts_light` or `/v1/image`,
so the broker's media worker being disabled on this box is irrelevant to it.

That was originally a hard constraint — the old `/v1/tts` path calls `_evict_other_heavy()` with
no `keep`, so speaking would evict the answer model on every utterance and destroy this rail's
co-residency. **That constraint has since been lifted:** `Broker.tts_light()` (Kokoro-82M) runs
in the media worker with no GPU gate and no eviction, following the `embed_image()` precedent, and
at ~350 MB the voice model coexists with a resident RAG LLM on the same card.

So voice is now *possible* here. It remains **out of scope by choice** rather than by constraint:
this rail is a desk tool for scoping and delivery work, and its answers are dense, citation-heavy
prose that reads far better than it listens. If voice is ever wanted, budget roughly
`gemma3:4b` (3.34 GB) + `bge-m3` (0.66 GB) + Kokoro (~0.35 GB) ≈ **4.35 GB**, which is at the
practical ceiling on this card — so a voice build should drop the generative model to 3B-class,
exactly as the SMB Partner rail does.
