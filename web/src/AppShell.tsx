// The unified platform shell: a left app rail (switch between platform apps), a
// persistent top bar (current app title + the shared model widget + theme), and
// the active app's content. Every app mounts inside this so the suite is one
// product, not several tenants.

import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { AppEntry, Theme } from './types'

const THEME_ORDER: Theme[] = ['light', 'dark', 'system']
const THEME_LABEL: Record<Theme, string> = {
  light: 'Light mode',
  dark: 'Dark mode',
  system: 'System theme',
}

function ThemeIcon({ theme }: { theme: Theme }) {
  const c = { width: 18, height: 18, viewBox: '0 0 24 24', fill: 'none',
    stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const, 'aria-hidden': true }
  if (theme === 'light') {
    return (
      <svg {...c}>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </svg>
    )
  }
  if (theme === 'dark') {
    return (
      <svg {...c}>
        <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
      </svg>
    )
  }
  return (
    <svg {...c}>
      <rect x="3" y="4" width="18" height="12" rx="2" />
      <path d="M8 20h8M12 16v4" />
    </svg>
  )
}

// One icon that cycles Light -> Dark -> System, popping a brief toast on each change.
export function ThemeToggle({ theme, onChange }: { theme: Theme; onChange: (t: Theme) => void }) {
  const [toast, setToast] = useState<string | null>(null)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const cycle = () => {
    const next = THEME_ORDER[(THEME_ORDER.indexOf(theme) + 1) % THEME_ORDER.length]
    onChange(next)
    setToast(THEME_LABEL[next])
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setToast(null), 1800)
  }
  return (
    <>
      <button
        className="theme-btn"
        onClick={cycle}
        title={`Theme: ${THEME_LABEL[theme]} — click to change`}
        aria-label={`Theme: ${THEME_LABEL[theme]}. Click to change.`}
      >
        <ThemeIcon theme={theme} />
      </button>
      {toast !== null ? (
        <div className="theme-toast" role="status">
          <ThemeIcon theme={theme} />
          <span>{toast}</span>
        </div>
      ) : null}
    </>
  )
}

const PALETTE_LABEL: Record<string, string> = {
  indigo: 'Indigo', slate: 'Slate', graphite: 'Graphite', evergreen: 'Evergreen',
  ocean: 'Ocean', ember: 'Ember', plum: 'Plum', rose: 'Rosé', gold: 'Gold & Sand',
  monokai: 'Monokai', solarized: 'Solarized', dracula: 'Dracula', nord: 'Nord',
  tokyo: 'Tokyo Night', onedark: 'One Dark', nightowl: 'Night Owl', gruvbox: 'Gruvbox',
  catppuccin: 'Catppuccin', github: 'GitHub', abyss: 'Abyss', kimbie: 'Kimbie',
}

export interface ThemeMenuProps {
  palette: string
  mode: Theme
  palettes: string[]
  isAdmin: boolean
  hasOverride: boolean
  defaultPalette: string
  defaultMode: Theme
  onPalette: (p: string) => void
  onMode: (m: Theme) => void
  onSetDefault: () => void
  onReset: () => void
}

// A popover: pick a palette (swatch grid) + a mode; admins can save the current
// choice as the platform default; a user with a personal override can reset to it.
export function ThemeMenu(p: ThemeMenuProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  return (
    <div className="tm" ref={ref}>
      <button className="theme-btn" onClick={() => setOpen((o) => !o)} title="Theme" aria-label="Theme">
        <ThemeIcon theme={p.mode} />
      </button>
      {open ? (
        <div className="tm-pop" role="menu">
          <div className="tm-h">Palette</div>
          <div className="tm-swatches">
            {p.palettes.map((name) => (
              <button
                key={name}
                className={`tm-sw ${name === p.palette ? 'on' : ''}`}
                title={PALETTE_LABEL[name] ?? name}
                onClick={() => p.onPalette(name)}
              >
                <span className="tm-swatch" data-pal={name} />
                <span className="tm-sw-l">{PALETTE_LABEL[name] ?? name}</span>
              </button>
            ))}
          </div>
          <div className="tm-h">Mode</div>
          <div className="tm-modes">
            {THEME_ORDER.map((m) => (
              <button
                key={m}
                className={`tm-mode ${m === p.mode ? 'on' : ''}`}
                onClick={() => p.onMode(m)}
              >
                <ThemeIcon theme={m} />
                <span>{THEME_LABEL[m].replace(' mode', '').replace(' theme', '')}</span>
              </button>
            ))}
          </div>
          {(p.isAdmin || p.hasOverride) ? <div className="tm-div" /> : null}
          {p.isAdmin ? (
            <button className="tm-act" onClick={p.onSetDefault}>
              <span>Set as platform default</span>
              <small>now: {PALETTE_LABEL[p.defaultPalette] ?? p.defaultPalette} · {THEME_LABEL[p.defaultMode].replace(' mode', '').replace(' theme', '')}</small>
            </button>
          ) : null}
          {p.hasOverride ? (
            <button className="tm-act" onClick={p.onReset}>
              <span>Reset to platform default</span>
              <small>you have a personal override</small>
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

export function AppShell({
  apps,
  activeAppId,
  onSelectApp,
  title,
  themeMenu,
  topbarExtra,
  children,
  onBrandClick,
  brandActive,
  brandTitle,
  iconOverrides,
}: {
  apps: AppEntry[]
  activeAppId: string
  onSelectApp: (id: string) => void
  title: ReactNode
  themeMenu: ThemeMenuProps
  topbarExtra?: ReactNode
  children: ReactNode
  // When provided, the top brand square becomes a button (used for Admin).
  onBrandClick?: () => void
  brandActive?: boolean
  brandTitle?: string
  // Per-app custom icons (rail launcher), keyed by app id; falls back to the emoji icon.
  iconOverrides?: Record<string, ReactNode>
}) {
  return (
    <div className="pshell">
      <nav className="app-rail" aria-label="Apps">
        {onBrandClick ? (
          <button
            className={`rail-brand rail-brand-btn ${brandActive ? 'on' : ''}`}
            title={brandTitle || 'Admin'}
            aria-label={brandTitle || 'Admin'}
            onClick={onBrandClick}
          >
            <span className="rail-brand-ico" aria-hidden="true">⚙️</span>
          </button>
        ) : (
          <div className="rail-brand" title="Platform" aria-hidden="true" />
        )}
        {apps.map((a) => (
          <button
            key={a.id}
            className={`rail-app ${a.id === activeAppId ? 'on' : ''}`}
            disabled={a.status !== 'ready'}
            onClick={() => onSelectApp(a.id)}
            title={a.status === 'ready' ? a.label : `${a.label} (coming soon)`}
          >
            <span className="rail-ico">{iconOverrides?.[a.id] ?? a.icon}</span>
            {a.label}
          </button>
        ))}
      </nav>
      <header className="ptopbar">
        <span className="app-title">{title}</span>
        <span className="spacer" />
        {topbarExtra}
        <ThemeMenu {...themeMenu} />
      </header>
      <main className="pcontent">{children}</main>
    </div>
  )
}
