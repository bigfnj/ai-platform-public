// What a model chip and the widget's Claude section SAY, as pure functions.
//
// Its own module for the reason dictate_policy.ts and speech_lang.ts are: ModelChips.tsx and
// ModelWidget.tsx are .tsx, there is no React harness in this repo, and this file has no
// runtime imports at all, so node's type stripping loads it directly and
// tests/model-chips.test.js can cover it. (`import type` is erased before anything resolves.)
//
// WHAT CHANGED, AND WHY THE TEXT MATTERS. The broker can now answer an OWNER's own jobs on a
// slot through Claude (its CLI backend) instead of the local model. Before that, a chip's state
// told you everything: green meant your next question would be fast. On a Claude slot the
// four-state `state` still describes the LOCAL model, which has become the fallback, so a chip
// that kept rendering it verbatim would say "cold" while every answer the viewer gets comes
// from Claude. Hence: the chip reads "Claude", and the local model's state moves to the tooltip
// as the health of the fallback, which is exactly what it now is.
//
// OLD BROKERS. An entry with no `backend` (or any value other than "claude" and "external")
// renders exactly as it did before this module existed: same label, same state text, same title,
// same dot class. That path is tested, because "everything else unchanged" is the claim most
// likely to rot.
//
// OFF THE BROKER. `backend: "external"` is a slot the rail serves from an endpoint of its own
// configuration instead of the broker (openmaic's OPENMAIC_LLM_BASE_URL). Nobody measured its
// `state`, which is the rail's guess, so the chip says where the slot runs and paints no
// residency colour.
import type { CliRoleOutcome, CliStatus, ModelChip, ModelState, Tone } from './types'

export const MODEL_STATE_TEXT: Record<ModelState, string> = {
  missing: 'not found',
  cold: 'cold',
  warming: 'warming up',
  loaded: 'GPU · ready',
}

/** Strictly "claude". Anything else, including a value a future broker invents, takes the
 *  local path: an unknown backend shown as the local model is stale, an unknown backend shown
 *  as Claude would be a false claim that the viewer's data leaves the box. */
export function isClaudeChip(m: ModelChip): boolean {
  return m.backend === 'claude'
}

/** Strictly "external", for the reason isClaudeChip is strict: a value nobody sends takes the
 *  old local path rather than a claim made up for it. */
function isExternalChip(m: ModelChip): boolean {
  return m.backend === 'external'
}

function isRemote(m: ModelChip): boolean {
  return !!m.upstream && m.upstream !== 'local'
}

/** The bold part of the chip: the slot label, plus the concrete model on a local slot. On a
 *  Claude slot the local model name is left out of the chip, because "LLM (mistral…) Claude"
 *  reads as "mistral is Claude"; the tooltip names it as the fallback instead. */
export function chipLabel(m: ModelChip): string {
  if (isClaudeChip(m)) return m.label
  return `${m.label}${m.model ? ` (${m.model})` : ''}`
}

/** The muted status text after the label. A busy Claude slot says so in WORDS, not only with
 *  the dot's pulse: the first version relied on the pulse alone, and a screenshot taken while
 *  a draft was running showed a chip identical to an idle one. The pulse is also switched off
 *  under prefers-reduced-motion, where the text is then the only signal left. */
export function chipStateText(m: ModelChip): string {
  if (isClaudeChip(m)) return m.busy ? 'Claude · working' : 'Claude'
  if (isExternalChip(m)) return 'external'
  return MODEL_STATE_TEXT[m.state]
}

/** The box name to show inline (`@name · `), or null. A Claude slot shows none: what answers
 *  the viewer is Claude, and where the FALLBACK would run is tooltip detail. An external slot
 *  shows none either: its text already says so, and its `upstream` names no broker. */
export function chipRemote(m: ModelChip): string | null {
  if (isClaudeChip(m)) return null
  if (isExternalChip(m)) return null
  return isRemote(m) ? m.upstream! : null
}

/** The dot's class list. A Claude dot carries `claude` and never a state class: the state is
 *  the fallback's, and painting it would put a red "missing" dot beside the word Claude. `busy`
 *  is honoured only on a Claude slot; on a local slot `warming` already means "a job of yours
 *  is waiting on this model". */
export function chipDotClass(m: ModelChip): string {
  if (isClaudeChip(m)) return `rail-chip-dot claude${m.busy ? ' busy' : ''}`
  if (isExternalChip(m)) return 'rail-chip-dot external'
  return `rail-chip-dot ${m.state}`
}

/** The hover title. */
export function chipTitle(m: ModelChip): string {
  const who = m.role ?? m.label
  if (isClaudeChip(m)) {
    const fallbackState = MODEL_STATE_TEXT[m.state] ?? String(m.state)
    return `${who} → Claude${m.busy ? ' · working now' : ''}`
      + " · your own jobs on this slot run on Claude through the broker's CLI backend"
      + ` · local fallback: ${m.model ?? '?'} (${fallbackState})`
      + (isRemote(m) ? ` on '${m.upstream}'` : '')
  }
  if (isExternalChip(m)) {
    return `${who} → ${m.model ?? '?'} · an endpoint this rail is configured to call instead of`
      + " the platform's broker · whether a model is loaded there is not visible from here"
  }
  // Byte-for-byte what ModelChips.tsx built inline before this module existed.
  return isRemote(m)
    ? `${who} → ${m.model ?? '?'} · ${MODEL_STATE_TEXT[m.state]}`
      + ` · runs on '${m.upstream}', not this box`
      + ' · no "warming up" state off-box: the queue is on the other machine'
    : `${who} → ${m.model ?? '?'} · ${MODEL_STATE_TEXT[m.state]}`
}

// --- the top-bar widget's Claude section ------------------------------------------------------

/** "12s", "3m 05s", "1h 02m". Negative input (the browser's clock behind the broker's) reads
 *  as 0s rather than as a minus sign; a non-number reads as an em dash. */
export function formatElapsed(seconds: number): string {
  if (!Number.isFinite(seconds)) return '—'
  const s = Math.max(0, Math.floor(seconds))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`
  return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`
}

export interface ClaudeRow {
  key: string
  /** The rail's name when the role belongs to one rail, else the role itself. */
  label: string
  /** The role, shown beside the rail name; null when `label` already IS the role. */
  sub: string | null
  elapsed: string
  /** Present only in an admin's status; whose subscription call this is. */
  requester: string | null
  title: string
}

/** One row per in-flight Claude call. Empty when the key is absent (not an owner or admin, or
 *  an older gateway), which is what keeps the whole section off the screen for everyone else. */
export function claudeRows(cli: CliStatus | null | undefined, nowMs: number): ClaudeRow[] {
  if (!cli || !Array.isArray(cli.active)) return []
  return cli.active.map((a, i) => {
    const label = a.rail || a.role
    const elapsed = formatElapsed(nowMs / 1000 - a.since)
    return {
      key: `${a.role}:${a.since}:${i}`,
      label,
      sub: a.rail ? a.role : null,
      elapsed,
      requester: a.requester ?? null,
      // Says what the row is NOT as much as what it is: nobody should look for this call in
      // the job queue, wait for it to free VRAM, or expect a cancel button to exist.
      title: `${a.role} on Claude via the broker's CLI backend for ${elapsed}.`
        + ' Not a GPU job: it holds no queue slot and no VRAM.',
    }
  })
}

export interface ClaudePill {
  text: string
  title: string
}

/** The compact pill on the widget's BUTTON while any Claude call the viewer may see is in
 *  flight, else null. Built from claudeRows, so it inherits the gateway's scoping: an owner's
 *  `active` holds only their own calls, an admin's holds everyone's.
 *
 *  BESIDE the Ollama summary, never instead of it. The two are independent resources: during a
 *  Claude call "Ollama · idle" is still true and still useful (a GPU job submitted now will not
 *  wait), and folding Claude into it is exactly the GPU-busy arithmetic the widget keeps Claude
 *  out of. Before this pill existed the Claude section lived only inside the popover, so the
 *  closed button read "Ollama · idle" for the whole of a running draft. The count is in the
 *  text, not only the title, because a still frame should say how much is running. */
export function claudeTriggerPill(rows: ClaudeRow[]): ClaudePill | null {
  if (rows.length === 0) return null
  const list = rows
    .map((r) => `${r.label}${r.sub ? ` (${r.sub})` : ''}`
      + `${r.requester ? ` for ${r.requester}` : ''}, ${r.elapsed}`)
    .join('; ')
  return {
    text: rows.length === 1 ? 'Claude · working' : `Claude ×${rows.length} · working`,
    title: `Claude is answering ${rows.length} call(s) through the broker's CLI backend: ${list}.`
      + ' Not GPU jobs: they hold no queue slot and no VRAM, so the Ollama status beside this'
      + ' is unaffected. Open the panel for details.',
  }
}

export interface ClaudeWarning {
  text: string
  title: string
}

/** The broker's outcome reason for a streamed answer the CLI cut off after its first token. Not a
 *  fallback: no local answer followed (the rail had shown the text), so the answer the viewer
 *  saw is incomplete. The failure's own slug is beside it, as `cause`. The broker's literal
 *  (services/broker/app/broker_cli.py _cli_interrupted); tests/model-chips.test.js reads it there. */
const CLI_INTERRUPTED = 'interrupted'

/** A warning when any role's LATEST Claude call failed, else null: it fell back to the local
 *  model, or (an `interrupted` outcome) its stream stopped mid-answer.
 *  `last_by_role` keeps only the newest outcome per role, so this clears by itself on that
 *  role's next success; nothing here needs to remember having shown it. Strictly `ok === false`:
 *  a malformed entry with no `ok` is not evidence of a fallback. The reason slug is shown as the
 *  broker logs it (`CLI FALLBACK: ... (rate_limited)`), so the badge and the log grep alike; an
 *  interruption is shown by its cause (`CLI INTERRUPTED: ... (stalled)`), and never as a fallback,
 *  which would say a local answer came instead. With no fallback among the failures, the text is
 *  exactly what it was before interruptions existed. */
export function claudeFallbackWarning(
  cli: CliStatus | null | undefined, nowMs: number,
): ClaudeWarning | null {
  const byRole = cli?.last_by_role
  if (!byRole || typeof byRole !== 'object') return null
  const failed = Object.entries(byRole)
    .filter((e): e is [string, Extract<CliRoleOutcome, { ok: false }>] =>
      !!e[1] && e[1].ok === false)
    .sort((a, b) => (b[1].at ?? 0) - (a[1].at ?? 0))
  if (failed.length === 0) return null
  const cut = (o: Extract<CliRoleOutcome, { ok: false }>) => o.reason === CLI_INTERRUPTED
  const cause = (o: Extract<CliRoleOutcome, { ok: false }>) => o.cause || 'unknown'
  const fellBack = failed.filter(([, o]) => !cut(o))
  const stopped = failed.filter(([, o]) => cut(o))
  const reasons = [...new Set(fellBack.map(([, o]) => o.reason || 'unknown'))]
  const causes = [...new Set(stopped.map(([, o]) => cause(o)))]
  const detail = failed
    .map(([role, o]) => `${role}: ${cut(o) ? `${CLI_INTERRUPTED} (${cause(o)})` : o.reason || 'unknown'}, `
      + `${formatElapsed(nowMs / 1000 - o.at)} ago`)
    .join('; ')
  const parts = [
    ...(reasons.length ? [`fell back (${reasons.join(', ')})`] : []),
    ...(causes.length ? [`stopped mid-answer (${causes.join(', ')})`] : []),
  ]
  const lead = [
    ...(fellBack.length ? ['The latest Claude call ran on the local model instead.'] : []),
    ...(stopped.length ? ['A Claude answer stopped after its first token, and no local answer'
      + ' followed: what was shown is incomplete.'] : []),
  ]
  return {
    text: `Claude ${parts.join('; ')}`,
    title: `${lead.join(' ')} ${detail}.`
      + " Clears on that role's next successful Claude call.",
  }
}

export interface ClaudeReadiness {
  text: string
  tone: Tone
  title: string
}

/** The admin's one-line answer to "I switched it on, why is nothing different?". Null for the
 *  owner shape (no `blockers`), which tells an owner nothing about the install by design.
 *  Three states, not two: a backend an admin switched OFF is a decision, not a fault, and a
 *  warning-toned badge for it would teach everyone to ignore the badge. */
export function claudeReadiness(cli: CliStatus | null | undefined): ClaudeReadiness | null {
  if (!cli || !('blockers' in cli) || !Array.isArray(cli.blockers)) return null
  const roles = Array.isArray(cli.roles) && cli.roles.length ? cli.roles.join(', ') : 'none'
  if (cli.ready) {
    return {
      text: 'Claude: ready',
      tone: 'good',
      title: `Owners' jobs on these roles run on Claude: ${roles}.`
        + ` ${cli.calls ?? 0} call(s) since the broker started.`,
    }
  }
  if (cli.enabled === false) {
    return {
      text: 'Claude: off',
      tone: 'neutral',
      title: 'The broker CLI backend is switched off. Every job runs on the local model.',
    }
  }
  // Every role switched off in Admin > Rails is the same kind of decision as the backend being
  // off, so it reads neutral too; only when it is the ONE thing in the way, or a real fault
  // beside it would be hidden behind a calm badge.
  if (cli.blockers.length === 1 && cli.blockers[0] === 'cli_all_roles_off') {
    return {
      text: 'Claude: off',
      tone: 'neutral',
      title: 'Every role is switched off in Admin > Rails (cli_all_roles_off).'
        + ' Every job runs on the local model.',
    }
  }
  const blockers = cli.blockers.length ? cli.blockers.join(', ') : 'unknown'
  return {
    text: 'Claude: not ready',
    tone: 'warning',
    title: `Not ready: ${blockers}.`
      + (cli.token_problem ? ` Token: ${cli.token_problem}.` : '')
      + " Owners' jobs run on the local model until this clears.",
  }
}
