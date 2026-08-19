# Handoff — SMB Partner Enablement rail

Written 2026-08-17 at the end of the session that built the SME corpus and the Scenario Builder.
Read this before picking the rail up again; [`BACKLOG.md`](BACKLOG.md) has the task list, this has
the context that is not obvious from the code.

## Where it stands

> **Updated 2026-08-18.** The three "where to pick up" items below are all **done**: the rail is
> deployed, the mobile surface is wired, and voice runs server-side in **both** directions. The
> rest of this file is still accurate context; treat the "Where to pick up" section as history.

Working end to end and **deployed at `localhost:1111`** behind the entitlement gate: six
scenarios, a 67-file sourced corpus across 13 collections (~1,050 chunks), grounded generation
with a live reasoning trace, a standalone mobile SPA at `/smb-partner-enablement/m/`, and
server-side voice — **Kokoro-82M** out (`/v1/tts_light`, backing *Read aloud* via `/api/speak`)
and **faster-whisper** in (`/v1/transcribe` via `/api/transcribe`). Both take no GPU gate and
evict nothing.

**The one non-obvious thing about speech input:** it is server-side because it *had* to be, not
only for privacy. The browser's `SpeechRecognition` has no device API — it always listens to the
OS default input, so the rail's own microphone picker could never steer it, and choosing a headset
produced a silent recording and a `no-speech` error. `getUserMedia({ deviceId })` + `MediaRecorder`
+ broker STT is the only arrangement where the picker means anything. Do not "simplify" it back to
Web Speech.

## How to run it

Two processes. The vite dev proxy already forwards HTTP and WebSocket to the backend.

```bash
# backend — run from src/ so --reload picks up edits
cd rails/smb-partner-enablement
export SMB_PARTNER_SEED_DIR="$PWD/seed/knowledge-base"
export SMB_PARTNER_DATA_DIR=<somewhere writable>
export SMB_PARTNER_STANDALONE=1 PYTHONPATH="$PWD/src"
python -m uvicorn --factory smb_partner.api:create_api --port 8870 --reload --reload-dir src

# frontend
cd frontend && npm run dev     # http://localhost:5260/smb-partner-enablement/
```

**The gotcha that cost this session twice:** `pip install .` does **not** update a running
uvicorn — it keeps the old module in memory. The symptom is confusing, because CSS hot-reloads
while the questions and the trace stay stale. If a change is not showing up, kill the process
(`netstat -ano | grep :8870`; a failed rebind reports `WinError 10013`) rather than assuming the
code is wrong.

## The one principle that matters

**Deterministic in code, judgement in the model.** The rail's model is 3B-class because it has to
stay resident beside the embedder, and it will not reliably respect a hard limit derived from
retrieved prose. Every time it produced something dangerous, the fix was to stop asking it:

| It did this | Fix |
|---|---|
| Recommended Business Premium to a 300+ seat customer while the card cited the cap | `_HARD_RULES` — computed limits it may not contradict, placed **last** in the brief |
| Turned "mostly frontline, a small head office" into "90% frontline, 10% office" | `_scrub_invented_numbers` — drop any figure absent from context |
| Offered "a free trial of Microsoft 365 Defender for Business" (no such support in context) | `_scrub_unsupported_entitlements` — entitlement claims need their product in context |
| Bullet-listed the partner's own answers back as the Scenario Card | `_build_scenario_card` — assembled in code |
| Put "Your next move:" at the *end*, after a page of restatement | Assistant-turn **prefill**, so it continues rather than reformats |

If you add a rule to `_HARD_RULES`, it must map to something the corpus actually says. That table
is the highest-consequence code in the rail and it currently has no test.

## Things that will surprise you

- **`research/` and `documents/` are gitignored and must stay that way.** They hold the internal
  rebuild sources — correspondence, decks, contact details. This repo is public. Scan the staged
  diff before any commit that touches this rail.
- **Restaurant Group is the least-grounded scenario.** Microsoft publishes frontline material for
  exactly four industries — Retail, Healthcare, Financial Services, Manufacturing — and *none* for
  restaurants or field services. It leans on Retail's content. Kept for fidelity to the original
  hackathon demo, but do not treat its answers as equally sourced.
- **Deal registration requires a Microsoft-*managed* customer account,** and most genuine SMB
  customers are unmanaged. The original deck promised partners "walk in with a registered deal";
  for much of the segment that outcome is structurally unavailable. The corpus and the constraint
  table both say so, deliberately.
- **`partner.microsoft.com` returns HTTP 403 to automated fetching.** Use `learn.microsoft.com`.
  The monthly Partner Center announcements page is the single best currency source.
- **Never state a rate, margin or fee that is not citable.** The 15% partner earned credit and the
  60/40 rebate split that circulate are *worked examples* inside Microsoft's docs, not rate cards.
  A list of unsourceable figures to refuse is in `smb-segment/09-sourcing-the-smb-coverage-story.md`.
- **`mcem/00-overview.md` is real content**, despite the `00-overview` name that the deleted
  placeholders also used.

## Where to pick up

**All three items below shipped on 2026-08-18** — kept for the reasoning, not as a task list.
The current list is in `BACKLOG.md`; the top open item is the **warm media worker**, which now
matters twice as much because both voice directions pay the subprocess cold-start (of a 4.8s STT
round trip, only ~1.7s is actual transcription).

*Original note:* in the order I would do it: **deploy it** (nobody can reach it), then **mobile**
(Dan's second pillar, and the surface where voice actually matters), then **Kokoro** (biggest jump
in demo feel, largest infrastructure risk). Detail for each is in `BACKLOG.md`.

## Deploy loop — the trap that cost hours

`build` **silently reuses stale layers here**. Four consecutive gateway builds printed
`Successfully built` while every step said `Using cache`, including the `COPY` of a frontend file
that had demonstrably changed — so none of the fixes reached the browser, and because the layer
was cached `tsc` never re-ran either. A green build proved nothing.

Use `--no-cache` on build and `--force-recreate` on up (`up -d` can print `Running` and skip the
swap entirely). **Each rail is two containers** — the gateway carries the frontend, the rail
backend is separate and behind its own `--profile`; a new endpoint needs both. Then verify inside
the *running* container rather than trusting the log:

```bash
podman exec platform-gateway-1 sh -c 'grep -rlF "/api/transcribe" /app/dist/smb-partner-enablement'
```

Grep for a literal that survives minification (a route, a CSS class); identifier names get mangled.

## Unrelated change carried in

`deploy/installer/platform-watchdog.ps1` had 156 lines of uncommitted improvement in the working
tree from a **prior** session — a real finding about gvproxy's named-pipe DACL belonging to
whichever account starts the podman machine. It parses clean and has no WIP markers, so it was
committed to leave a clean tree, but it was **not authored or tested in this session**. Verify it
before relying on the watchdog's behaviour.
