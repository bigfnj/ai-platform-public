// Shared design-system primitives. Tokens live in styles.css; these only compose
// classnames so light/dark swap in one place. Icons are inline SVG (no dep).

import type { ButtonHTMLAttributes, ReactNode } from 'react'
import type { Tone } from './types'

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <section className={`card ${className}`}>{children}</section>
}

export function CardHeader({ title, right }: { title: ReactNode; right?: ReactNode }) {
  return (
    <header className="card-head">
      <h2 className="card-title">{title}</h2>
      {right ? <div className="card-head-right">{right}</div> : null}
    </header>
  )
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'default' | 'danger' | 'ghost'
  size?: 'md' | 'sm'
}
export function Button({ variant = 'default', size = 'md', className = '', ...rest }: ButtonProps) {
  return <button className={`btn btn-${variant} ${size === 'sm' ? 'btn-sm' : ''} ${className}`} {...rest} />
}

export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className={`badge badge-${tone}`}>
      <span className="badge-dot" aria-hidden="true" />
      {children}
    </span>
  )
}

export function StatTile({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
    </div>
  )
}

export function Spinner() {
  return <span className="spinner" aria-label="loading" />
}

export function HeartIcon({ filled }: { filled: boolean }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"
      fill={filled ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2">
      <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 1 0-7.8 7.8l1.1 1L12 21l7.7-7.6 1.1-1a5.5 5.5 0 0 0 0-7.8z" />
    </svg>
  )
}

export function FavButton({ on, onClick }: { on: boolean; onClick: (e: React.MouseEvent) => void }) {
  return (
    <button className={`fav ${on ? 'on' : ''}`} onClick={onClick} aria-pressed={on}
      title={on ? 'Remove favorite' : 'Add favorite'}>
      <HeartIcon filled={on} />
    </button>
  )
}

function StarIcon({ lit }: { lit: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"
      fill={lit ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.6">
      <path d="M12 2l2.9 6.3 6.9.7-5.2 4.6 1.5 6.8L12 17.8 5.9 21l1.5-6.8L2.2 9.6l6.9-.7z" />
    </svg>
  )
}

export function Stars({ value, onChange }: { value: number | null; onChange?: (stars: number) => void }) {
  const readonly = !onChange
  return (
    <span className={`stars ${readonly ? 'readonly' : ''}`}>
      {[1, 2, 3, 4, 5].map((n) => (
        <button key={n} className={value && n <= value ? 'lit' : ''}
          onClick={(e) => { e.stopPropagation(); onChange?.(n) }}
          title={`${n} star${n > 1 ? 's' : ''}`}>
          <StarIcon lit={!!value && n <= value} />
        </button>
      ))}
    </span>
  )
}

export function TagChip({
  name, color, onRemove, onClick, active,
}: {
  name: string; color: string; onRemove?: () => void; onClick?: () => void; active?: boolean
}) {
  return (
    <span className={`chip ${active ? 'on' : ''}`} onClick={onClick}>
      <span className={`chip-dot tag-${color}`} />
      {name}
      {onRemove ? (
        <span className="x" onClick={(e) => { e.stopPropagation(); onRemove() }}>×</span>
      ) : null}
    </span>
  )
}
