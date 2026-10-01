// Minimal markdown renderer for grounded answers.
//
// Deliberately tiny and dependency-free: the model is instructed to emit bold, bullets and
// inline [n] citations, so that is exactly what this supports. A full markdown library would be
// ~40 KB of federated bundle to render four constructs.
//
// Citation markers are turned into visible chips rather than left as literal "[2]", because the
// whole value proposition of this rail is that every claim is traceable — the citation needs to
// look clickable-adjacent, not like punctuation.
import type { ReactNode } from 'react'

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\[\d+\])/g

export function renderInline(text: string, key: string): ReactNode[] {
  return text.split(INLINE).filter(Boolean).map((part, i) => {
    const k = `${key}-${i}`
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={k}>{part.slice(2, -2)}</strong>
    }
    if (part.startsWith('`') && part.endsWith('`')) {
      return <code key={k}>{part.slice(1, -1)}</code>
    }
    if (/^\[\d+\]$/.test(part)) {
      return <span className="gcx-cite-ref" key={k}>{part.slice(1, -1)}</span>
    }
    return <span key={k}>{part}</span>
  })
}

/** Block-level render: paragraphs, bullet lists, ordered lists. */
export function renderMarkdown(text: string): ReactNode {
  const lines = (text || '').split('\n')
  const blocks: ReactNode[] = []
  let para: string[] = []
  let list: { ordered: boolean; items: string[] } | null = null

  const flushPara = () => {
    if (!para.length) return
    const joined = para.join(' ')
    blocks.push(<p key={`p${blocks.length}`}>{renderInline(joined, `p${blocks.length}`)}</p>)
    para = []
  }
  const flushList = () => {
    if (!list) return
    const { ordered, items } = list
    const rendered = items.map((it, i) => (
      <li key={i}>{renderInline(it, `l${blocks.length}-${i}`)}</li>
    ))
    blocks.push(
      ordered
        ? <ol key={`l${blocks.length}`}>{rendered}</ol>
        : <ul key={`l${blocks.length}`}>{rendered}</ul>,
    )
    list = null
  }

  for (const raw of lines) {
    const line = raw.trimEnd()
    if (!line.trim()) {
      flushPara()
      flushList()
      continue
    }
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line)
    const ordered = /^\s*\d+[.)]\s+(.*)$/.exec(line)
    if (bullet || ordered) {
      flushPara()
      const wantOrdered = Boolean(ordered)
      if (!list || list.ordered !== wantOrdered) {
        flushList()
        list = { ordered: wantOrdered, items: [] }
      }
      list.items.push((bullet?.[1] ?? ordered?.[1] ?? '').trim())
      continue
    }
    flushList()
    para.push(line.trim())
  }
  flushPara()
  flushList()
  return <>{blocks}</>
}
