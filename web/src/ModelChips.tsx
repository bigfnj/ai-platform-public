// Shared model-chip row — the four-state status line that sits under a rail's title.
//
// Before this, gemini-cx, smb-partner and co-worker each hand-rolled an identical component
// (same four states, same dot colours, three different class prefixes). One implementation now,
// so a new rail shows model state the same way the others do without copying anything.
//
// The dot is four-state, not on/off, and the distinction that matters operationally is
// red-vs-blue: `missing` needs an `ollama pull`, `cold` just needs someone to ask a question.
// Collapsing both to "off" hides that. The classes live in the GLOBAL @web-core/styles.css, so
// even rails that inject their own <style> render these correctly.
import type { ReactNode } from 'react'

export type ModelState = 'missing' | 'cold' | 'warming' | 'loaded'

export interface ModelChip {
  /** Stable key for the slot (matches the rail's manifest slot id where there is one). */
  slot: string
  /** Human label for the slot, e.g. "LLM" or "Retrieval". */
  label: string
  /** The concrete model the slot resolves to, e.g. "mistral-small3.2:24b". Optional. */
  model?: string
  state: ModelState
  /** The broker role, shown only in the hover title. Optional. */
  role?: string
  /** Which broker runs this slot: 'local' (or absent) for this box, otherwise a registered
   *  upstream's name. A delegated slot is worth marking for two reasons: a green chip then
   *  visibly depends on ANOTHER machine being up, and such a slot has no `warming` state —
   *  warming means "a job is queued on a model that is not resident", and both the queue and
   *  the model are on the other box. Unmarked, that absence reads as a bug. */
  upstream?: string
}

export const MODEL_STATE_TEXT: Record<ModelState, string> = {
  missing: 'not found',
  cold: 'cold',
  warming: 'warming up',
  loaded: 'GPU · ready',
}

export interface ModelChipsProps {
  models?: ModelChip[]
  /** Muted text after the chips, e.g. "307 chunks · 17 collections" or "42 items harvested". */
  trailing?: ReactNode
  /** 'checking' while the first status poll is in flight; 'unreachable' when the broker is down.
   *  Both render a single muted note instead of chips, so the header never blanks. */
  status?: 'ok' | 'checking' | 'unreachable'
}

export function ModelChips({ models, trailing, status = 'ok' }: ModelChipsProps) {
  if (status === 'checking') {
    return <div className="rail-chips"><span className="rail-chip-note">checking…</span></div>
  }
  if (status === 'unreachable') {
    return <div className="rail-chips"><span className="rail-chip-note">● broker unreachable</span></div>
  }
  const chips = models ?? []
  if (chips.length === 0 && trailing == null) return null
  return (
    <div className="rail-chips">
      {chips.map((m) => {
        const remote = !!m.upstream && m.upstream !== 'local'
        const title = remote
          ? `${m.role ?? m.label} → ${m.model ?? '?'} · ${MODEL_STATE_TEXT[m.state]}`
            + ` · runs on '${m.upstream}', not this box`
            + ' · no "warming up" state off-box: the queue is on the other machine'
          : `${m.role ?? m.label} → ${m.model ?? '?'} · ${MODEL_STATE_TEXT[m.state]}`
        return (
          <span className="rail-chip" key={m.slot} title={title}>
            <i className={`rail-chip-dot ${m.state}`} />
            {m.label}{m.model ? ` (${m.model})` : ''}{' '}
            {/* The box name is in the chip, not only the tooltip: whether a model is running
                on another machine changes what "ready" is worth, and a hover nobody performs
                is not a disclosure. */}
            {remote && <span className="rail-chip-note">@{m.upstream} · </span>}
            <span className="rail-chip-note">{MODEL_STATE_TEXT[m.state]}</span>
          </span>
        )
      })}
      {trailing != null && <span className="rail-chip-note">{trailing}</span>}
    </div>
  )
}

export default ModelChips
