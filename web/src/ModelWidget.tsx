// Top-bar GPU/model control, shared by every app. Presentational: the shell polls
// platform status and passes it in. The compact bar shows Ollama status + a VRAM
// meter; clicking it opens a popover with the models currently LOADED (each with an
// unload ✗) and the live JOB QUEUE — the active GPU job plus anything waiting behind
// it, so users can see how many jobs are ahead. Data comes from the broker's
// /api/platform/status (loaded + GPU + jobs).

import { useEffect, useRef, useState } from 'react'
import type { PlatformStatus } from './types'
import { Badge, Spinner } from './ui'

function gibFromMib(mib: number): string {
  return (mib / 1024).toFixed(1)
}
function gibFromBytes(bytes: number | null): string {
  return bytes ? `${(bytes / 1073741824).toFixed(1)} GB` : ''
}

// A short "what is this model good at" blurb, shown on the info icon's hover.
// Keyed by family so version bumps still match; unknown models get a safe fallback.
// (This list is Ollama text/embedding models only — voice/image run in the broker.)
function describeModel(name: string, cls: 'heavy' | 'embed'): string {
  const n = name.toLowerCase()
  if (cls === 'embed' || n.includes('embed') || n.includes('bge'))
    return 'Embeddings — turns text into vectors for semantic search & similarity. Not a chat model.'
  if (n.includes('coder') || n.includes('code'))
    return 'Code specialist — best for programming, code generation, and refactoring.'
  if (n.includes('deepseek') || n.includes('-r1'))
    return 'Reasoning model — thinks step by step; strong on hard multi-step problems (slower).'
  if (n.startsWith('mistral'))
    return "General-purpose chat & instructions — the suite's default for translation and lesson drafting."
  if (n.startsWith('gemma'))
    return 'General-purpose chat (Google Gemma) — bigger sizes are more capable, smaller ones faster.'
  if (n.startsWith('llama3.2'))
    return 'Small, fast general-purpose chat — low VRAM, quick responses.'
  if (n.startsWith('llama'))
    return 'General-purpose chat — a solid all-round model.'
  if (n.startsWith('qwen'))
    return 'General-purpose chat & reasoning (Qwen); larger sizes handle tougher tasks.'
  if (n.startsWith('gpt-oss'))
    return 'General-purpose open chat model.'
  return 'General-purpose language model.'
}

export function ModelWidget({
  status,
  busy,
  isAdmin,
  onUnload,
  onCancel,
}: {
  status: PlatformStatus | null
  busy: boolean
  isAdmin: boolean
  onUnload: (model: string) => void
  onCancel: (seq: number) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  if (!status || !status.broker_reachable) {
    return <Badge tone="critical">Broker offline</Badge>
  }

  const loaded = status.loaded ?? []
  const jobs = status.jobs ?? []

  const gpu = status.gpu
  const pct = gpu ? Math.round((gpu.used_mib / gpu.total_mib) * 100) : 0
  const sev = pct >= 90 ? 'critical' : pct >= 70 ? 'warning' : ''
  const heavyResident = status.heavy_loaded[0] ?? null
  // Belt & suspenders: the Ollama /ps `loaded` list never includes the torch media worker
  // (SDXL/XTTS), so image/audio jobs looked "idle". Cross-check two more signals: the broker's
  // queue (a heavy op — chat OR media — is running/queued) and VRAM well above the idle baseline.
  const q = status.queue ?? { active: 0, waiting: 0 }
  const gpuBusy = (q.active ?? 0) > 0 || (q.waiting ?? 0) > 0
  const vramBusy = gpu ? gpu.used_mib > Math.max(6000, gpu.total_mib * 0.25) : false
  const summary = gpuBusy
    ? heavyResident
      ? `${heavyResident} · working…`
      : 'working…'
    : loaded.length
      ? heavyResident ?? `${loaded.length} loaded`
      : vramBusy
        ? 'in use'
        : 'idle'

  return (
    <div className="mw" ref={ref}>
      <button className="mw-trigger" onClick={() => setOpen((o) => !o)} aria-expanded={open} title="Models">
        <Badge tone={status.ollama_reachable ? 'good' : 'critical'}>Ollama</Badge>
        <span className="mw-summary mono">{summary}</span>
        <span className="mw-caret">▾</span>
      </button>

      {gpu ? (
        <span className="mw-vram">
          <div className="mw-track">
            <div className={`mw-fill ${sev}`} style={{ width: `${pct}%` }} />
          </div>
          <span className="mw-read">{gibFromMib(gpu.used_mib)}/{gibFromMib(gpu.total_mib)} GB</span>
        </span>
      ) : null}

      {open && (
        <div className="mw-pop" role="dialog" aria-label="Models">
          <div className="mw-pop-head">
            <span>{gpu?.gpu_name ?? 'GPU'}</span>
            {gpu ? <span className="mw-read">{gibFromMib(gpu.used_mib)}/{gibFromMib(gpu.total_mib)} GB</span> : null}
          </div>

          <div className="mw-pop-sec">
            <div className="mw-pop-h">Loaded</div>
            {loaded.length ? (
              loaded.map((m) => (
                <div className="mw-pop-row" key={m.name}>
                  <span className="mw-i" title={describeModel(m.name, m.class)} aria-label="About this model">i</span>
                  <span className="nm mono">{m.name}</span>
                  <Badge tone={m.class === 'heavy' ? 'accent' : 'neutral'}>{m.class}</Badge>
                  <span className="sz">{gibFromBytes(m.size_vram)}</span>
                  {isAdmin && (
                    <button
                      className="mw-x"
                      title={`Unload ${m.name}`}
                      disabled={busy}
                      onClick={() => onUnload(m.name)}
                    >
                      {busy ? <Spinner /> : '✕'}
                    </button>
                  )}
                </div>
              ))
            ) : (
              <div className="mw-empty">Nothing loaded — the GPU is idle.</div>
            )}
          </div>

          <div className="mw-pop-sec">
            <div className="mw-pop-h">Job queue{jobs.length ? ` (${jobs.length})` : ''}</div>
            {jobs.length ? (
              jobs.map((j, i) => (
                <div className="mw-pop-row mw-job" key={j.seq}>
                  <span className={`mw-job-dot ${j.state}`} aria-hidden="true">{j.state === 'active' ? '▶' : '•'}</span>
                  <span className="nm mw-job-rail">{j.source ?? 'Platform'}</span>
                  <span className="mono mw-job-model" title={j.model ?? ''}>{j.model ?? '—'}</span>
                  <Badge tone={j.state === 'active' ? 'good' : 'neutral'}>
                    {j.state === 'active' ? 'running' : i === 1 ? 'next' : `${i} ahead`}
                  </Badge>
                  {isAdmin && (
                    <button className="mw-x" title="Cancel this job" disabled={busy} onClick={() => onCancel(j.seq)}>
                      {busy ? <Spinner /> : '✕'}
                    </button>
                  )}
                </div>
              ))
            ) : (
              <div className="mw-empty">No jobs queued — the GPU is idle.</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}