# Harvest protocol

**Every co-work harvest loop follows this file.** It is the shared half of the four loops —
memory, output, idempotency, retention. Each loop's own `SKILL.md` carries only what is
specific to it: what to fetch and how to think about it.

Read this together with `SCHEMA.md`, which is the authoritative field contract.

Paths below are relative to `C:\Users\justin.lowe\ai-platform\`.

---

## Scope — what you write, and what you never write

**You produce data. You do not build the rail.**

| | |
|---|---|
| ✅ Yours | `data\co-worker\` — item JSON, markdown briefs, `CONTEXT.md`, loop working-state (e.g. `inbox\teams\chat-registry.json`) |
| ✅ Yours | This file and `SCHEMA.md` — your operating manual and contract, subject to the naming rule below |
| ❌ Never | `rails\co-worker\frontend\` and `rails\co-worker\backend\` — the rail's source |
| ❌ Never | Anything else under `rails\`, `apps\`, `deploy\`, or `services\` |

The dashboard is built and reviewed on the host, where it can be rendered in a browser and
checked against real items. **You cannot reach the backend or load the page from your
sandbox** — so you cannot verify any UI change you make, and an unverifiable change to a
rendering surface is worse than no change. A previous session wrote a full dashboard this
way; it never rendered once.

If a finding needs something the rail can't express — a new field, an endpoint, a different
display — **say so in your report-back (§6). Propose it; don't implement it.** That is a
faster path to it existing than writing code nobody can run.

### Real names never leave `data\`

**`data\` is gitignored. Everything under `rails\` is committed to a PUBLIC repository.**

That asymmetry is the whole rule. Inside `data\` write real names, accounts, and ticket ids
freely — that is the point of the harvest, and it never leaves the machine. But when you edit
this file or `SCHEMA.md`, **every example must be invented**: no real person, client, account,
ticket id, or internal URL, including inside a filename example or a slug.

This has been violated twice. A worked example was copied verbatim from a live item, and
filename examples used two colleagues' names as slugs — both reached a public repo. Write
`20260811_email_vpn-tunnel-access-request.json`, never a real person's name; `Alex Rivera`,
never a real sender.

## 1. Memory first — non-negotiable

**Read `data\co-worker\CONTEXT.md` before anything else.**

You start every run with no memory. That file is your memory: who people are, what the
accounts and programs are, the classification rules already learned the hard way, and a
**Corrections log** of mistakes previous runs made. Its rules override any assumption you
would otherwise make. Making a logged mistake again is a failure of the run.

If it is missing, say so prominently and proceed with caution — never silently invent context.

**At the end of the run**, append durable discoveries (a new person and their role, a new
client contact, a corrected assumption) to CONTEXT.md. Terse additions only; never rewrite it
wholesale. Never delete the Corrections log.

## 2. Two artifacts, both required

| Artifact | Path | Purpose |
|---|---|---|
| Narrative markdown | `data\co-worker\inbox\<source>\…md` | For Justin to read |
| Item JSON | `data\co-worker\inbox\*.json` — **flat** | Cards on the dashboard |

The backend globs `inbox/*.json` flat and non-recursive. JSON nested in a subfolder is never
found; markdown in a subfolder is correctly ignored. Never put JSON in the subfolder.

Every markdown brief **ends with Method & limits**: what you read in full versus sampled,
what is inference versus assertion, what you could not retrieve, and any partial result your
tools returned. A gap named is more useful than a gap hidden.

Markdown for a given period is overwritten by the next run for that period. Anything that
must survive belongs in CONTEXT.md.

## 3. Writing items — the idempotency sequence

This is the part that was wrong before. Follow it exactly, in order.

**Step 1 — determine the periods this run covers.**

| Source | Period | Format |
|---|---|---|
| `email` | the day each email **arrived** | `20260811` |
| `calendar` | ISO week of the Monday | `2026W33` |
| `teams` | ISO week of the run | `2026W33` |
| `insights` | ISO week of the Monday | `2026W33` |

Usually one period. The email loop covers two whenever its window spans yesterday and today —
and an email keys on the day it *arrived*, never the day you happened to look at it.

**Step 2 — read the existing set and reuse its slugs.**

List `inbox\*.json` for each period you cover. For any finding that is the same finding as
last run, **reuse its exact slug.** Inventing a fresh slug for an unchanged finding is the
easiest way to create a duplicate, and it orphans that item's triage state.

**Step 3 — delete the prior set for every period you cover.**

Delete every `<period>_<source>_*.json` for each covered period. Only your own source, only
periods you cover. Overwriting alone is not enough: findings that have since been resolved
would linger forever as stale cards.

**Step 4 — write this run's complete set.**

Filenames are `<period>_<source>_<slug>.json`. No clock time, no colons, stem limited to
letters, digits, `-` and `_`. `<source>` is the source — `email`, `calendar`, `teams`,
`insights` — **never the type**. `<slug>` describes the finding, not the date
(`conflict-thu-triple-book`, not `conflict-aug-13`).

**The contract this produces: running any loop N times in a row leaves exactly the same files
as running it once.** Every loop is safe to run manually, at any hour, as often as you like.
If a re-run changes the file count for a period it covers, a step above was skipped.

## 4. Item rules

Full field table is in `SCHEMA.md`. The rules that get broken most:

- `schema: 2` and a `period` matching the filename. Both required.
- **Every item needs a one-line `why`.** No exceptions.
- **Every `insight` needs `evidence`** — the measurement, thread or pattern behind the claim.
  An insight without evidence is an opinion.
- A `dangling` item is never priority worse than `3`, from any source.
- Priority: `1` client blocking/time-critical · `2` client, act this week · `3` internal
  action or any dropped commitment · `4` internal informational · `5` noise.
  **Client outranks internal, always.**
- Datetimes carry an explicit Pacific offset correct for that date: `-07:00` in PDT
  (2nd Sun Mar – 1st Sun Nov), `-08:00` in PST. Graph returns UTC — convert.
- `links` = real Graph `webLink`s, copied verbatim. `related` = **co-worker item ids only**,
  never a Graph id. A cross-source ref must resolve, so list `inbox\*.json` and use a real id
  rather than guessing another loop's slug.
- **Re-derive `related` from a fresh inbox listing on every run. Never carry a ref forward
  unverified.** Slug reuse (step 2) tempts you to copy the previous body and refs wholesale,
  but another loop may have correctly retired the item you pointed at — and because you may
  only rewrite your *own* source, nobody else can repair your dangling ref. It would freeze
  into the archive when your period ages out. Re-check every id; drop the ones that no longer
  resolve.
- **Prefer durable targets.** Point `related` at a finding that stays true, not at one whose
  whole purpose is to be resolved. Referencing a "no output produced yet" item, or any
  `dangling`, guarantees a broken edge the moment somebody produces the output. Point at the
  `insight` describing the habit, not at the instance of it.
- **Never delete an item another loop points at.** Slug reuse cuts both ways: if a finding of
  yours is resolved but carries inbound refs, keep the slug and rewrite the body to describe
  the resolution. Renaming a resolved finding creates exactly the stranded ref the rule above
  exists to prevent. Verified 2026-08-12: three stranded refs arose and all three cleared by
  re-derivation alone, so no further contract change is required.
- **One item per finding**, target 8–25 small cards. Don't paste the whole brief into one
  `body` — summarize and point `doc` at the markdown.

**You may only ever write items for your own `source`.** If you spot a problem in another
loop's item, emit a finding describing it — routing it is your job, repairing it is not.

Triage state lives in `inbox\.state.json` and is keyed by item id. Never write or mutate it;
because ids are deterministic, Justin's done/dismissed marks survive your rewrite on their own.

## 5. Close out every run

Both tools are Python and run in the Linux shell, where the repo is bind-mounted under
`/sessions/<session>/mnt/ai-platform`. **The session id changes on every run, so never
hardcode it** — discover the mount instead:

```bash
AI=$(ls -d /sessions/*/mnt/ai-platform 2>/dev/null | head -1)
INBOX="$AI/data/co-worker/inbox"

python3 "$AI/rails/co-worker/tools/validate_inbox.py" --inbox "$INBOX"
python3 "$AI/rails/co-worker/tools/prune_inbox.py"    --inbox "$INBOX"
```

If `$AI` comes back empty the folder is not connected — say so plainly and stop, rather than
reporting a clean run you did not verify.

Note the split: **Read/Write/Edit use the Windows paths** (`C:\Users\justin.lowe\ai-platform\…`)
because they run on the host. **Bash uses the `/sessions/…` mount.** The same file has two
paths depending on which tool you reach for; passing a `C:\` path to Bash silently finds
nothing, and passing a `/sessions/` path to Read fails outright.

**Validate first, and fix every error, re-running until clean.** The backend skips malformed
items with only a log warning, so an invalid item vanishes from the dashboard silently. The
validator is the only thing that makes that visible. Warnings are advisory; errors are not.

**Then prune.** Every loop, every run — it is idempotent and cheap, and it means clutter
cannot accumulate if another loop is paused. It archives items past the dashboard window to
`inbox\archive\`, deletes archived items past their retention window, and expires narrative
briefs on the same retention numbers. Retention lives in one table at the top of
`prune_inbox.py`; change it there and nowhere else.

Your own brief is never at risk: it is dated today and still referenced by the items you just
wrote, and the pruner refuses to delete a brief any surviving item points at.

**The inverse case has no guard, so handle it yourself: a brief that no surviving item points
at is not protected, but it is not deleted early either — it simply waits out
`RETENTION_DAYS` carrying whatever it said. When your run repoints every one of your items at
a new brief, delete the brief they used to point at in the same step. Never read a brief that
no item references as current; check `doc` targets before trusting one.**

Never delete `inbox\archive\` — it is where trend history lives.

## 6. Report back

Close the run by presenting the markdown file, then stating:

- how many JSON items were written, and for which periods
- the validator result and the pruner's archived/expired counts
- anything appended to CONTEXT.md
- **any rail change you'd want** — a field the schema lacks, an endpoint you needed, a way
  these findings would read better on screen. One line each. You know the shape of the data
  better than anyone; you just don't build the surface that shows it.
