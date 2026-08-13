# Co-Worker item schema

The contract between the **harvest process** (Claude co-work scheduled tasks) and the
**Co-Worker rail**. Both sides must agree on this file.

**Schema version: 2**

> **What changed in v2 (2026-08-11).** v1 stamped filenames with the clock, so re-running a
> loop wrote a second copy of every finding instead of replacing it — one double-run
> permanently doubled that period's cards. v2 makes item ids **deterministic**, so a rerun
> overwrites itself. Also added: `period` (grouping that doesn't collide), retention and
> pruning, a stated `related` policy, and persisted triage state.
>
> **v2.1 (2026-08-12).** Closed the two remaining duplication paths and bounded growth:
> `teams` moved from a day period to a **week** period (its 30-day rolling window
> re-surfaced the same dangling commitments under a new id on every new run date);
> replace-set was widened to **every period a run covers**, not just one; slug reuse was
> made mandatory; and retention split into two tiers so the dashboard window can stay
> short while history stays long. **Any loop may now be re-run any number of times per day
> without producing a duplicate.**
>
> **v2.2 (2026-08-12).** Gave structure to reasoning that previously had to be smuggled
> through prose: typed `related` edges (a recommendation and the habit it answers are no
> longer merely "related"), `metrics` with `prev` so a measurement and its trend delta have
> a home outside `evidence`, `confidence` instead of hedging inline, and `competing` so a
> `conflict` can name what collides and which one wins. **All four are optional and
> additive** — `schema` stays `2` and every existing item remains valid. Backend adds
> `?period=` on `/api/inbox` and a new `/api/archive`, so trend history is reachable.
>
> **v2.3 (2026-08-12).** All four loops independently asked for the same three things after
> their first v2.2 run, which is the signal this responds to. `verification` states how the
> source was actually read — the distinction that let a `summary`-derived finding be published
> looking identical to a verified one. `metrics` gains `n`, `direction`, `target` and a
> per-measurement `confidence`: sample size and "which way is good" were both falling back to
> prose, and **the rail cannot render a trend arrow without `direction`**, since a rising
> number is good for throughput and bad for latency. `series` lets a calendar item say the
> commitment expires on its own. Still additive; `schema` stays `2`.
>
> **v2.4 (2026-08-12) — the freeze.** Four fields, each earned by something that happened in
> the v2.3 cycle rather than anticipated. `metrics[].kind` separates a **correction** from a
> **movement**: `prev` renders as a directional arrow, so a recount displayed as a trend
> actively misreports improvement — five retractions landed in one cycle with nowhere to say
> they were retractions. `metrics[].n` now accepts `0`, because "0 of ~125 channels reachable"
> is a real denominator. `metrics[].verification` mirrors per-measurement `confidence`, since
> a synthesis item mixes computed numbers with inherited ones. And `retracts` joins the rel
> types, because `supersedes` means "now stale" while two withdrawn findings were "proved
> wrong" — different admissions. Still additive; `schema` stays `2`.

## Two artifacts per run

Each harvest loop emits **both**:

| Artifact | Path | Consumer |
|---|---|---|
| Narrative markdown | `inbox/<source>/*.md` | Humans, and the dashboard's drill-through modal |
| Item JSON | `inbox/*.json` (**flat**) | The rail — one file per finding, rendered as a card |

The backend globs **`inbox/*.json` — flat, non-recursive** (`main.py: inbox_list`). Markdown
lives one level down in per-source subfolders, which is what keeps it invisible to that glob.
Never nest JSON items in subfolders; they will not be found.

`inbox/archive/` holds pruned items. Also invisible to the flat glob — that's the point.

## Filenames — deterministic. This is the idempotency mechanism.

```
<period>_<source>_<slug>.json

2026W33_calendar_conflict-wed-double-book.json
20260811_email_vpn-tunnel-access-request.json
2026W33_teams_dangling-vpn-answer-owed.json
2026W33_insights_meeting-load-climbing.json
```

**No clock time in the filename.** The id is a function of *what the finding is*, not *when
the run happened*. Two runs over the same period produce the same id for the same finding and
the second overwrites the first.

| Source | `<period>` | Format |
|---|---|---|
| `calendar` | ISO week of the Monday | `2026W33` |
| `insights` | ISO week of the Monday | `2026W33` |
| `email` | the day the item's email **arrived** | `20260811` |
| `teams` | ISO week of the run | `2026W33` |

**Pick the period so that everything a run can re-discover lands in the same period.**
That is the whole trick. `email` keys on the day the mail *arrived*, not the run date, so an
email surfaced by both today's and tomorrow's run keeps one id. `teams` keys on the week, not
the run date, because its 30-day window re-surfaces the same dangling commitment every time
it runs. Get this wrong and re-runs duplicate no matter how stable the slug is.

- `<source>` is always one of `calendar` \| `email` \| `teams` \| `insights` — **the source,
  never the type.** A `noise` item from the email loop is `..._email_...`, not `..._noise_...`.
- `<slug>` — short, stable, kebab-case, descriptive of the finding. **Stability matters:**
  the same finding next run should produce the same slug so it overwrites rather than
  duplicating. Describe the finding, not the date (`conflict-wed-double-book`, not
  `conflict-aug-13`).
- **No colons** — illegal in Windows filenames.
- The filename stem becomes `_id` and is used by `GET /api/inbox/{id}`, which rejects ids
  containing `/`, `\`, or a leading `.`. Keep stems alphanumeric plus `-` and `_`.

### Replace-set semantics — required

A run **owns every period it covers**, and publishes the complete, current set for those
periods. Concretely, before writing anything:

1. Work out the full list of periods this run covers. Usually one. The email loop covers two
   when its window spans yesterday and today.
2. For each covered period `<p>`, **delete every existing `<p>_<source>_*.json`.**
3. Write this run's complete set.

Overwriting alone is not enough: if this run finds fewer issues than the last, the resolved
ones would linger forever as stale cards. Deleting periods you did **not** cover is equally
wrong — that is another loop's or another day's data.

### Slug reuse — required

Before step 2, **read the ids you are about to delete and reuse them.** If a finding is the
same finding as last run, it must get the same slug. Free-associating a new slug for an
unchanged finding is the single easiest way to reintroduce duplicates the moment the
replace-set window moves, and it orphans the item's triage state in `.state.json`.

Sorting is by file mtime (newest first), which continues to work because overwriting updates
mtime.

### The idempotency contract

Taken together: **running any loop N times in a row produces exactly the same set of files as
running it once.** Every loop is safe to run manually, at any hour, as many times as you like.
If a re-run changes the file *count* for a period it covers, something above was skipped.

## Fields

Backend injects `_id`, `_file`, `_mtime`, and `_status` on read — never write those.

| Field | Type | Required | Notes |
|---|---|---|---|
| `schema` | int | ✅ | Always `2`. The frontend warns loudly on a version it doesn't know. |
| `type` | string | ✅ | See type table below. |
| `source` | string | ✅ | `calendar` \| `email` \| `teams` \| `insights` |
| `period` | string | ✅ | Matches the filename's `<period>`. Group on this for trend deltas — **not** on `run`. |
| `title` | string | ✅ | Short. Card headline. Under ~80 chars. |
| `why` | string | ✅ | **One line: why this matters.** Required on every item, no exceptions. |
| `body` | string | ✅ | Summary. Markdown-lite: headings, `**bold**`, `` `code` ``, `- ` bullets, `|` tables, `>` quotes all render. Keep it a summary — `doc` carries the long form. |
| `priority` | int | ✅ | `1`–`5`, **1 is highest**. Drives default sort. See priority rules. |
| `client` | bool | ✅ | Is this client work? Client outranks internal, always. |
| `when` | string\|null | ✅ | ISO-8601 **with offset** for the thing itself. Null if not time-anchored. |
| `due` | string\|null | ✅ | ISO-8601 with offset if action is owed by a deadline, else null. |
| `from` | string\|null | ✅ | Person or entity. Display name over email address. |
| `run` | string | ✅ | `<source>-YYYY-MM-DD` — the execution that last wrote this item. Provenance only; **do not group on it**, two runs the same day collide. |
| `doc` | string\|null | ✅ | Relative path to the narrative markdown, e.g. `calendar/2026-08-17-week.md`. Served by `GET /api/doc/{path}` and opened in the dashboard's drill-through modal. |
| `tags` | string[] | — | Freeform labels. Defaults `[]`. |
| `links` | object[] | — | `[{ "label": "Open in Outlook", "url": "https://…" }]`. Real Graph `webLink` values, copied verbatim. |
| `related` | (string\|object)[] | — | Other **co-worker item `_id`s**, optionally typed. See the `related` policy below. |
| `thread_id` | string\|null | — | Groups items from one conversation or series. |
| `evidence` | string\|null | — | Where the claim came from. **Required on every `insight`.** |
| `metrics` | object[] | — | Quantities behind the claim. See **Measurements**. |
| `confidence` | string\|null | — | `high` \| `medium` \| `low`. See **Confidence**. |
| `competing` | object[] | — | What collides, for `conflict` items. See **Conflicts**. |
| `verification` | string\|null | — | How the source was actually read. See **Verification**. |
| `series` | object\|null | — | Recurrence, for `calendar` items. See **Series**. |

**Timezone rule:** every datetime carries an explicit offset. Justin is US Pacific: `-07:00`
during PDT (Mar–Nov), `-08:00` during PST (Nov–Mar). Graph returns UTC; the harvest converts
and writes the correct offset for that date. `tools/validate_inbox.py` enforces this.

### `related` policy

`related` holds **co-worker item ids only** — the filename stem of another `.json` in this
inbox. It must **never** contain a Microsoft Graph message id, event id, or `itemid`. Graph
ids look like `AAkBOQAICN73O3ysgAAuAAAAAB2EAxG…`, resolve to nothing, and are silently
dropped.

- To link **out** to the source system → `links` with the Graph `webLink`.
- To link **across** the harvest → `related` with item ids.

**Cross-source edges** are welcome from any loop — they're the most valuable kind — but they
must **resolve**. Because ids are deterministic you cannot guess another loop's slug, so
before writing a cross-source ref, **list `inbox/*.json` and use a real id.** An unresolved
ref means it was guessed, and it fails silently.

If you want to note a connection you can't address, say so in `body` as prose. Never invent
an id. The **`insights` loop reads every item** and is responsible for adding the cross-source
edges the other loops missed.

#### Typed edges

A bare string is an untyped edge and stays valid forever. To say *how* two items relate, use
an object instead — mixing both forms in one list is fine:

```json
"related": [
  "2026W33_calendar_focus-block-breached",
  { "id": "2026W33_teams_insight-open-loop-pattern", "rel": "answers" }
]
```

| `rel` | Meaning |
|---|---|
| `relates-to` | Default. Same as a bare string. |
| `answers` | This item is the remedy for that one. A recommendation → the habit it fixes. |
| `derives-from` | This was computed from that — an insight from the items it generalizes. |
| `duplicates` | Same finding surfaced by another loop. |
| `supersedes` | This replaces that, which is now **stale** — it was true, and has been overtaken. |
| `retracts` | This withdraws that, which was **wrong** — published on a mistaken reading, not overtaken by events. |
| `blocks` | That can't proceed until this does. |

`supersedes` and `retracts` are not interchangeable. "No longer current" and "should never have
been published" are different claims about a finding, and only the second is an admission. Two
false `dangling` items were withdrawn in one cycle; both would have read as routine staleness
under `supersedes`.

The distinction that matters: **a recommendation and the habit it addresses are not merely
"related."** Untyped, the only thing connecting them is prose describing the link — which is
formatting standing in for structure.

### Measurements — `metrics`

Any number behind a claim goes here instead of being written into `evidence` as a sentence.

```json
"metrics": [
  { "label": "median first response", "value": 19.5, "unit": "hours", "prev": 6.2 },
  { "label": "commitments older than 14d", "value": 4, "unit": null, "prev": 2 }
]
```

| Key | Type | Required | Notes |
|---|---|---|---|
| `label` | string | ✅ | What was measured. |
| `value` | number | ✅ | The measurement. A real number, not a string. |
| `unit` | string\|null | — | `hours`, `days`, `%`, `count`… Null when the label carries it. |
| `prev` | number\|null | — | Same measurement last period. **This is where a trend delta lives** — the rail computes the delta, so never write "up from 6.2" in prose. Null on the first period means *baseline*, not missing. |
| `n` | int\|null | — | Sample size. `median 36.5s` over 6 observations is a different claim than over 600, and without this the distinction falls back to prose. **`0` is legal and meaningful** — "0 of ~125 channels reachable" is a real denominator, distinct from omitting `n` because nothing was counted. |
| `kind` | string\|null | — | `movement` (default) \| `correction`. **Whether the number changed because reality moved or because the earlier measurement was wrong.** See below. |
| `verification` | string\|null | — | Per-measurement `full-read` \| `summary` \| `inferred`, mirroring per-measurement `confidence`. A synthesis item routinely mixes a computed number with an inherited one. |
| `direction` | string\|null | — | `up-good` \| `down-good` \| `neutral`. **Which way is better.** The rail cannot colour a trend arrow without it — a rising number is good for throughput and bad for latency, and nothing else in the item says which. |
| `target` | number\|null | — | The value this should reach, when one exists. Lets the rail show distance-to-goal rather than a bare number. |
| `confidence` | string\|null | — | Per-measurement `high` \| `medium` \| `low`. Item-level `confidence` is too coarse when one item carries a directly-measured number alongside an inherited one. |

`evidence` stays prose and explains *where the number came from*. `metrics` carries the number
itself. An `insight` that quantifies anything should populate both.

#### `kind` — a correction is not a trend

`prev` renders as a directional arrow, which asserts *movement over time*. When a figure
changes because the earlier count was wrong, that arrow is a lie: it reports improvement where
what actually happened is that we measured badly before.

```json
{ "label": "hedge closure", "value": 50.0, "unit": "%", "prev": 28.6,
  "kind": "correction", "direction": "up-good",
  "confidence": "high", "n": 5 }
```

- `movement` — default, and what you should assume. Reality changed between periods.
- `correction` — the same underlying reality, recounted. The rail shows this as a restatement,
  never as a trend arrow.

**Set it whenever `prev` and `value` differ for any reason other than elapsed time.** Five
retractions landed in a single cycle with nowhere to record that they were retractions; the
only safe option was dropping them into prose, which is exactly the behaviour v2.2 and v2.3
removed everywhere else.

### Confidence

```json
"confidence": "medium"
```

`high` \| `medium` \| `low`, or omit it when the claim is a plain observation. Use it instead
of hedging inline — "this may be" and "roughly" in a `body` are caveats the rail can't read,
so they can't be filtered, sorted, or surfaced. State the caveat's *reason* in `evidence`.

An `insight` marked `low` is still worth writing; an unmarked wrong one is not.

### Conflicts — `competing`

For `conflict` items: name what actually collides and which one wins. A conflict whose
contenders exist only inside `body` prose can't be rendered, counted, or acted on.

```json
"competing": [
  { "label": "Project Status Update", "ref": null, "start": "2026-08-13T11:00:00-07:00",
    "end": "2026-08-13T11:15:00-07:00", "verdict": "take" },
  { "label": "Org All-Hands", "ref": null, "start": "2026-08-13T11:00:00-07:00",
    "end": "2026-08-13T11:50:00-07:00", "verdict": "drop" }
]
```

| Key | Type | Required | Notes |
|---|---|---|---|
| `label` | string | ✅ | The event name. |
| `ref` | string\|null | — | A co-worker item id if one exists for it. Never a Graph id. |
| `start` / `end` | string\|null | — | ISO-8601 with offset, same rule as `when`. |
| `verdict` | string\|null | — | `take` \| `drop` \| `defer` \| `delegate`. Exactly one entry should be `take`. |

`why` still carries the one-line reasoning. `competing` carries the structure.

### Verification — how you actually read the source

```json
"verification": "full-read"
```

| Value | Meaning |
|---|---|
| `full-read` | You read the underlying messages or events end-to-end. |
| `summary` | You worked from a search result, preview, or digest — not the source itself. |
| `inferred` | You reasoned to this without reading the source at all. |

Omit it only when the distinction genuinely doesn't apply.

**This field exists because of a specific failure.** A `dangling` was published claiming a
commitment had hung for 14 days; an end-to-end read later showed it had been delivered and
read within 11 minutes. The run had exhausted its read quota and built the finding from a
search summary — a `summary` finding presented identically to a `full-read` one, with the
caveat narrated in `evidence` where nothing could act on it.

A `summary` or `inferred` finding is still worth publishing. One that *looks* verified is not.
Pair it with `confidence`, and put the reason in `evidence`.

### Series — `series`

For `calendar` items about a recurring commitment:

```json
"series": { "recurrence": "weekly", "series_end": "2026-08-25T00:00:00-07:00", "occurrences": 3 }
```

| Key | Type | Required | Notes |
|---|---|---|---|
| `recurrence` | string | ✅ | `daily` \| `weekly` \| `biweekly` \| `monthly` \| `irregular`. |
| `series_end` | string\|null | — | ISO-8601 with offset when the series stops on its own. A forum that expires by itself needs no intervention, and nothing else in the item can say so. |
| `occurrences` | int\|null | — | Meetings remaining in the window. |

## Types

| `type` | Meaning | Typical source |
|---|---|---|
| `meeting` | A meeting needing attention or prep | calendar |
| `agenda-draft` | Drafted agenda for a meeting Justin owns that had none | calendar |
| `conflict` | Scheduling collision, with a recommendation | calendar |
| `prep` | Prep material or agenda surfaced from someone else's invite | calendar |
| `email` | An email ranked for response | email |
| `dangling` | A commitment Justin made and never closed ("let me check…") | teams |
| `follow-up` | Something owed to someone | any |
| `reminder` | Time-anchored nudge | any |
| `fyi` | Context, no action | any |
| `noise` | Spam, or a thread he's cc'd on but not engaged in | email, teams |
| `insight` | An observed pattern, trend or habit | teams, insights |
| `recommendation` | A concrete suggested change | insights, email |

## Priority rules

Justin's stated ordering: **client work is #1. Internal may be important, but never as
important as client.** Encoded so the ranking can be critiqued over time:

| Priority | Meaning |
|---|---|
| `1` | Client, and blocking or time-critical — a client is waiting, or a decision point is imminent |
| `2` | Client, needs action this week |
| `3` | Internal, needs action — or a dropped commitment of any kind |
| `4` | Internal, informational; broadcast meetings; low-stakes FYIs |
| `5` | Noise — spam, passive cc, anything safely ignorable |

A `dangling` item is never worse than `3` regardless of source: an unanswered promise is a
credibility problem.

## Triage state

`PATCH /api/inbox/{id}` with `{"status": "open" | "done" | "dismissed"}`.

State is stored in a **sidecar** file, `inbox/.state.json`, mapping item id → status. Harvest
output files are never mutated, so a rerun can freely overwrite an item without destroying
triage. Because ids are deterministic, state survives reruns and attaches to the right item.

The backend merges it in as `_status` on read (defaulting to `open`). Items whose id no longer
exists are garbage-collected from the sidecar by `tools/prune_inbox.py`.

## Retention and pruning — two tiers

Items accumulate: the email loop alone runs daily. `tools/prune_inbox.py` enforces two
separate windows, because "how many cards should the dashboard show" and "how much history
should we keep" are different questions and one number cannot answer both.

```
inbox/*.json          tier 1 — the DASHBOARD window (short, actionable now)
   │  ACTIVE_DAYS
   ▼
inbox/archive/*.json  tier 2 — the HISTORY window (long, feeds trend deltas)
   │  RETENTION_DAYS
   ▼
   deleted
```

| Source | Tier 1 — on the dashboard | Tier 2 — kept in `archive/` |
|---|---|---|
| `email` | 7 days | 30 days |
| `calendar` | 7 days (current week) | 26 weeks |
| `teams` | 14 days | 26 weeks |
| `insights` | 14 days | 26 weeks |

Anything with `_status: "done"` or `"dismissed"` is archived after 7 days regardless of source.

**Narrative markdown is swept on the same retention numbers, single-tier.** Briefs never move
to `archive/`: they already live in a per-source subfolder that the flat glob cannot see, so
relocating them would only break the `doc` paths behind drill-through. They are deleted once
past `RETENTION_DAYS`, with one guard — **a brief still referenced by any surviving item,
active or archived, is never deleted**, because a dead drill-through link is worse than a
stale file. That guard means a brief outlives its items by exactly one pruner run; the sweep
converges on the following run and is then a no-op. Files that are not `*.md` inside those
subfolders (e.g. `teams/chat-registry.json`, which is loop scratch state) are never touched.

Tier 1 is what keeps `inbox/` from flooding — the flat glob only ever sees roughly one to two
periods per source, so the root count is bounded and flat over time no matter how long the
system runs. Tier 2 is the actual data-retention policy, and it terminates: the archive is not
a landfill.

**Every loop runs the pruner at the end of its own run.** Not just `insights` — that made
pruning depend on one loop staying healthy. It is idempotent and cheap, so running it four
times a week costs nothing and means clutter cannot accumulate if a loop is paused. There is
no separate cleanup task to remember, and nothing to maintain.

Archived items remain readable on disk; the insights loop reads `inbox/archive/` when
computing long-range trends.

## Example

```json
{
  "schema": 2,
  "type": "conflict",
  "source": "calendar",
  "period": "2026W33",
  "title": "Wednesday 11:00a is double-booked",
  "why": "The 15-minute client status call is the one that actually needs you; the other two are a broadcast and your own focus block.",
  "body": "Three events collide Thu Aug 13, 11:00a PT:\n\n- **Project Status Update** (11:00–11:15, tentative) — client delivery accountability\n- **Org All-Hands** (11:00–11:50) — ~250-person broadcast\n- **Focus Block** (11:00–12:00) — your own focus block\n\nTake the client call, drop the broadcast, reclaim the rest.",
  "priority": 2,
  "client": true,
  "when": "2026-08-13T11:00:00-07:00",
  "due": null,
  "from": "Alex Rivera",
  "run": "calendar-2026-08-17",
  "doc": "calendar/2026-08-17-week.md",
  "tags": ["conflict", "acme-program", "focus-block"],
  "links": [{ "label": "Open in Outlook", "url": "https://outlook.office.com/owa/?itemid=…" }],
  "related": ["2026W33_calendar_focus-block-breached"],
  "thread_id": "project-status",
  "evidence": null
}
```

## Rules for the harvest process

1. **Every item needs a `why`.** No exceptions.
2. **Deterministic ids. Reuse existing slugs, then delete the prior set for every period you
   cover, then write.** This is what stops the dashboard filling with duplicates, and it is
   what makes a loop safe to run several times a day.
3. **One item per finding**, not one per run. The card grid wants many small cards. Target
   8–25 items. Don't paste a whole brief into one `body` — summarize and set `doc`.
4. **Never touch another source's items**, and never delete `inbox/archive/`.
5. **Label inference as inference** in `body`, with the basis in `evidence`.
6. **Run `tools/validate_inbox.py` after writing.** If it reports errors, fix them — the
   backend skips malformed files with only a log warning, so a broken item vanishes silently.
7. **Then run `tools/prune_inbox.py`.** Every loop, every run. Retention is not somebody
   else's job.

## Validation

```bash
python rails/co-worker/tools/validate_inbox.py            # exits non-zero on error
python rails/co-worker/tools/prune_inbox.py --dry-run     # show what would be archived
```

`GET /api/healthz` reports `inbox_items` (valid) alongside `skipped` (malformed), and the
dashboard surfaces a warning when `skipped > 0` — so silent breakage becomes visible.
