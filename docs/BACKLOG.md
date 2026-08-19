# Platform backlog

Open items that span the platform rather than one rail. Per-rail backlogs live at
`rails/<id>/BACKLOG.md`; rail-contract gaps live in [`RAIL_CONTRACT.md`](./RAIL_CONTRACT.md)
under "Known gaps".

## Voice

- **Kokoro synthesis is slower than real time on this hardware.** Measured RTF **2.25×** —
  9.7 s to produce 4.3 s of audio — which is poor for interactive read-aloud. The cause is a
  mismatched build, not the GPU: `kokoro_onnx` falls back to `CPUExecutionProvider` because
  DirectML cannot execute this graph at all (forcing `ONNX_PROVIDER=DmlExecutionProvider`
  raises `RUNTIME_EXCEPTION` on `ConvTranspose '/encoder/F0.1/pool/ConvTranspose'`,
  `80070057`), and the installed file is `kokoro-v1.0.fp16-gpu.onnx` — an fp16 GPU build that
  CPU has to convert weight by weight.

  Fix: point `BROKER_KOKORO_MODEL_PATH` at an **int8 or fp32** Kokoro build. This is for
  *speed*; switching "to a CPU model" to save VRAM would achieve nothing, since it is already
  on CPU and uses no VRAM.

- **The media venv has two onnxruntime distributions stacked** —
  `onnxruntime-1.29.0` and `onnxruntime_directml-1.24.4` both installed into the same
  `onnxruntime` package. That is how DirectML came to be advertised by
  `get_available_providers()` while being unusable. Worth resolving to one flavour so the
  provider list stops lying, even though the CPU fallback is correct here.

- **Browser dictation has not been confirmed by a human.** The path is
  `DictateButton` → `onTranscript` → the rail's own state setter, so there is no DOM write and
  none of the React value-tracker hazards apply — but nobody has watched text land in a field
  and then saved it. Worth one manual pass in co-worker's search box.

- **Rails with bespoke voice have not been migrated.** `smb-partner-enablement` and
  `gemini-cx` still carry their own `voice.py` / `voice.ts` and their own device pickers,
  predating the platform capability. Consolidating them onto `@web-core`'s chips would delete
  real duplication (~470 lines of `voice.ts` alone), but it is a behaviour change to two
  working rails and wants its own session. Deliberately not started: adding a *second* bespoke
  client is the drift the platform capability exists to prevent, and replacing a working one is
  a different risk.

## Broker

- **An eviction was observed while verifying voice and was never attributed.** On this 8 GB
  laptop GPU a resident 3 GB model disappeared around a cold TTS call, but voice provably
  cannot be the cause: `tts_light` never calls `_evict_other_heavy` (asserted structurally),
  and Kokoro runs on CPU. The desktop alone swung between 2.6 and 7.2 GB of the 8 GB card
  during testing, which is the more plausible explanation. Two of the four test runs were also
  contaminated — one raced a broker restart. If it recurs, instrument what else holds VRAM
  before suspecting this path.

  Note the platform docs assume a 24 GB RTX 4090; the co-residency arithmetic in
  `rails/*/MODELS.md` does not hold on an 8 GB card.

## Repository hygiene

- **One historical commit message names the employer.** `258cfe0`'s message reads "…are
  indistinguishable from real Accenture engagement data" — a *remediation* note explaining why
  example names were replaced. No file content in any commit contains it (verified with
  `git log --all -S` and `git grep` across history), and no client, colleague, or personal data
  is tracked or has ever been committed. Removing it means rewriting public history and
  force-pushing, which would disrupt anyone else working from this remote, so it is left as an
  owner decision rather than done unilaterally.
