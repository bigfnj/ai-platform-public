# Ingest contract

How an external task supplies transcripts and summaries to Meeting Atlas.

The rail does **no inference**. It indexes what it finds on disk and serves it. Anything
better than Meetily's own raw output — a frontier-model re-transcription, speaker labels,
a summary whose citations survive checking — arrives as **sidecar files** written next to
the recording.

Same writer/reader split as the `co-worker` rail: a Claude co-work task running on the
Windows host writes the files; this backend reads them.

---

## Where files go

One directory per meeting, under the recordings root
(`C:\Users\<you>\Music\meetily-recordings` on the host, `/data/recordings` in the container):

```
Meeting 2026-08-20_06-59-29_2026-08-20_13-59/
  transcripts.json              # Meetily writes this
  metadata.json                 # Meetily writes this
  audio.mp4                     # Meetily writes this
  transcript.enriched.json      # ← YOU write this  (optional)
  summary.json                  # ← YOU write this  (optional)
```

Write the sidecars into the **existing** meeting folder. Do not create new folders, rename
anything, or modify Meetily's three files — the rail treats them as the source of record
and Meetily itself may still be holding them.

### Precedence

| Field | Winner | Falls back to |
|---|---|---|
| transcript | `transcript.enriched.json` | `transcripts.json` (Meetily) |
| summary | `summary.json` | the Meetily SQLite, if mounted |
| title | `summary.json` → `title` | the Meetily SQLite, then the folder's auto-name |

The rail surfaces which one it used, so a summary is never silently attributed to the wrong
model.

---

## `transcript.enriched.json`

```json
{
  "model": "claude-opus-5",
  "source": "co-work re-transcription",
  "generated_at": "2026-08-20T09:14:03-07:00",
  "segments": [
    { "start": 0.0,   "duration": 2.44, "text": "Morning everyone, let's get started.", "speaker": "Ada" },
    { "start": 24.54, "duration": 1.09, "text": "Sounds good.",                         "speaker": "Grace" }
  ]
}
```

**`segments` is the only required key.** Per segment:

| Key | Required | Notes |
|---|---|---|
| `start` | yes | seconds from the start of the audio, float |
| `duration` | no | seconds. Omit it and supply `end` instead; the rail derives one from the other |
| `end` | no | alternative to `duration` |
| `text` | yes | non-empty after trimming, or the segment is dropped |
| `speaker` | no | **the reason this file is worth writing** |

### Speakers are the headline feature here

Meetily produces **no diarization at all** — the `speaker` column in its database is NULL
for every row. So per-speaker talk time is impossible from raw Meetily data, and the rail
deliberately does not fake it from silence gaps.

Supply `speaker` and a "Who talked" panel appears on the meeting page: share of speaking
time, words and turns per person, plus a table view. Leave it out and nothing breaks — the
panel simply does not render.

Use a **stable, human-readable** label (`"Ada"`, not `"SPEAKER_01"`). It is displayed
verbatim beside every line.

---

## `summary.json`

```json
{
  "title": "Q3 Platform Migration Review",
  "model": "claude-opus-5",
  "provider": "anthropic",
  "generated_at": "2026-08-20T09:15:22-07:00",
  "elapsed_s": 41.2,
  "markdown": "# Q3 Platform Migration Review\n\n**Summary**\n\n..."
}
```

`markdown` is the only required key. `title` is worth supplying — without it, and without
the Meetily database mounted, the meeting shows its auto-generated folder name.

### The markdown shape the parser understands

Section headings may be `**Bold**` or `## Heading`. Recognised names (case-insensitive):

| Section | Aliases | Parsed into |
|---|---|---|
| `Summary` | Overview, Meeting Summary | one paragraph block |
| `Key Decisions` | Decisions | bullet list |
| `Action Items` | Actions, Next Steps | a markdown table (below) |
| `Discussion Highlights` | Highlights, Key Points, Discussion Points | `**Topic:** body` bullets |

Anything unrecognised still reaches the UI under "Raw model output", so an unexpected
section is never lost — just not structured. Parsing is an enhancement, never a gate.

### The action-item table — and why the citation format matters

```markdown
**Action Items**

| Owner | Task | Due | Reference Transcript Segment | Segment Time stamp |
|---|---|---|---|---|
| Ada | Add milestones to the migration timeline. | TBD | [03:22]"let's walk the updated timeline" | 03:22 |
```

Columns are matched by header keyword (`owner`/`who`/`assign`, `task`/`action`/`item`,
`due`/`date`/`deadline`, `ref`/`segment`/`transcript`/`quote`, `time`/`stamp`), so the exact
header wording is flexible.

**The `Reference` cell is the important one.** Put a `[MM:SS]` timestamp and a short
**verbatim** quote in it. The rail then locates that quote in the real transcript and
reports what it found:

| Badge | Meaning |
|---|---|
| `verified 3:22` | the quote was found there. Click it to jump to the line |
| `cited 25:28 · found 26:03` | found, but >20s from the timestamp you claimed |
| `quote not in transcript` | the quote appears nowhere — the citation is fabricated |
| `quote reused` | the same quote backs two or more different items |
| `due date implausible` | the due date is in the past or >120 days out |
| *(one per meeting)* | every item carries the same due date |

This is not decoration. On a real 27-minute meeting, a local 4B model dated all four
action items to one invented day nearly three months out, and cited a single throwaway
line as the evidence for three unrelated tasks — a line that occurs 35 seconds away
from the timestamp it claimed.

So: **write `TBD` unless a date was actually spoken aloud**, and quote text that really
exists. A frontier model should clear every one of these checks; if it does not, the
badges will say so, which is the point.

---

## Telling the rail to re-read

```bash
curl -X POST http://localhost:1111/meeting-atlas/api/reindex
```

Returns what it found:

```json
{ "ok": true, "n_meetings": 12, "n_summarised": 12, "n_enriched": 9,
  "n_flagged": 0, "seconds": 0.31, "indexed_at": "2026-08-21T07:57:38" }
```

Call it once after writing a batch of sidecars. There is also a fallback: the rail re-reads
on demand when the mount's newest folder mtime changes, at most every
`MEETING_ATLAS_AUTOREINDEX_SECONDS` (default 300). Set that to `0` once your task calls the
hook itself.

Indexing is cheap — 255 segments took 18 ms — so calling it more often than necessary costs
nothing.

---

## Two constraints worth knowing

**Write atomically, on the host.** Your task runs on Windows against a normal directory, so
ordinary atomic-write patterns are fine there. But note the rail's side: the recordings
directory reaches the container as a **9p** bind mount through Podman/Hyper-V, and 9p
rejects rename-over-an-existing-file. That is why this backend is mounted **read-only** and
holds its index in memory — it has no write path to get wrong. Don't add one.

**`display_time` in Meetily's `transcripts.json` is UTC**, while the folder name's leading
timestamp is local. Neither is used: `metadata.json`'s `created_at` is the one canonical
instant, converted once to `MEETING_ATLAS_DISPLAY_TZ`. If you write an enriched transcript,
`start` is **seconds from the beginning of the audio** — not a wall clock — so there is no
timezone question on your side at all.
