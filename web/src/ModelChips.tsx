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
//
// A slot whose `backend` is "claude" is the exception: the viewer's own jobs there run on Claude
// through the broker's CLI backend, so the chip reads "Claude" and the four-state value moves to
// the tooltip as the health of the local fallback. Every string and class comes from
// ./model_chip_view, which is pure so a node test can pin it; this file only lays it out.
import type { ReactNode } from 'react'
import type { ModelChip } from './types'
import { chipDotClass, chipLabel, chipRemote, chipStateText, chipTitle } from './model_chip_view'

// Re-exported so `@web-core/ModelChips` keeps offering what it always did. The types moved to
// ./types and the text table to ./model_chip_view, both so the pure module could reach them.
export type { ModelChip, ModelState } from './types'
export { MODEL_STATE_TEXT } from './model_chip_view'

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
        const remote = chipRemote(m)
        return (
          <span className="rail-chip" key={m.slot} title={chipTitle(m)}>
            <i className={chipDotClass(m)} />
            {chipLabel(m)}{' '}
            {/* The box name is in the chip, not only the tooltip: whether a model is running
                on another machine changes what "ready" is worth, and a hover nobody performs
                is not a disclosure. */}
            {remote && <span className="rail-chip-note">@{remote} · </span>}
            <span className="rail-chip-note">{chipStateText(m)}</span>
          </span>
        )
      })}
      {trailing != null && <span className="rail-chip-note">{trailing}</span>}
    </div>
  )
}

export default ModelChips
