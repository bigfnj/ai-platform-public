// The one rail header, shared by every rail.
//
// The platform settled on a single shape for the top of every rail: an icon, a bold title, a
// one-line muted subtext, the model chips, and a rule closing it off. It used to be copied per
// rail with per-rail class prefixes (and several rails skipped half of it). This component is the
// source of truth for that shape; RAIL_CONTRACT.md requires every rail to use it, and
// tools/rail_conformance.py checks that a rail's module imports it.
//
// The chips row is passed in rather than built here, because which models a rail has — and how it
// learns their state — is rail-specific; render a <ModelChips/> into it. `actions` is the escape
// hatch for header-row controls (a tab bar, a device picker, a refresh button): they sit to the
// right of the titles, above the rule, so the icon/title/subtext/chips column stays identical
// across rails no matter what a rail hangs off the side.
import type { ReactNode } from 'react'

export interface RailHeaderProps {
  /** Emoji string or an SVG/element. Rendered at the left, vertically centred with the title. */
  icon: ReactNode
  title: string
  /** One or two sentences. Omit only if the rail genuinely has nothing to say. */
  subtitle?: ReactNode
  /** The model-chips row — render a <ModelChips/>. Omit only on a rail with no model at all. */
  chips?: ReactNode
  /** Right-aligned header controls (tabs, pickers, buttons). Kept out of the title column. */
  actions?: ReactNode
  /** Extra class on the <header>, for the rare rail that needs to tweak spacing. */
  className?: string
}

export function RailHeader({ icon, title, subtitle, chips, actions, className }: RailHeaderProps) {
  return (
    <header className={`rail-head${className ? ` ${className}` : ''}`}>
      <span className="rail-head-icon" aria-hidden="true">{icon}</span>
      <div className="rail-head-titles">
        <h1 className="rail-head-title">{title}</h1>
        {subtitle != null && subtitle !== '' && <p className="rail-head-sub">{subtitle}</p>}
        {chips}
      </div>
      {actions != null && <div className="rail-head-actions">{actions}</div>}
    </header>
  )
}

export default RailHeader
