// The edu-suite dashboard as a federated React module (exposed as `edu_suite/module`).
// Tabbed by workflow: each tab explains what to upload, what the job generates, and
// whether Spanish translation is included, then shows that workflow's own new-job form
// and its own jobs. All model work goes through the platform broker via the dashboard's
// /api surface (proxied by the gateway).
//
// Styles are injected as a <style> tag (federation doesn't reliably inject a remote's
// CSS into the host) and use the shell's CSS variables with fallbacks so it themes.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  createJob,
  deleteJob,
  downloadUrl,
  getJob,
  interpret,
  listJobs,
  listWorkflows,
  siteUrl,
  type Interp,
  type Job,
  type Workflow,
} from './api'
import EditUnit from './edit'
import IepForm from './iep_edit'

const CSS = `
.edu { --e-ink: var(--text-primary, #1f2733); --e-muted: var(--text-secondary, #68738a);
  --e-surface: var(--surface-1, #ffffff); --e-border: var(--border, #e2e8f0);
  --e-accent: var(--accent, #4e63d9); color: var(--e-ink); }
.edu .grid { display: grid; gap: 18px; max-width: 960px; }
.edu .card { background: var(--e-surface); border: 1px solid var(--e-border);
  border-radius: 14px; padding: 16px 18px; min-width: 0; }
.edu h3 { margin: 0 0 10px; font-size: 16px; }
.edu label { display: block; font-weight: 650; font-size: 13px; margin: 10px 0 4px; }
.edu input[type=text], .edu select, .edu input[type=file] { font: inherit; width: 100%;
  box-sizing: border-box; padding: 8px 10px; border: 1px solid var(--e-border);
  border-radius: 9px; background: var(--e-surface); color: var(--e-ink); }
.edu .check { display: flex; align-items: center; gap: 8px; font-weight: 500; margin: 8px 0; }
.edu .check input { width: auto; }
.edu button { font: inherit; font-weight: 700; padding: 9px 14px; border: 0;
  border-radius: 9px; background: var(--grad-accent, var(--e-accent)); color: #fff; cursor: pointer; }
.edu button:disabled { opacity: .5; cursor: default; }
.edu button.ghost { background: transparent; color: var(--e-ink); border: 1px solid var(--e-border); font-weight: 600; }
.edu .muted { color: var(--e-muted); font-size: 13px; }
.edu table { width: 100%; border-collapse: collapse; }
.edu th, .edu td { text-align: left; padding: 8px; border-bottom: 1px solid var(--e-border);
  font-size: 14px; }
.edu .badge { font-size: 12px; font-weight: 800; padding: 2px 9px; border-radius: 20px;
  text-transform: capitalize; }
.edu .queued { background: #eef1f6; color: #445; }
.edu .running { background: #fff0bf; color: #7a5b00; }
.edu .done { background: #d7f5e3; color: #1e7a4b; }
.edu .failed { background: #ffd9d9; color: #a12; }
.edu .row { display: flex; gap: 8px; margin-bottom: 10px; }
.edu .row > input { flex: 1; }
.edu .link { color: var(--e-accent); cursor: pointer; text-decoration: none; }
.edu .stages { font-size: 13px; line-height: 1.7; }

/* two-column layout: form on the left, jobs on the right */
.edu .layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 400px);
  gap: 18px; align-items: start; max-width: 1160px; }
.edu .col { display: grid; gap: 18px; align-content: start; min-width: 0; }
@media (max-width: 820px) { .edu .layout { grid-template-columns: 1fr; } }

/* workflow tabs */
.edu .tabs { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }
.edu .tab { font: inherit; font-weight: 650; font-size: 13px; padding: 8px 15px; border-radius: 999px;
  border: 1px solid var(--e-border); background: transparent; color: var(--e-muted); cursor: pointer; }
.edu .tab.active { background: var(--grad-accent, var(--e-accent)); color: #fff; border-color: transparent; }
.edu .tab:hover:not(.active) { color: var(--e-ink); }

/* per-workflow guide */
.edu .lead { margin: 0 0 14px; font-size: 14px; line-height: 1.55; }
.edu .guide-row { display: flex; gap: 12px; margin: 10px 0; font-size: 14px; align-items: baseline; }
.edu .guide-row .k { flex: 0 0 132px; font-weight: 750; color: var(--e-muted); }
.edu .guide-row .v { flex: 1; }
.edu .xl { font-size: 11px; font-weight: 800; padding: 2px 8px; border-radius: 20px;
  text-transform: uppercase; letter-spacing: .3px; margin-right: 6px; white-space: nowrap; }
.edu .xl-always { background: #d7f5e3; color: #1e7a4b; }
.edu .xl-optional { background: #e4e9ff; color: #3a4bb3; }
.edu .xl-none { background: #eef1f6; color: #667; }
.edu .hint { font-size: 12.5px; color: var(--e-muted); margin: 6px 0 0; line-height: 1.5; }

/* folder preview tree (checkable) */
.edu .tree { overflow-x: auto; margin: 0; padding: 10px 12px; border-radius: 10px;
  background: rgba(127,127,127,.08); border: 1px solid var(--e-border); color: var(--e-ink); }
.edu .trow { display: flex; align-items: center; gap: 8px; margin: 0; font-weight: 400;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12.5px; line-height: 1.8; }
.edu .trow input { width: auto; margin: 0; flex: 0 0 auto; cursor: pointer; }
.edu .tprefix { white-space: pre; color: var(--e-muted); }
.edu .tname { white-space: pre; }
.edu .tname.wk { color: var(--e-accent); font-weight: 700; }
.edu .trow.off .tname { text-decoration: line-through; opacity: .5; }
.edu .previewfoot { margin-top: 10px; font-size: 13px; }
.edu .ok { color: #1e7a4b; font-weight: 700; }
.edu .warn { color: #a12; font-weight: 700; }

/* additional instructions + the confirm-before-build panel */
.edu textarea { font: inherit; width: 100%; box-sizing: border-box; padding: 8px 10px;
  border: 1px solid var(--e-border); border-radius: 9px; background: var(--e-surface);
  color: var(--e-ink); min-height: 66px; resize: vertical; }
.edu .confirm { border-color: var(--e-accent); box-shadow: 0 0 0 1px var(--e-accent) inset; }
.edu .understand { font-size: 14px; line-height: 1.5; margin: 0 0 8px; }
.edu .applies { margin: 6px 0; padding-left: 18px; font-size: 13.5px; }
.edu .applies li { margin: 2px 0; }
.edu .cignored { margin: 8px 0; font-size: 13px; line-height: 1.5; }
.edu .cignored .x { color: #a12; font-weight: 700; }
.edu .cq { font-size: 13.5px; font-weight: 600; margin: 10px 0; }
.edu .cbtns { display: flex; gap: 8px; }

/* failed-job error panel */
.edu .errbox { border: 1px solid #f1b0b0; background: #fff2f2; border-radius: 10px;
  padding: 12px 14px; margin-bottom: 12px; }
.edu .errhead { color: #b3261e; font-weight: 800; font-size: 14px; margin-bottom: 6px;
  display: flex; align-items: center; gap: 6px; }
.edu .errbody { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px; line-height: 1.5; color: #7a1c16; white-space: pre-wrap; word-break: break-word; }
`

type Xlate = 'always' | 'optional' | 'none'

interface Guide {
  summary: string
  inputs: string
  generates: string
  xlate: Xlate
  xlateText: string
}

// Onboarding copy per workflow — what to upload, what comes out, and whether the job
// carries all the way through to Spanish translation.
const GUIDE: Record<string, Guide> = {
  just_translate: {
    summary:
      'Turn English documents into clear Mexican Spanish. Use it when you just need a faithful es_MX version of a handout, letter, or any text.',
    inputs: 'One or more documents (PDF, Word, or .txt). Text is extracted automatically.',
    generates:
      'A side-by-side English / Mexican-Spanish (es_MX) HTML page with a 🔊 English and Spanish audio button for every passage, plus a plain-text .es.txt per document — zipped together with the audio files.',
    xlate: 'always',
    xlateText:
      'Translation to Mexican Spanish is the entire purpose here — every document is translated and gets EN/ES audio. No extra option to enable.',
  },
  cvc: {
    summary:
      'Build a printable bilingual phonics worksheet for CVC (consonant-vowel-consonant) reading practice, with a picture and spoken word for every entry.',
    inputs:
      'A document with your CVC words — PDF, Word, .txt, .csv, or .md (a list with one word per line works great, e.g. "cat"). Or leave it empty and check "Use the sample word set".',
    generates:
      'A printable bilingual phonics worksheet — one page per vowel, six words each. Every word gets clip-art (SDXL) plus English and Spanish audio (XTTS).',
    xlate: 'always',
    xlateText:
      'Included automatically — every word is translated to Spanish and gets Spanish audio. Nothing to toggle.',
  },
  teachtown_builder: {
    summary:
      "Create a brand-new interactive unit from your own worksheet PDFs. Drop the unit's master folder (with its Week 1, Week 2… subfolders); the AI drafts the vocabulary and missions, you review and edit, then it builds the site.",
    inputs:
      "The unit's master folder — e.g. \"Great Expectations\" containing any core material plus Week 1, Week 2… subfolders of worksheet PDFs. Pick the folder; it uploads as-is, no zipping.",
    generates:
      'A drafted unit (organized by the weeks in your folder, with top-level material in an Overview section) you can review and edit, then a built self-contained interactive lesson site.',
    xlate: 'optional',
    xlateText:
      'Every unit is built bilingual: English + Mexican Spanish (es_MX) with audio. Tip: "Review the draft before building" lets you edit the AI draft first.',
  },
  iep_present_levels: {
    summary:
      'Turn a SEIS Present-Levels PDF into a fuller English present-levels narrative. Upload it, review the 8 extracted sections, add your notes and data, and the local model elaborates each section to paste into SEIS. English only; nothing leaves the local machine.',
    inputs:
      'One SEIS Present-Levels PDF. The 8 sections are OCR-extracted; then a two-column form shows each extracted section beside a box for your new input.',
    generates:
      'A printable HTML draft of all 8 present-levels sections plus an Areas-of-Need list, elaborated in English — a DRAFT for the IEP team to review and approve. Missing numbers become [bracketed placeholders] to fill in.',
    xlate: 'none',
    xlateText:
      'English only — IEPs are written in English. (A translated parent copy, if ever needed, is a separate pass through Just Translate.)',
  },
  echo: {
    summary:
      'A plumbing test that echoes back your uploaded files. Not a real lesson workflow; it exists to prove the pipeline runs end to end.',
    inputs: 'Any files — this is a plumbing test, not a real lesson workflow.',
    generates: 'A bundle that just lists the files you uploaded. No AI models run.',
    xlate: 'none',
    xlateText: 'Not applicable — this workflow only proves the pipeline works end to end.',
  },
}

const XLATE_LABEL: Record<Xlate, string> = {
  always: 'Always',
  optional: 'Optional',
  none: 'N/A',
}

function badgeClass(s: string): string {
  return ['queued', 'running', 'done', 'failed'].includes(s) ? s : 'queued'
}

// An IEP job is an "extract" job (offers the review form, not a launchable artifact)
// unless its params mark it kind:"generate" (the elaborated narrative). Default to
// extract if params are absent/unparseable.
function iepIsExtract(jb: Job): boolean {
  if (jb.workflow !== 'iep_present_levels') return false
  try {
    return (JSON.parse(jb.params || '{}').kind || 'extract') !== 'generate'
  } catch {
    return true
  }
}

// --- folder-upload preview -------------------------------------------------
// A folder <input webkitdirectory> hands every file a `webkitRelativePath` like
// "Great Expectations/Week 1/reading.pdf". We render that structure back as a
// checkable tree so the teacher can confirm — and deselect — items before the job.
function relPath(f: File): string {
  return (f as unknown as { webkitRelativePath?: string }).webkitRelativePath || f.name
}
function isPdf(f: File): boolean {
  return f.name.toLowerCase().endsWith('.pdf')
}

interface TNode {
  name: string
  dir: boolean
  children: Map<string, TNode>
  path?: string // set on file leaves: the original folder-relative path
}

interface TreeInfo {
  root: TNode | null
  pdfCount: number
  otherCount: number
  master: string | null
}

function buildTree(files: File[]): TreeInfo {
  let root: TNode | null = null
  let pdfCount = 0
  let otherCount = 0
  let master: string | null = null
  for (const f of files) {
    if (!isPdf(f)) {
      otherCount++
      continue // only PDFs become worksheets; skip the rest from the tree
    }
    const orig = relPath(f)
    const parts = orig.split('/').filter(Boolean)
    if (parts.length === 0) continue
    pdfCount++
    if (parts.length > 1 && master === null) master = parts[0]
    if (!root) root = { name: parts.length > 1 ? parts[0] : 'files', dir: true, children: new Map() }
    let node = root
    const rest = parts.length > 1 ? parts.slice(1) : parts // drop master segment
    rest.forEach((seg, i) => {
      const leaf = i === rest.length - 1
      let child = node.children.get(seg)
      if (!child) {
        child = { name: seg, dir: !leaf, children: new Map(), path: leaf ? orig : undefined }
        node.children.set(seg, child)
      }
      node = child
    })
  }
  return { root, pdfCount, otherCount, master }
}

function collectPaths(node: TNode): string[] {
  if (node.path) return [node.path]
  const out: string[] = []
  for (const c of node.children.values()) out.push(...collectPaths(c))
  return out
}

const WEEK_RE = /week\s*0*\d+/i

interface Row {
  id: string
  prefix: string
  name: string
  dir: boolean
  week: boolean
  files: string[] // the file(s) this row's checkbox governs
}

function treeRows(node: TNode, prefix: string, isRoot: boolean, out: Row[]): void {
  if (isRoot) out.push({ id: '__root__', prefix: '', name: `${node.name}/`, dir: true, week: false, files: collectPaths(node) })
  const kids = [...node.children.values()].sort((a, b) => {
    if (a.dir !== b.dir) return a.dir ? -1 : 1 // folders first
    return a.name.localeCompare(b.name, undefined, { numeric: true })
  })
  kids.forEach((k, i) => {
    const last = i === kids.length - 1
    out.push({
      id: k.path ?? `${prefix}|${k.name}`,
      prefix: `${prefix}${last ? '└─ ' : '├─ '}`,
      name: `${k.name}${k.dir ? '/' : ''}`,
      dir: k.dir,
      week: k.dir && WEEK_RE.test(k.name),
      files: k.dir ? collectPaths(k) : [k.path!],
    })
    if (k.dir) treeRows(k, prefix + (last ? '   ' : '│  '), false, out)
  })
}

// Week numbers present among the selected PDFs (from their folder names), passed to
// the interpreter so it can sanity-check instructions like "only translate Week 5".
function detectedWeeks(files: File[]): number[] {
  const s = new Set<number>()
  for (const f of files) {
    if (!isPdf(f)) continue
    const parts = relPath(f).split('/').filter(Boolean)
    for (const seg of parts.slice(0, -1)) {
      const m = seg.match(/week\s*0*(\d+)/i)
      if (m) s.add(parseInt(m[1], 10))
    }
  }
  return [...s].sort((a, b) => a - b)
}

export default function EduSuiteModule() {
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [wf, setWf] = useState('just_translate')
  const [name, setName] = useState('')
  const [sample, setSample] = useState(false)
  const [review, setReview] = useState(false)
  const [picked, setPicked] = useState<File[]>([])
  const [excluded, setExcluded] = useState<Set<string>>(new Set())
  const [instructions, setInstructions] = useState('')
  const [interp, setInterp] = useState<Interp | null>(null)
  const [interpreting, setInterpreting] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const [jobs, setJobs] = useState<Job[]>([])
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState<Job | null>(null)
  const [editing, setEditing] = useState<string | null>(null)

  const isBuilder = wf === 'teachtown_builder'
  // Workflows that accept free-text "Additional instructions" (AI confirms scope first).
  const supportsInstr = ['just_translate', 'cvc', 'teachtown_builder'].includes(wf)

  useEffect(() => {
    const style = document.createElement('style')
    style.textContent = CSS
    document.head.appendChild(style)
    return () => {
      document.head.removeChild(style)
    }
  }, [])

  useEffect(() => {
    listWorkflows()
      .then((ws) => {
        setWorkflows(ws)
        // Land on a workflow that actually exists in THIS instance. The IEP app serves
        // only iep_present_levels (edu-suite hides it), so the hardcoded 'just_translate'
        // default isn't present there — without this, the guide/form show a stale
        // workflow's copy until the user clicks the (only) tab. Keep the current pick if
        // it's valid; otherwise select the first available workflow.
        setWf((cur) => (ws.some((w) => w.key === cur) ? cur : (ws[0]?.key ?? cur)))
      })
      .catch(() => setWorkflows([]))
  }, [])

  // Jobs are scoped to the active workflow tab, so each workflow shows only its own.
  const refreshJobs = useCallback(() => {
    listJobs(query, wf).then(setJobs).catch(() => {})
  }, [query, wf])

  useEffect(() => {
    refreshJobs()
    const id = setInterval(refreshJobs, 3000)
    return () => clearInterval(id)
  }, [refreshJobs])

  useEffect(() => {
    if (!selected) {
      setDetail(null)
      return
    }
    let alive = true
    const tick = () =>
      getJob(selected)
        .then((j) => {
          if (alive) setDetail(j)
        })
        .catch(() => {})
    tick()
    const id = setInterval(tick, 1200)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [selected])

  // Switching tabs resets that workflow's options + selection so nothing leaks across.
  const selectTab = (key: string) => {
    if (key === wf) return
    setWf(key)
    setSample(false)
    setReview(false)
    setPicked([])
    setExcluded(new Set())
    setInstructions('')
    setInterp(null)
    setSelected(null)
    setMsg('')
    if (fileRef.current) fileRef.current.value = ''
  }

  const onPick = () => {
    setPicked(Array.from(fileRef.current?.files ?? []))
    setExcluded(new Set()) // fresh selection starts fully checked
    setInterp(null) // week context changed; re-confirm instructions
  }

  const tree = useMemo(() => (isBuilder ? buildTree(picked) : null), [isBuilder, picked])
  const rows = useMemo(() => {
    const o: Row[] = []
    if (tree?.root) treeRows(tree.root, '', true, o)
    return o
  }, [tree])
  const allPaths = useMemo(() => (tree?.root ? collectPaths(tree.root) : []), [tree])
  const checkedCount = allPaths.filter((p) => !excluded.has(p)).length
  const canStart = !busy && (!isBuilder || checkedCount > 0)

  const start = async () => {
    const fd = new FormData()
    fd.append('workflow', wf)
    // Default the unit name to the picked master folder for the Builder.
    let jobName = name.trim()
    if (isBuilder && !jobName && tree?.master) jobName = tree.master
    fd.append('name', jobName)
    const params: Record<string, unknown> = {}
    // TeachTown Builder is always bilingual now (English + es_MX) with audio.
    if (isBuilder) {
      params.enrich = true
      params.audio = true
    }
    if (sample) params.sample = true
    if (review) params.review = true
    // Only the validated, in-scope guidance from the confirm step reaches the workflow.
    if (supportsInstr && interp?.guidance) params.guidance = interp.guidance
    fd.append('params', JSON.stringify(params))
    // Send the folder-relative path as the filename so the backend can rebuild the
    // structure under input/ (it sanitizes against traversal). For the Builder, only
    // send checked PDFs — unchecked items are excluded from processing and output.
    for (const f of picked) {
      if (isBuilder && (!isPdf(f) || excluded.has(relPath(f)))) continue
      fd.append('files', f, relPath(f))
    }

    setBusy(true)
    setMsg('Starting…')
    try {
      const d = await createJob(fd)
      if (d.error) {
        setMsg(d.error)
      } else {
        setMsg(`Started ${d.id}`)
        setName('')
        setPicked([])
        setExcluded(new Set())
        setInstructions('')
        setInterp(null)
        if (fileRef.current) fileRef.current.value = ''
        refreshJobs()
        if (d.id) setSelected(d.id)
      }
    } catch (e) {
      setMsg(`Error: ${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  // With additional instructions, Start first asks the AI to interpret + confirm; the
  // job only runs once the teacher accepts (or after they edit and re-check).
  const hasInstr = supportsInstr && instructions.trim().length > 0

  const interpretNow = async () => {
    setInterpreting(true)
    setMsg('Checking your instructions…')
    try {
      const r = await interpret({
        workflow: wf,
        instructions: instructions.trim(),
        weeks: isBuilder ? detectedWeeks(picked) : [],
        worksheets: isBuilder ? checkedCount : picked.length,
      })
      if (r.error) setMsg(r.error)
      else {
        setInterp(r)
        setMsg('')
      }
    } catch (e) {
      setMsg(`Error: ${(e as Error).message}`)
    } finally {
      setInterpreting(false)
    }
  }

  const onStart = () => {
    if (hasInstr && !interp) void interpretNow()
    else void start()
  }

  const remove = async (id: string) => {
    if (!confirm('Delete this job and its files?')) return
    await deleteJob(id)
    if (selected === id) setSelected(null)
    refreshJobs()
  }

  if (editing) {
    const editJob = jobs.find((j) => j.id === editing)
    const done = (id: string) => {
      setEditing(null)
      setSelected(id)
      refreshJobs()
    }
    const cancel = () => setEditing(null)
    return (
      <div className="edu">
        {editJob?.workflow === 'iep_present_levels' ? (
          <IepForm jobId={editing} onCancel={cancel} onDone={done} />
        ) : (
          <EditUnit jobId={editing} onCancel={cancel} onDone={done} />
        )}
      </div>
    )
  }

  // Real workflows first; the echo test workflow sinks to the end.
  const orderedWf = [...workflows].sort(
    (a, b) => (a.key === 'echo' ? 1 : 0) - (b.key === 'echo' ? 1 : 0),
  )
  const active = workflows.find((w) => w.key === wf)
  const g = GUIDE[wf]

  return (
    <div className="edu">
      <div className="tabs" role="tablist">
        {orderedWf.map((w) => (
          <button
            key={w.key}
            className={`tab ${w.key === wf ? 'active' : ''}`}
            onClick={() => selectTab(w.key)}
          >
            {w.label}
          </button>
        ))}
      </div>

      <div className="layout">
        <div className="col">
          {g && (
            <div className="card">
              <h3>{active?.label ?? wf}</h3>
              <p className="lead">{g.summary}</p>
              <div className="guide-row">
                <span className="k">Inputs</span>
                <span className="v">{g.inputs}</span>
              </div>
              <div className="guide-row">
                <span className="k">Generates</span>
                <span className="v">{g.generates}</span>
              </div>
              <div className="guide-row">
                <span className="k">Spanish translation</span>
                <span className="v">
                  <span className={`xl xl-${g.xlate}`}>{XLATE_LABEL[g.xlate]}</span>
                  {g.xlateText}
                </span>
              </div>
            </div>
          )}

          <div className="card">
            <h3>New {active?.label ?? ''} job</h3>

            <label>Name (optional)</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={isBuilder ? 'defaults to the folder name' : 'e.g. Grade 7 Food & Water'}
            />

            {isBuilder ? (
              <>
                <label>Unit folder</label>
                <input
                  key="folder"
                  type="file"
                  ref={fileRef}
                  onChange={onPick}
                  multiple
                  {...({ webkitdirectory: '', directory: '' } as Record<string, string>)}
                />
                <p className="hint">
                  Select the unit's master folder (e.g. "Great Expectations") with its
                  Week 1, Week 2… subfolders inside — it uploads with the structure intact,
                  no zipping. Uncheck anything you don't want processed. Files at the top
                  level (not in a Week folder) become an Overview section.
                </p>
              </>
            ) : (
              <>
                <label>Documents</label>
                <input key="files" type="file" ref={fileRef} onChange={onPick} multiple />
              </>
            )}

            {supportsInstr && (
              <>
                <label htmlFor="instr">Additional instructions (optional)</label>
                <textarea
                  id="instr"
                  value={instructions}
                  onChange={(e) => {
                    setInstructions(e.target.value)
                    setInterp(null)
                  }}
                  placeholder="e.g. Use a warm, simple tone and keep sentences short."
                />
                <p className="hint">
                  Tell the AI how to adjust this job. When you start, it confirms what it
                  understood — and flags anything it can't do — before running.
                </p>
              </>
            )}

            {wf === 'cvc' && (
              <div className="check">
                <input
                  id="sample"
                  type="checkbox"
                  checked={sample}
                  onChange={(e) => setSample(e.target.checked)}
                />
                <label htmlFor="sample" style={{ margin: 0 }}>
                  Use the sample word set
                </label>
              </div>
            )}
            {isBuilder && (
              <div className="check">
                <input
                  id="review"
                  type="checkbox"
                  checked={review}
                  onChange={(e) => setReview(e.target.checked)}
                />
                <label htmlFor="review" style={{ margin: 0 }}>
                  Review the draft before building
                </label>
              </div>
            )}
          </div>

          {interp && (
            <div className="card confirm">
              <h3>Check before building</h3>
              <p className="understand">{interp.understanding || interp.question}</p>
              {interp.applies.length > 0 && (
                <ul className="applies">
                  {interp.applies.map((a, i) => (
                    <li key={i}>{a}</li>
                  ))}
                </ul>
              )}
              {interp.ignored.length > 0 && (
                <div className="cignored">
                  {interp.ignored.map((ig, i) => (
                    <div key={i}>
                      <span className="x">Won't do:</span> {ig.text}
                      {ig.reason ? ` — ${ig.reason}` : ''}
                    </div>
                  ))}
                </div>
              )}
              {interp.question && <p className="cq">{interp.question}</p>}
              <div className="cbtns">
                <button onClick={start} disabled={!canStart}>
                  Build it
                </button>
                <button className="ghost" onClick={() => setInterp(null)}>
                  Edit instructions
                </button>
              </div>
            </div>
          )}

          {isBuilder && picked.length > 0 && tree && (
            <div className="card">
              <h3>Preview — check what to include</h3>
              {tree.root ? (
                <div className="tree">
                  {rows.map((r) => {
                    const inc = r.files.filter((p) => !excluded.has(p)).length
                    const all = inc === r.files.length
                    const some = inc > 0 && !all
                    return (
                      <label key={r.id} className={`trow ${inc === 0 ? 'off' : ''}`}>
                        <input
                          type="checkbox"
                          checked={all}
                          ref={(el) => {
                            if (el) el.indeterminate = some
                          }}
                          onChange={() =>
                            setExcluded((prev) => {
                              const next = new Set(prev)
                              if (all) r.files.forEach((p) => next.add(p))
                              else r.files.forEach((p) => next.delete(p))
                              return next
                            })
                          }
                        />
                        <span className="tprefix">{r.prefix}</span>
                        <span className={`tname ${r.week ? 'wk' : ''}`}>{r.name}</span>
                      </label>
                    )
                  })}
                </div>
              ) : (
                <p className="muted">No PDF worksheets found in this folder.</p>
              )}
              <div className="previewfoot">
                {checkedCount > 0 ? (
                  <span className="ok">
                    ✓ {checkedCount} of {tree.pdfCount} worksheet PDF{tree.pdfCount === 1 ? '' : 's'} selected
                  </span>
                ) : (
                  <span className="warn">⚠ Nothing selected — check at least one worksheet to build.</span>
                )}
                {tree.otherCount > 0 && (
                  <span className="muted">
                    {' '}· {tree.otherCount} non-PDF file{tree.otherCount === 1 ? '' : 's'} ignored
                  </span>
                )}
              </div>
            </div>
          )}
          <div style={{ marginTop: 14 }}>
            <button onClick={onStart} disabled={!canStart || interpreting}>
              {interpreting ? 'Checking…' : hasInstr && !interp ? 'Review & start' : 'Start job'}
            </button>{' '}
            <span className="muted">{msg}</span>
          </div>
        </div>

        <div className="col">
          <div className="card">
            <h3>
              Jobs <span className="muted">· {active?.label ?? ''}</span>
            </h3>
            <div className="row">
              <input
                type="text"
                placeholder="search name…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((jb) => (
                  <tr key={jb.id}>
                    <td>
                      <span className="link" onClick={() => setSelected(jb.id)}>
                        {jb.name}
                      </span>
                    </td>
                    <td>
                      <span className={`badge ${badgeClass(jb.status)}`}>
                        {jb.status === 'failed' ? 'error' : jb.status}
                      </span>
                    </td>
                    <td>
                      {jb.workflow === 'teachtown_builder' && (
                        <>
                          <span className="link" onClick={() => setEditing(jb.id)}>
                            edit
                          </span>
                          {' · '}
                        </>
                      )}
                      {iepIsExtract(jb) && jb.status === 'done' && (
                        <>
                          <span className="link" onClick={() => setEditing(jb.id)}>
                            review
                          </span>
                          {' · '}
                        </>
                      )}
                      {jb.status === 'done' && !iepIsExtract(jb) && (
                        <>
                          <a className="link" href={siteUrl(jb.id)} target="_blank" rel="noreferrer">
                            launch
                          </a>
                          {' · '}
                          <a className="link" href={downloadUrl(jb.id)}>
                            download
                          </a>
                          {' · '}
                        </>
                      )}
                      <span className="link" onClick={() => remove(jb.id)}>
                        delete
                      </span>
                    </td>
                  </tr>
                ))}
                {jobs.length === 0 && (
                  <tr>
                    <td className="muted" colSpan={3}>
                      No {active?.label ?? ''} jobs yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {detail && (
            <div className="card">
              <h3>
                {detail.name}{' '}
                <span className={`badge ${badgeClass(detail.status)}`}>
                  {detail.status === 'failed' ? 'error' : detail.status}
                </span>
              </h3>
              {detail.status === 'failed' && (
                <div className="errbox">
                  <div className="errhead">⛔ Error</div>
                  <div className="errbody">
                    {detail.error ||
                      'The job failed. See the stages below for where it stopped.'}
                  </div>
                </div>
              )}
              <div className="stages">
                {(detail.events ?? [])
                  .filter((e) =>
                    ['stage_started', 'stage_finished', 'stage_progress', 'model',
                      'job_failed', 'job_finished'].includes(e.kind))
                  .map((e, i) => {
                    if (e.kind === 'stage_started') return <div key={i}>▶ <b>{e.message || e.stage}</b></div>
                    if (e.kind === 'stage_finished')
                      return (
                        <div key={i}>
                          {e.status === 'done' ? '✓' : '✕'} {e.stage}{' '}
                          <span className="muted">
                            {e.elapsed ? `${e.elapsed.toFixed(1)}s` : ''} {e.message || ''}
                          </span>
                        </div>
                      )
                    if (e.kind === 'model')
                      return <div key={i} className="muted">&nbsp;&nbsp;model {e.status}: {e.model}</div>
                    if (e.kind === 'stage_progress')
                      return <div key={i} className="muted">&nbsp;&nbsp;{e.message}</div>
                    if (e.kind === 'job_failed')
                      return <div key={i} style={{ color: '#c0392b' }}><b>job failed: {e.message}</b></div>
                    return <div key={i} style={{ color: '#1e8e5a' }}><b>job finished</b></div>
                  })}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
