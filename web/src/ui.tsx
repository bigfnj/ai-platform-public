// Shared design-system primitives. Tokens live in styles.css; these only compose
// classnames so light/dark swap in one place. Icons are inline SVG (no dep).

import type { ButtonHTMLAttributes, ReactNode } from 'react'
import type { Tone } from './types'

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'default' | 'danger' | 'ghost'
  size?: 'md' | 'sm'
}
export function Button({ variant = 'default', size = 'md', className = '', ...rest }: ButtonProps) {
  return <button className={`btn btn-${variant} ${size === 'sm' ? 'btn-sm' : ''} ${className}`} {...rest} />
}

export function Badge({ tone = 'neutral', children, title }:
  { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`badge badge-${tone}`} title={title}>
      <span className="badge-dot" aria-hidden="true" />
      {children}
    </span>
  )
}

export function Spinner() {
  return <span className="spinner" aria-label="loading" />
}

