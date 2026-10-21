# Watch Builder ⌚

Design a Pebble smartwatch watchface in the browser, compile it for all seven Pebble
platforms, and see it rendered on real emulators.

The rail's declared shape is [`rail.json`](rail.json). Why it is built this way, and what was
measured rather than assumed, is [`docs/DESIGN.md`](docs/DESIGN.md).

## What it does

- A canvas editor over a JSON **design document**: fifteen element types, coordinates as
  percentages so one document is correct on four different screen resolutions.
- A **live canvas** at the platform's real pixel size, with drag-to-move and corner-resize.
  It draws from the backend's resolved boxes rather than repeating the arithmetic, so the
  preview cannot drift from the build.
- **Image upload** with per-platform resampling. The SDK cannot scale a bitmap at runtime
  (a small rect clips, a large one tiles), so each platform gets its own variant at the exact
  size it will be drawn, dithered to 1-bit on the black-and-white platforms.
- A **layout audit** that resolves every element per platform and reports cropping, text
  overlap, Quick View collisions and round-rim problems in pixels.
- A real **compile** through the Pebble SDK, which is baked into this rail's image, plus a
  **screenshot** from each target emulator.
- A **sunlight-corrected** preview, because Pebble's reflective screens desaturate hard and
  the raw emulator image shows colours no wrist ever sees.
- Eight **presets** read off a real, documented Pebble Time 2 face, including the two
  designs its author rejected. Each one says which of its elements report real data and
  which only depict a state, because most of what drove the original was a calendar, a
  task list and a work timer that this rail does not have.
- Two **AI features**: describe a face in words, and explore variations of one you have.

## Presets

Eight starting points, from Jonas Hietala's write-up of designing a personal Pebble Time 2
face. Measured rather than eyeballed: his post carries 49 inline SVG prototypes at
`viewBox="0 0 200 228"`, which is emery's exact resolution.

| id | after | what it is for |
|---|---|---|
| `muted-first-draft` | his original | The clean, uniform design he finished before the watch arrived and was sure was perfect. Then he built it: "it sucked. It was so boring." |
| `comic-panel` | the new comic style | What replaced it, and what he wears. Thicker lines, brighter colours, deliberate irregularity. |
| `loaded-day` | fully loaded day | The same face with the day packed. The stress test. |
| `uniform-events` | uniform events | The tidy variant he rejected, which he admits suits the Round 2 better. Tick gabbro and see. |
| `quiet-dial` | when it's clear | Nothing on the calendar. The state the face is in most of the time. |
| `next-task` | the next task bar | One actionable thing, from Getting Things Done. Rectangular screens only, for a measured reason. |
| `step-badge` | bronze / silver / gold / diamond | His answer to the step counter after three numeric attempts he disliked: tiers, and no number anywhere. |
| `break-alarm` | time for a break | The alarm you have to walk twenty steps and tap repeatedly to clear, which is why the face exists. |

All eight target **emery only** by default, which is also the default for a new blank
design. Seven of them render correctly on gabbro too, verified by building and screenshotting
both, and on the round screen the wedges clip into clean arc segments that arguably look
better. It is one tick in the platform list. The default is one watch because choosing a
round layout is a design decision rather than a resolution setting, and because defaulting to
two compiled and booted two emulators on every build before the layout was settled.

`next-task` is pinned to emery and will not follow that default: a bar 88% wide at 86% down
has its corners 165px from the centre of a 260px round screen, and the bezel leaves 126. A
bar narrow enough to fit the circle is too short for a task.

Two things to know before editing one:

**The wedges are calendar events placed by TIME UNTIL, not clock time.** Three o'clock means
three hours away, twelve means now, and an event drifts toward twelve as it approaches. So
`_event_wedge` takes hours. The rim is a countdown, not a clock.

**Most of it is a depiction, and each preset says which parts.** `live` names the elements
wired to real data; `limits` names the rest. The original is driven by a calendar, Todoist and
a work timer through a companion phone app, none of which this rail has.

The original, with its prototypes, is his post:
<https://jonashietala.se/blog/2026/06/26/designing_a_personal_pebble_watchface>. The
element-by-element breakdown with the measured palette collisions was written up in a separate
Pebble design knowledge base, which is not published.

## The two AI features

Both emit a schema-validated design document. Neither ever writes C, which is the whole
safety argument: a hallucinated field fails validation, and a valid document cannot produce
C that fails to compile.

Model slot: `designer` → `@watch-builder-designer`, repointable from **Admin → Rails**.

Every AI action asks first. `/api/ai/estimate` reports the call count and approximate seconds,
and whether they are GPU seconds or, for a viewer the broker answers from the Claude CLI, calls
on that viewer's own subscription with no GPU unless one falls back to the local model. Nothing
runs until a second click, because the GPU may be shared with other work.

Both actions run as background jobs (`backend/watch_builder/jobs.py`). The POST answers 202
with the job at once, and the page polls `GET /api/ai/jobs/{id}` every 1.5 s for progress (in
variants for a variation pass, each variant shown as soon as it is finished), then the result
or the error. So no request waits on a model, and every call waits the whole
`broker.CHAT_TIMEOUT` (480 s) and carries the person who asked.

**Cancel job** sends `DELETE /api/ai/jobs/{id}`: no new call starts after a cancel; a call
already sent finishes and its answer is dropped. The cancel does not reach the broker, so a
call sent before it runs there in full: a CLI call still waiting for a slot starts once one
frees, and a CLI failure still falls back to the local model.

A job nobody is watching stops. Each job holds a 5-minute lease (`jobs.LEASE_S`) from the
moment it is submitted, and every read of it by its owner sets the lease to 5 minutes from
that read, never adding to what is left: the page's poll, the listing a reloaded page reads,
and a keep-alive read of the job every 30 s (`KEEPALIVE_MS` in `frontend/src/module.tsx`)
whenever the poll is not reading it. That is while the tab is hidden, while the viewer is on
another rail in the same platform tab, and after the poll has lost touch with the rail. The
keep-alive lives in the module, outside React: the shell unmounts a rail it is not showing,
but the rail's federated module stays loaded while the tab is open. Browsers clamp a hidden
tab's timers to about once a minute, which the 5 minutes outlasts several times over, so
switching tabs, minimising or moving to another rail keeps the job.

The keep-alive keeps the job's id and nothing any read of it answered. The shell signs out
and back in without a reload, so the next person to open Watch Builder in the tab may be
someone else, and a page shows only what the rail answered a read made as its own viewer.
Coming back to Watch Builder reads the kept job again: the owner's job carries on, and
anyone else's read of it is the 404 an unknown id gets, which shows nothing and ends the
keep-alive. The page takes the keep-alive's reads of that job as it takes its own: until one
has shown it the job, a 404 may be someone else's job as well as a lost one, so it says
nothing and ends the keep-alive. If the rail does not answer that first read, the page says
it has lost contact, without naming any job, and asks on until a read is answered. A pass
that finished while no page of Watch Builder was mounted (the viewer was on another rail) is
offered by the listing (`GET /api/ai/jobs`), on the way back, after a reload or in a new tab:
the keep-alive's read with no page mounted carries `?keepalive=1`, which renews the lease
without marking a finished job delivered. Every read a page shows is a plain read and
delivers a finished job: its poll, its read as it mounts, and the keep-alive's while it is
mounted (hidden, or out of touch). So a pass seen to the end is not offered again, and a pass
that finished in a hidden tab is shown, and delivered, in that tab: if the browser then
discards and reloads that tab, the listing does not offer it again (the rail still keeps it
for 30 minutes, but no page asks for it).

The poll says it has lost touch after 90 s of failed reads (`MAX_FAILED_POLLS`), but the
keep-alive asks on until the rail answers (the poll then resumes), the job is gone (a 404) or
it is final, so a gateway restart or a dropped connection costs the job nothing as long as one
read gets through within the lease. A 401 or 403 (signed out) or a 404 ends it, whether or
not Watch Builder is open, and an open page that has shown the job says which. Each read has
a 30 s deadline, so one that never settles counts as one failure and the next read is sent.
The deadline is a timer, not `AbortSignal.timeout`, which Safari before 16 and Chrome before
103 lack.

Closing the tab or the browser stops the job within the lease of its last read plus the call
then in flight. No new call starts once the lease has lapsed; a call already sent finishes,
and if that completes the variant or the design, it is kept. A variant that still needed its
reformat or repair is marked with the reason, like every variant after it, and a design that
did is failed. The job ends as one its ceiling stopped does, with `stopped: "unwatched"`: a
variation pass is done with the variants that finished. Reopening Watch Builder within the
30-minute keep window shows it: "Stopped: nobody was watching it", with how many variants
were kept. A reload, or a second tab of the same person, renews the lease like the poll does;
anyone else's read is a 404 and renews nothing. A browser that freezes a background tab
outright (some do, to save power) stops its timers too, so its job stops as if the tab were
closed, and the running job's line names that exception.

The bounds, with the reasons in `jobs.py`: one queued or running job per person, and at most
two whose worker has not returned (a cancelled one still in a call, and one queued behind it).
A submission past either is refused with a 409 naming the person's job. For a queued or
running job the page then shows that job's progress, and a design it finishes is offered (**Open this
design**) rather than put in the editor, which may hold edits that tab has not saved. No call
starts after a job has run 45 minutes, a finished job is kept 30 minutes, and at most 64 jobs
are held. A page reloaded mid-job finds the viewer's job through `GET /api/ai/jobs` and
carries on. A restart of the rail loses every job in flight; a page showing the job sees the
404 and says so rather than polling on. A page that opens after the restart shows nothing of
it, since that 404 is also what someone else's job gets.

## API

| Route | Purpose |
|---|---|
| `GET /api/health` | liveness |
| `GET /api/capabilities` | model chips, `build_available`, SDK version, platform table |
| `POST /api/validate` | schema, semantics, per-platform layout audit, colour shift |
| `GET /api/presets` | eight starting points, each a full document plus `live` and `limits` |
| `GET /api/presets/{id}` | one of them; 404 lists the known ids |
| `POST /api/source` | the generated C project as files (works with no SDK) |
| `POST /api/build` | compile, screenshot, return the `.pbw` and both image variants |
| `GET /api/ai/estimate` | what an AI action will cost this viewer, before spending it |
| `POST /api/ai/design` | a prompt in; 202 with a job whose result is a validated design document |
| `POST /api/ai/variations` | a design in; 202 with a job whose result is N variants along named axes |
| `GET /api/ai/jobs` | the viewer's own AI jobs, newest first; renews each one's lease |
| `GET /api/ai/jobs/{id}` | one job: state, progress, partial variants, result or error, and `stopped`; renews its lease |
| `DELETE /api/ai/jobs/{id}` | cancel it: no new call starts; a call already sent finishes and its answer is dropped |
| `GET/POST/DELETE /api/designs[/id]` | owner-scoped saved designs |

Identity is enforced app-wide; a request without `X-Platform-User` is a 401, including
`/api/health`.

## Running it

In the platform stack, compose builds it. Standalone:

```bash
cd backend && pip install -e .
PLATFORM_STANDALONE=1 python -m watch_builder.main    # :8920

cd ../frontend && npm install && npm run dev           # :5370
```

**Standalone dev on Windows cannot compile.** The Pebble SDK has no Windows build, so
`build_available` reports false and the build button is hidden. Design, validation, the
layout audit and the colour report all work; use `POST /api/source` to export a project, or
run the container.

## Tests

```bash
# from the repo root
PYTHONPATH=rails/watch-builder/backend .venv/Scripts/python -m pytest rails/watch-builder/tests -q
```

No GPU and no SDK required. The broker is stubbed in `test_ai.py` on purpose: a suite that
quietly spends inference time is a suite that gets switched off.

## Three things worth knowing before editing

**Percentages, not pixels.** The four screen resolutions in the Pebble family are 144x168,
180x180, 200x228 and 260x260. Pebble's own first-watchface tutorial hardcodes a vertical
offset and renders with a dead bottom third on both new watches. The document format exists
to stop that.

**System fonts do not scale.** Every system font line height is byte-identical across all
seven platforms, measured. `font: "auto"` picks the largest appropriate face for the target;
pinning a font is pinning 144x168-sized type onto a 260x260 screen.

**A green build is not a working layout.** The first end-to-end test of this rail's own
generator produced a clean seven-platform build in which the time ran through the date on
five panels. That is why `layout.py` exists and why `/api/validate` runs on every edit.
