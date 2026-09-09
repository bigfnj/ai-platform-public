// Minimal markdown renderer, no dependencies.
//
// Card bodies only need bold/code/bullets, but the drill-through modal renders the full
// narrative brief — which is heavy on headings and tables — so those are supported too.
// Deliberately not a general parser: it handles the subset the harvest actually emits and
// degrades to plain text for anything else.
import type { ReactNode } from 'react'

/** Inline: **bold**, `code`, [text](url). */
export function renderInline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = []
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\))/g
  let last = 0
  let m: RegExpExecArray | null
  let n = 0
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const tok = m[0]
    const k = `${keyBase}-${n}`
    if (tok.startsWith('**')) {
      out.push(<strong key={k}>{tok.slice(2, -2)}</strong>)
    } else if (tok.startsWith('`')) {
      out.push(<code key={k}>{tok.slice(1, -1)}</code>)
    } else {
      const mm = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(tok)
      if (mm) {
        out.push(
          <a key={k} href={mm[2]} target="_blank" rel="noreferrer">
            {mm[1]}
          </a>,
        )
      } else {
        out.push(tok)
      }
    }
    last = m.index + tok.length
    n++
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

function isTableDivider(line: string): boolean {
  return /^\|?[\s:|-]+\|[\s:|-]*$/.test(line) && line.includes('-')
}

function splitRow(line: string): string[] {
  return line
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((c) => c.trim())
}

/**
 * Block-level render. Supports #/##/### headings, tables, `- ` bullets, `1. ` ordered
 * lists, > blockquotes, --- rules, and paragraphs.
 */
export function renderMarkdown(src: string): ReactNode[] {
  const lines = src.replace(/\r\n/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let para: string[] = []
  let bullets: string[] = []
  let ordered: string[] = []
  let quote: string[] = []
  let key = 0

  const flushPara = () => {
    if (!para.length) return
    const k = `p${key++}`
    blocks.push(<p key={k}>{renderInline(para.join(' '), k)}</p>)
    para = []
  }
  const flushBullets = () => {
    if (!bullets.length) return
    const k = `u${key++}`
    const items = bullets
    blocks.push(
      <ul key={k}>
        {items.map((b, j) => (
          <li key={j}>{renderInline(b, `${k}-${j}`)}</li>
        ))}
      </ul>,
    )
    bullets = []
  }
  const flushOrdered = () => {
    if (!ordered.length) return
    const k = `o${key++}`
    const items = ordered
    blocks.push(
      <ol key={k}>
        {items.map((b, j) => (
          <li key={j}>{renderInline(b, `${k}-${j}`)}</li>
        ))}
      </ol>,
    )
    ordered = []
  }
  const flushQuote = () => {
    if (!quote.length) return
    const k = `q${key++}`
    const items = quote
    blocks.push(
      <blockquote key={k}>
        {items.map((b, j) => (
          <p key={j}>{renderInline(b, `${k}-${j}`)}</p>
        ))}
      </blockquote>,
    )
    quote = []
  }
  const flushAll = () => {
    flushBullets()
    flushOrdered()
    flushQuote()
    flushPara()
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim()

    if (!line) {
      flushAll()
      continue
    }

    // table: a header row followed by a divider row
    if (line.startsWith('|') && i + 1 < lines.length && isTableDivider(lines[i + 1].trim())) {
      flushAll()
      const head = splitRow(line)
      const rows: string[][] = []
      i += 2
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        rows.push(splitRow(lines[i].trim()))
        i++
      }
      i--
      const k = `t${key++}`
      blocks.push(
        <div className="cw-tablewrap" key={k}>
          <table>
            <thead>
              <tr>
                {head.map((h, j) => (
                  <th key={j}>{renderInline(h, `${k}-h${j}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri}>
                  {r.map((c, ci) => (
                    <td key={ci}>{renderInline(c, `${k}-${ri}-${ci}`)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      continue
    }

    const h = /^(#{1,4})\s+(.*)$/.exec(line)
    if (h) {
      flushAll()
      const depth = h[1].length
      const k = `h${key++}`
      const text = renderInline(h[2], k)
      if (depth <= 1) blocks.push(<h3 key={k}>{text}</h3>)
      else if (depth === 2) blocks.push(<h4 key={k}>{text}</h4>)
      else blocks.push(<h5 key={k}>{text}</h5>)
      continue
    }

    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) {
      flushAll()
      blocks.push(<hr key={`r${key++}`} />)
      continue
    }

    if (line.startsWith('>')) {
      flushBullets()
      flushOrdered()
      flushPara()
      quote.push(line.replace(/^>\s?/, ''))
      continue
    }

    const om = /^\d+[.)]\s+(.*)$/.exec(line)
    if (om) {
      flushBullets()
      flushQuote()
      flushPara()
      ordered.push(om[1])
      continue
    }

    if (/^[-*+]\s+/.test(line)) {
      flushOrdered()
      flushQuote()
      flushPara()
      bullets.push(line.replace(/^[-*+]\s+/, ''))
      continue
    }

    flushBullets()
    flushOrdered()
    flushQuote()
    para.push(line)
  }

  flushAll()
  return blocks
}
