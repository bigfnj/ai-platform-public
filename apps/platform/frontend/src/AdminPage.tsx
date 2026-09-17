// Admin console. Three categories (tabs):
//   Rails  — the LLM each rail loads for a task; per-rail model roles, repointed live.
//   Models — the workstation model pool: every installed model, its In-Use/Loaded state, and
//            Enable/Disable (reversible) + Delete (irreversible, blocked while In-Use).
//   Users  — manage users, their role, and which apps each may see (delegation-aware).
// Admin-only; the gateway's /api/platform/admin/* endpoints enforce the same rules server-side.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Badge, Button, platformApi } from '@web-core'
import type { AdminUser, AppEntry, MediaOption, ModelCategory, ModelOption, ModelPoolEntry, RailManagerEntry, RailModelSlot, RailModels, RailSchedules, RailsSettings, Recurrence } from '@web-core'

type Tab = 'users' | 'rails' | 'manager' | 'models' | 'schedule'

export default function AdminPage({ meUsername }: { meUsername: string }) {
  const [tab, setTab] = useState<Tab>('rails')
  return (
    <div className="module">
      <div className="admin-tabs" role="tablist" aria-label="Admin sections">
        <button className={`admin-tab ${tab === 'rails' ? 'on' : ''}`} role="tab"
                aria-selected={tab === 'rails'} onClick={() => setTab('rails')}>Rails</button>
        <button className={`admin-tab ${tab === 'manager' ? 'on' : ''}`} role="tab"
                aria-selected={tab === 'manager'} onClick={() => setTab('manager')}>Rail Manager</button>
        <button className={`admin-tab ${tab === 'models' ? 'on' : ''}`} role="tab"
                aria-selected={tab === 'models'} onClick={() => setTab('models')}>Models</button>
        <button className={`admin-tab ${tab === 'schedule' ? 'on' : ''}`} role="tab"
                aria-selected={tab === 'schedule'} onClick={() => setTab('schedule')}>Schedule</button>
        <button className={`admin-tab ${tab === 'users' ? 'on' : ''}`} role="tab"
                aria-selected={tab === 'users'} onClick={() => setTab('users')}>Users</button>
      </div>
      {tab === 'rails' ? <RailsTab />
        : tab === 'manager' ? <RailManagerTab />
        : tab === 'models' ? <ModelsTab />
        : tab === 'schedule' ? <ScheduleTab />
        : <UsersTab meUsername={meUsername} />}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Schedule tab: the central scheduler. Per-rail maintenance tasks, each with an
// Outlook-style recurrence editor (Daily / Weekly / Monthly, minus Duration).
// ---------------------------------------------------------------------------

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] // index = Mon=0..Sun=6

function fmtWhen(iso: string | null): string {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) }
  catch { return iso }
}

function RecurrenceEditor({ rec, onChange }: { rec: Recurrence; onChange: (patch: Partial<Recurrence>) => void }) {
  const unit = rec.freq === 'daily' ? 'day(s)' : rec.freq === 'weekly' ? 'week(s)' : 'month(s)'
  const days = new Set(rec.byweekday ?? [])
  const toggleDay = (i: number) => {
    const next = new Set(days)
    next.has(i) ? next.delete(i) : next.add(i)
    onChange({ byweekday: Array.from(next).sort((a, b) => a - b) })
  }
  const lastDay = rec.bymonthday === -1
  return (
    <div className="sch-rec">
      <div className="sch-freq">
        {(['daily', 'weekly', 'monthly'] as const).map((f) => (
          <label key={f} className={rec.freq === f ? 'on' : ''}>
            <input type="radio" name={`freq-${Math.random()}`} checked={rec.freq === f} onChange={() => onChange({ freq: f })} />
            {f[0].toUpperCase() + f.slice(1)}
          </label>
        ))}
      </div>
      <div className="sch-row">
        <span>Recur every</span>
        <input type="number" min={1} max={52} value={rec.interval}
               onChange={(e) => onChange({ interval: Math.max(1, Number(e.target.value) || 1) })} />
        <span>{unit}</span>
        <span className="sch-at">at</span>
        <input type="time" value={rec.at} onChange={(e) => onChange({ at: e.target.value })} />
      </div>
      {rec.freq === 'weekly' && (
        <div className="sch-days">
          {WEEKDAYS.map((d, i) => (
            <button key={d} type="button" className={days.has(i) ? 'on' : ''} onClick={() => toggleDay(i)}>{d}</button>
          ))}
        </div>
      )}
      {rec.freq === 'monthly' && (
        <div className="sch-row">
          <span>On day</span>
          <input type="number" min={1} max={31} disabled={lastDay}
                 value={lastDay ? '' : (rec.bymonthday ?? 1)}
                 onChange={(e) => onChange({ bymonthday: Math.min(31, Math.max(1, Number(e.target.value) || 1)) })} />
          <label className={lastDay ? 'on' : ''} style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
            <input type="checkbox" checked={lastDay} onChange={(e) => onChange({ bymonthday: e.target.checked ? -1 : 1 })} />
            last day of month
          </label>
        </div>
      )}
      <div className="sch-row">
        <span>Time zone</span>
        <input type="text" className="sch-tz" value={rec.tz} onChange={(e) => onChange({ tz: e.target.value })} placeholder="America/Los_Angeles" />
      </div>
    </div>
  )
}

function ScheduleTab() {
  const [rails, setRails] = useState<RailSchedules[]>([])
  const [draft, setDraft] = useState<Record<string, { recurrence: Recurrence; enabled: boolean }>>({})
  const [err, setErr] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  const apply = useCallback((view: { rails: RailSchedules[] }) => {
    setRails(view.rails)
    const d: Record<string, { recurrence: Recurrence; enabled: boolean }> = {}
    for (const r of view.rails) for (const t of r.tasks) d[`${r.rail}/${t.task_id}`] = { recurrence: { ...t.recurrence }, enabled: t.enabled }
    setDraft(d)
  }, [])
  const load = useCallback(async () => {
    try { apply(await platformApi.adminSchedules()); setErr('') }
    catch (ex) { setErr((ex as Error).message) } finally { setLoaded(true) }
  }, [apply])
  useEffect(() => { load() }, [load])

  const key = (rail: string, tid: string) => `${rail}/${tid}`
  const patch = (k: string, p: Partial<Recurrence>) => setDraft((d) => ({ ...d, [k]: { ...d[k], recurrence: { ...d[k].recurrence, ...p } } }))
  const setEnabled = (k: string, en: boolean) => setDraft((d) => ({ ...d, [k]: { ...d[k], enabled: en } }))

  const save = async (rail: string, tid: string) => {
    const k = key(rail, tid); setBusy(k); setErr('')
    try { apply(await platformApi.adminSetSchedule(rail, tid, draft[k])); setSaved(k); setTimeout(() => setSaved((s) => (s === k ? null : s)), 2200) }
    catch (ex) { setErr((ex as Error).message) } finally { setBusy(null) }
  }
  const runNow = async (rail: string, tid: string) => {
    const k = key(rail, tid); setBusy(k + ':run'); setErr('')
    try { await platformApi.adminRunSchedule(rail, tid); await load() }
    catch (ex) { setErr((ex as Error).message) } finally { setBusy(null) }
  }

  if (!loaded) return <div className="card"><div className="empty">Loading…</div></div>

  return (
    <>
      {err && <div className="card" style={{ marginBottom: 16 }}><p className="error-line" style={{ margin: 0 }}>{err}</p></div>}
      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Scheduled tasks</h3>
        <p className="muted" style={{ margin: 0 }}>
          Recurring maintenance the platform runs for each rail. Edit the recurrence (like Outlook,
          minus duration), enable/disable, or run one now. The gateway fires each task on its cadence.
        </p>
      </div>

      {rails.length === 0 && <div className="card"><div className="empty">No scheduled tasks for the installed rails.</div></div>}

      {rails.map((rail) => (
        <div className="card" key={rail.rail} style={{ marginBottom: 16 }}>
          <h3 className="rail-title"><span aria-hidden="true">{rail.icon}</span> {rail.rail}</h3>
          {rail.tasks.map((t) => {
            const k = key(rail.rail, t.task_id)
            const d = draft[k]
            if (!d) return null
            const b = busy === k
            return (
              <div className="sch-task" key={t.task_id}>
                <div className="sch-task-head">
                  <label className="sch-enable">
                    <input type="checkbox" checked={d.enabled} onChange={(e) => setEnabled(k, e.target.checked)} />
                    <span className="sch-task-label">{t.label}</span>
                  </label>
                  {t.last_status && <Badge tone={/^(ok|triggered)/.test(t.last_status) ? 'good' : 'critical'}>{t.last_status}</Badge>}
                </div>
                <p className="sch-task-desc">{t.description}</p>
                <RecurrenceEditor rec={d.recurrence} onChange={(p) => patch(k, p)} />
                <div className="sch-task-foot">
                  <span className="muted">Next: <b>{fmtWhen(t.next_run)}</b> · Last: {fmtWhen(t.last_run)}</span>
                  <span className="sch-actions">
                    {saved === k && <span className="rail-slot-saved">✓ saved</span>}
                    <Button size="sm" variant="ghost" disabled={b} onClick={() => runNow(rail.rail, t.task_id)}>Run now</Button>
                    <Button size="sm" disabled={b} onClick={() => save(rail.rail, t.task_id)}>{b ? 'Saving…' : 'Apply'}</Button>
                  </span>
                </div>
              </div>
            )
          })}
        </div>
      ))}
    </>
  )
}

// ---------------------------------------------------------------------------
// Models tab: the workstation model pool + lifecycle (enable / disable / delete).
// ---------------------------------------------------------------------------

// --- Rail Manager ---------------------------------------------------------------
// What each rail IS, and whether it is switched on. Distinct from the Rails tab, which repoints
// model roles for rails you already run; this one is about which rails run at all.
function RailManagerTab() {
  const [rails, setRails] = useState<RailManagerEntry[]>([])
  const [err, setErr] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(async () => {
    try { setRails((await platformApi.adminRailManager()).rails); setErr('') }
    catch (ex) { setErr((ex as Error).message) } finally { setLoaded(true) }
  }, [])
  useEffect(() => { void load() }, [load])

  const toggle = async (r: RailManagerEntry) => {
    setBusy(r.id); setErr('')
    try { await platformApi.adminRailToggle(r.id, r.state !== 'enabled'); await load() }
    catch (ex) { setErr((ex as Error).message) }
    finally { setBusy(null) }
  }

  if (!loaded) return <div className="card"><div className="empty">Loading…</div></div>

  const on = rails.filter((r) => r.state === 'enabled')
  const off = rails.filter((r) => r.state === 'disabled')
  const na = rails.filter((r) => r.state === 'unavailable')

  const card = (r: RailManagerEntry) => (
    <div className="card admin-rail-card" key={r.id}>
      <div className="row" style={{ gap: 10, alignItems: 'baseline' }}>
        <span style={{ fontSize: 20 }} aria-hidden="true">{r.icon}</span>
        <b style={{ fontSize: 15 }}>{r.label}</b>
        {r.state === 'enabled' && <Badge tone="good">on</Badge>}
        {r.state === 'disabled' && <Badge tone="warning">off</Badge>}
        {r.state === 'unavailable' && <Badge tone="neutral">not installed</Badge>}
        {r.status === 'soon' && <Badge tone="neutral">roadmap</Badge>}
        {r.installed && !r.bundle_built && (
          <Badge tone="critical" title="No built frontend bundle resolves for this rail, so it would render blank rather than show an error. Build its frontend and restart the gateway.">no bundle</Badge>
        )}
      </div>
      {r.description && (
        <div className="muted" style={{ fontSize: 13, marginTop: 6, lineHeight: 1.45 }}>
          {r.description}
        </div>
      )}
      <div className="row" style={{ gap: 10, marginTop: 10, alignItems: 'center' }}>
        <span className="muted" style={{ fontSize: 12 }}>
          {r.entitled_users} user{r.entitled_users === 1 ? '' : 's'} entitled
        </span>
        <div style={{ flex: 1 }} />
        {r.state === 'unavailable' ? (
          <span className="muted" style={{ fontSize: 12 }}>
            add to PLATFORM_ENABLED_APPS and restart
          </span>
        ) : (
          <Button variant="ghost" size="sm" disabled={busy === r.id} onClick={() => void toggle(r)}>
            {busy === r.id ? '…' : r.state === 'enabled' ? 'Turn off' : 'Turn on'}
          </Button>
        )}
      </div>
    </div>
  )

  return (
    <>
      {err && <div className="rail-slot-warn">{err}</div>}
      <div className="card">
        <div className="muted" style={{ fontSize: 13, lineHeight: 1.5 }}>
          Turning a rail <b>off</b> removes it from everyone&apos;s launcher. It is reversible and
          deletes nothing — and it is availability control, not enforcement: someone already working
          in that rail keeps their session rather than being cut off mid-task. Per-user access is
          separate, in <b>Users</b>.
        </div>
      </div>

      {on.length > 0 && <h3 className="admin-h">Running ({on.length})</h3>}
      <div className="admin-rail-grid">{on.map(card)}</div>

      {off.length > 0 && <h3 className="admin-h">Switched off ({off.length})</h3>}
      <div className="admin-rail-grid">{off.map(card)}</div>

      {na.length > 0 && (
        <>
          <h3 className="admin-h">Not installed here ({na.length})</h3>
          <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
            In the catalog but not part of this deployment. They cannot be switched on from here:
            a rail&apos;s bundle is mounted when the gateway starts.
          </div>
          <div className="admin-rail-grid">{na.map(card)}</div>
        </>
      )}
    </>
  )
}

function ModelsTab() {
  const [models, setModels] = useState<ModelPoolEntry[]>([])
  const [categories, setCategories] = useState<ModelCategory[]>([])
  const [err, setErr] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [rescanning, setRescanning] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [confirmDel, setConfirmDel] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const d = await platformApi.adminModels()
      setModels(d.models); setCategories(d.categories ?? []); setErr('')
    }
    catch (ex) { setErr((ex as Error).message) }
    finally { setLoaded(true) }
  }, [])
  useEffect(() => { load() }, [load])

  const rescan = useCallback(async () => {
    setRescanning(true)
    try { await load() } finally { setRescanning(false) }
  }, [load])

  const guard = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name); setErr('')
    try { await fn(); await load() }
    catch (ex) { setErr((ex as Error).message) }
    finally { setBusy(null) }
  }
  const toggle = (m: ModelPoolEntry) => guard(m.name, () => platformApi.adminModelToggle(m.name, !m.enabled))
  const del = (m: ModelPoolEntry) => guard(m.name, async () => { await platformApi.adminModelDelete(m.name); setConfirmDel(null) })

  if (!loaded) return <div className="card"><div className="empty">Loading…</div></div>

  const renderRow = (m: ModelPoolEntry) => {
    const b = busy === m.name
    const confirming = confirmDel === m.name
    return (
      <tr key={m.name} style={m.enabled ? undefined : { opacity: 0.6 }}>
        <td>
          <code>{m.name}</code>
          {m.parameter_size && <span className="muted"> · {m.parameter_size}</span>}
          {m.generated && (
            <Badge tone="neutral" title="Category + description auto-generated by the local model, cached">
              auto
            </Badge>
          )}
          {m.blurb && <div className="muted" style={{ fontSize: 12, marginTop: 3, maxWidth: 560, lineHeight: 1.4 }}>{m.blurb}</div>}
        </td>
        <td><span className="muted">{m.class || '—'}</span></td>
        <td>
          <div className="row" style={{ gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            {m.in_use ? <Badge tone="accent">in use</Badge> : <Badge tone="neutral">idle</Badge>}
            {m.loaded && <Badge tone="good">loaded</Badge>}
            {!m.enabled && <Badge tone="warning">disabled</Badge>}
            {m.in_use && <span className="muted" style={{ fontSize: 12 }}>{m.roles.map((r) => `@${r}`).join(', ')}</span>}
          </div>
        </td>
        <td>
          {confirming ? (
            <div className="row" style={{ gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <span className="rail-slot-warn" style={{ margin: 0 }}>
                Delete <b>{m.name}</b> from disk permanently? This cannot be undone.
              </span>
              <Button variant="danger" size="sm" disabled={b} onClick={() => del(m)}>
                {b ? 'Deleting…' : 'Confirm delete'}
              </Button>
              <Button variant="ghost" size="sm" disabled={b} onClick={() => setConfirmDel(null)}>cancel</Button>
            </div>
          ) : (
            <div className="row" style={{ gap: 6, justifyContent: 'flex-end' }}>
              <Button variant="ghost" size="sm" disabled={b} onClick={() => toggle(m)}>
                {m.enabled ? 'Disable' : 'Enable'}
              </Button>
              <Button variant="danger" size="sm" disabled={b || m.in_use}
                title={m.in_use ? 'In use by a rail — repoint it in the Rails tab first' : 'Permanently delete from disk'}
                onClick={() => setConfirmDel(m.name)}>delete</Button>
            </div>
          )}
        </td>
      </tr>
    )
  }

  // Group the (already in_use/class/name-sorted) models by category, in the catalog's order.
  const ordered = [...categories].sort((a, b) => a.order - b.order)
  const known = new Set(ordered.map((c) => c.id))
  const groups = ordered
    .map((c) => ({ cat: c, items: models.filter((m) => (m.category || 'other') === c.id) }))
    .filter((g) => g.items.length > 0)
  const orphans = models.filter((m) => !known.has(m.category || 'other'))
  if (orphans.length) groups.push({ cat: { id: 'other', label: 'Other', order: 99 }, items: orphans })

  return (
    <>
      {err && <div className="card" style={{ marginBottom: 16 }}><p className="error-line" style={{ margin: 0 }}>{err}</p></div>}

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
          <h3 style={{ marginTop: 0 }}>Model pool</h3>
          <Button variant="ghost" size="sm" disabled={rescanning} onClick={rescan}
                  title="Re-read the installed models from the broker">
            {rescanning ? 'Scanning…' : '↻ Re-scan'}
          </Button>
        </div>
        <p className="muted" style={{ margin: 0 }}>
          Every model installed on the workstation, grouped by what it's for. <b>Disable</b> hides a
          model from the rail pickers and frees its VRAM — reversible, and any rail already pointed
          at it keeps working. <b>Delete</b> removes it from disk for good and is blocked while a
          rail depends on it. The pool is read live on open; <b>Re-scan</b> re-reads it now, and the
          scheduler's <i>Model pool scan</i> (Schedule tab) does it hands-off.
        </p>
      </div>

      {groups.map((g) => (
        <div className="card" key={g.cat.id} style={{ marginBottom: 16 }}>
          <h4 style={{ marginTop: 0 }}>{g.cat.label} <span className="muted" style={{ fontWeight: 400 }}>· {g.items.length}</span></h4>
          <table className="admin-table">
            <thead>
              <tr><th>Model</th><th>Class</th><th>Status</th><th></th></tr>
            </thead>
            <tbody>{g.items.map(renderRow)}</tbody>
          </table>
        </div>
      ))}
    </>
  )
}

// ---------------------------------------------------------------------------
// Rails tab: per-rail model selection.
// ---------------------------------------------------------------------------

// --- broker (upstream) selection -------------------------------------------------
// A slot may run on THIS box or on another registered broker. The gateway reports the
// registered brokers in `upstreams`, a model pool per broker in `models`, and the broker a slot
// currently uses in the slot's `upstream`. The stored role value is `upstream::model` — a DOUBLE
// colon, because a single one is already the model/tag separator — but the PUT takes the two
// apart, so everything below works in BARE model names and lets the gateway recompose.

const LOCAL = 'local'
const IMAGE_LOCAL_NOTE = "Image backends are this box's media worker, so this slot always runs locally."

interface UpstreamOption {
  name: string
  url: string | null
  reachable: boolean
  authorized?: boolean // absent when the upstream needs no token (e.g. local)
}
// Model pools keyed by upstream name. An older gateway sends a bare array instead; that is the
// local pool, and normalising it here keeps the panel working rather than emptying every dropdown.
type ModelsByUpstream = Record<string, ModelOption[]>
type SlotV2 = RailModelSlot & { upstream?: string | null }
type RailV2 = Omit<RailModels, 'slots'> & { slots: SlotV2[] }
interface RailsSettingsV2 extends Omit<RailsSettings, 'rails' | 'models'> {
  rails: RailV2[]
  models: ModelOption[] | ModelsByUpstream
  upstreams?: UpstreamOption[]
}

function modelsByUpstream(models: ModelOption[] | ModelsByUpstream | undefined): ModelsByUpstream {
  if (Array.isArray(models)) return { [LOCAL]: models }
  return models ?? {}
}

// Split a stored role value into (upstream, bare model ref), mirroring the broker's own rule:
// an UNKNOWN name before `::` is not a delegation, so the whole string resolves locally.
function splitRef(value: string, known: Set<string>): { upstream: string; ref: string } {
  const i = value.indexOf('::')
  if (i > 0) {
    const name = value.slice(0, i)
    if (known.has(name)) return { upstream: name, ref: value.slice(i + 2) }
  }
  return { upstream: LOCAL, ref: value }
}

// The broker + bare model a slot sits on now. The slot's explicit `upstream` wins; a gateway
// that doesn't send one still parses out of the stored `upstream::model` pattern.
function slotRef(s: SlotV2, known: Set<string>): { upstream: string; ref: string } {
  const split = splitRef(s.pattern, known)
  return { upstream: s.upstream || split.upstream, ref: split.ref }
}

function upstreamLabel(u: UpstreamOption): string {
  if (u.name === LOCAL) return 'Local (this box)'
  if (!u.url) return u.name
  // Show the host so two remotes are told apart at a glance; fall back to the raw URL.
  try { return `${u.name} (${new URL(u.url).host})` } catch { return `${u.name} (${u.url})` }
}
// Why an upstream can't be picked — '' when it can. Such an upstream is shown disabled, never
// hidden: an option that vanishes is how an operator concludes the feature is broken.
function upstreamBlocked(u: UpstreamOption): string {
  if (!u.reachable) return 'unreachable'
  if (u.authorized === false) return 'token rejected'
  return ''
}

// platformApi.adminSetRailModel() predates the broker selector and sends {model} alone, so the
// PUT is issued here with {model, upstream}. Same error semantics as web-core's req(): read the
// body ONCE (a second read throws "body stream already read" and masks the real error), then
// prefer the API's {detail} so a 400 surfaces inline as the panel already expects.
async function putRailModel(role: string, model: string, upstream: string): Promise<RailsSettingsV2> {
  const res = await fetch(`/api/platform/admin/rails/${role}`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ model, upstream }),
  })
  const raw = await res.text().catch(() => '')
  if (!res.ok) {
    let detail = raw
    try {
      const body = JSON.parse(raw)
      detail = typeof body?.detail === 'string' ? body.detail : JSON.stringify(body)
    } catch {
      /* body wasn't JSON (e.g. a plain-text 500 / proxy HTML) — keep raw text */
    }
    throw new Error(detail || `${res.status} ${res.statusText}`)
  }
  return (raw ? JSON.parse(raw) : {}) as RailsSettingsV2
}

function RailsTab() {
  const [rails, setRails] = useState<RailV2[]>([])
  const [models, setModels] = useState<ModelsByUpstream>({}) // model pool per upstream
  const [media, setMedia] = useState<MediaOption[]>([]) // image backends for image slots
  const [upstreams, setUpstreams] = useState<UpstreamOption[]>([]) // registered brokers
  const [sel, setSel] = useState<Record<string, string>>({}) // role -> pending model ('' = cleared)
  const [selUp, setSelUp] = useState<Record<string, string>>({}) // role -> pending upstream
  const [err, setErr] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [busyRole, setBusyRole] = useState<string | null>(null)
  const [savedRole, setSavedRole] = useState<string | null>(null)

  const apply = useCallback((view: RailsSettingsV2) => {
    const ups = view.upstreams ?? []
    const knownNames = new Set(ups.map((u) => u.name))
    setRails(view.rails)
    setModels(modelsByUpstream(view.models))
    setMedia(view.media)
    setUpstreams(ups)
    // Reset each slot's pending selection to its now-current broker + bare model.
    const next: Record<string, string> = {}
    const nextUp: Record<string, string> = {}
    for (const r of view.rails) for (const s of r.slots) {
      const cur = slotRef(s, knownNames)
      next[s.role] = cur.ref
      nextUp[s.role] = cur.upstream
    }
    setSel(next)
    setSelUp(nextUp)
  }, [])

  // Only local registered? Then this is a single-box install and the panel renders exactly as
  // it did before the selector existed — no extra control, no extra copy.
  const knownUps = useMemo(() => new Set(upstreams.map((u) => u.name)), [upstreams])
  const showBroker = useMemo(() => upstreams.some((u) => u.name !== LOCAL), [upstreams])

  const load = useCallback(async () => {
    try {
      apply(await platformApi.adminRails())
      setErr('')
    } catch (ex) {
      setErr((ex as Error).message)
    } finally {
      setLoaded(true)
    }
  }, [apply])

  useEffect(() => { load() }, [load])

  const setRoleModel = async (role: string, model: string, upstream: string) => {
    setBusyRole(role)
    setErr('')
    try {
      apply(await putRailModel(role, model, upstream))
      setSavedRole(role)
      setTimeout(() => setSavedRole((r) => (r === role ? null : r)), 2200)
    } catch (ex) {
      setErr((ex as Error).message)
    } finally {
      setBusyRole(null)
    }
  }

  // Switching broker re-points the model dropdown at that box's pool and clears the pick, since
  // a model on one box almost never exists on the other; keeping it would only earn a 400 from
  // Apply. Switching back restores the slot's stored model — that one is known-good for that box.
  const pickUpstream = (slot: SlotV2, name: string) => {
    const cur = slotRef(slot, knownUps)
    setSelUp((c) => ({ ...c, [slot.role]: name }))
    setSel((c) => ({ ...c, [slot.role]: name === cur.upstream ? cur.ref : '' }))
  }

  const onApply = (slot: SlotV2) => {
    const model = sel[slot.role]
    const cur = slotRef(slot, knownUps)
    // Image slots are local by construction — never echo back a stale remote the payload may
    // still carry for one, or Apply would re-save a delegation the media worker can't honour.
    const upstream = slot.kind === 'image' ? LOCAL : (selUp[slot.role] ?? cur.upstream)
    if (!model || (model === cur.ref && upstream === cur.upstream)) return
    void setRoleModel(slot.role, model, upstream)
  }

  if (!loaded) return <div className="card"><div className="empty">Loading…</div></div>

  return (
    <>
      {err && <div className="card" style={{ marginBottom: 16 }}><p className="error-line" style={{ margin: 0 }}>{err}</p></div>}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Rail models</h3>
        <p className="muted" style={{ margin: 0 }}>
          The LLM each rail loads for a task. Each rail has its own model role, so changing one
          repoints only that rail — it takes effect on the next request, no restart. Pick a specific
          model to pin it, or “Auto” to always use the newest match.
          {showBroker && (
            <> Where more than one broker is registered, each slot also picks the <b>box</b> that
            runs it; the model list then shows what is installed <i>there</i>.</>
          )}
        </p>
      </div>

      {rails.length === 0 && (
        <div className="card"><div className="empty">No rails with configurable models are installed here.</div></div>
      )}

      {rails.map((rail) => (
        <div className="card" key={rail.id} style={{ marginBottom: 16 }}>
          <h3 className="rail-title">
            <span aria-hidden="true">{rail.icon}</span> {rail.label}
          </h3>
          <div className="rail-slots">
            {rail.slots.map((s) => {
              // An image slot is pinned to this box: its backends are the local media worker.
              const imageSlot = s.kind === 'image'
              const cur = slotRef(s, knownUps) // broker + bare model stored for this slot
              const curUp = imageSlot ? LOCAL : (selUp[s.role] ?? cur.upstream)
              const current = sel[s.role] ?? cur.ref
              // Options depend on the slot kind: image slots offer the media backends;
              // a vision slot only offers vision-capable models; chat offers any generative model.
              // For chat/vision the pool is the one reported for the SELECTED broker.
              const pool = models[curUp] ?? []
              const opts: { value: string; text: string }[] =
                imageSlot
                  ? media.map((m) => ({ value: m.name, text: m.note ? `${m.label} — ${m.note}` : m.label }))
                  : (s.kind === 'vision' ? pool.filter((m) => m.vision) : pool)
                      .map((m) => ({ value: m.name, text: m.name + (m.parameter_size ? ` · ${m.parameter_size}` : '') }))
              // The stored pattern only describes the box it is stored against, so the synthetic
              // "Auto:"/"not installed" option is offered only while that box is the one selected.
              const atSlotUp = curUp === cur.upstream
              const isWild = /[*?[\]]/.test(cur.ref)
              const known = opts.some((o) => o.value === cur.ref)
              const changed = current !== '' && (current !== cur.ref || curUp !== cur.upstream)
              const atDefault = s.pattern === s.default
              const busy = busyRole === s.role
              return (
                <div className="rail-slot" key={s.role}>
                  <div className="rail-slot-head">
                    <span className="rail-slot-label">{s.label}</span>
                    {s.kind !== 'chat' && <span className="rail-slot-kind">{s.kind}</span>}
                    <span className="muted rail-slot-role" title={`model role · env ${s.env}`}>@{s.role}</span>
                  </div>
                  <p className="rail-slot-desc">{s.description}</p>
                  <div className="rail-slot-ctl">
                    {showBroker && (
                      <select
                        value={curUp}
                        disabled={busy || imageSlot}
                        aria-label={`Broker for ${s.label}`}
                        title={imageSlot ? IMAGE_LOCAL_NOTE : 'Which broker runs this slot'}
                        style={{ minWidth: 200 }}
                        onChange={(e) => pickUpstream(s, e.target.value)}
                      >
                        {/* A slot pointed at a broker that is no longer registered still has to
                            show what it is pointed at, or the panel would silently misreport it. */}
                        {!knownUps.has(curUp) && <option value={curUp}>{curUp} — not registered</option>}
                        {upstreams.map((u) => {
                          const blocked = upstreamBlocked(u)
                          return (
                            <option key={u.name} value={u.name} disabled={!!blocked}>
                              {upstreamLabel(u)}{blocked ? ` — ${blocked}` : ''}
                            </option>
                          )
                        })}
                      </select>
                    )}
                    <select
                      value={current}
                      disabled={busy}
                      aria-label={`Model for ${s.label}`}
                      onChange={(e) => setSel((c) => ({ ...c, [s.role]: e.target.value }))}
                    >
                      {current === '' && <option value="">Select a model…</option>}
                      {atSlotUp && (isWild || !known) && (
                        <option value={cur.ref}>
                          {isWild
                            ? `Auto: ${cur.ref}${s.model ? ` → ${s.model}` : ' (none installed)'}`
                            : `${cur.ref} (not installed)`}
                        </option>
                      )}
                      {opts.map((o) => (
                        <option key={o.value} value={o.value}>{o.text}</option>
                      ))}
                    </select>
                    <Button size="sm" disabled={!changed || busy} onClick={() => onApply(s)}>
                      {busy ? 'Applying…' : 'Apply'}
                    </Button>
                    {savedRole === s.role && <span className="rail-slot-saved">✓ applied</span>}
                  </div>
                  <div className="rail-slot-foot">
                    <span className="muted">Default: <code>{s.default}</code></span>
                    {!atDefault && (
                      <button type="button" className="rail-slot-revert" disabled={busy}
                              onClick={() => {
                                // A shipped default may itself delegate (`offsite::model`), so it
                                // is split the same way rather than posted as one opaque string.
                                const d = splitRef(s.default, knownUps)
                                void setRoleModel(s.role, d.ref, d.upstream)
                              }}>
                        revert to default
                      </button>
                    )}
                  </div>
                  {showBroker && imageSlot && (
                    <p className="rail-slot-desc" style={{ margin: '8px 0 0' }}>{IMAGE_LOCAL_NOTE}</p>
                  )}
                  {!imageSlot && curUp !== LOCAL && opts.length === 0 && (
                    <p className="rail-slot-warn">
                      “{curUp}” reported no {s.kind === 'vision' ? 'vision-capable ' : ''}models — it may be
                      unreachable, or nothing is installed on that box.
                    </p>
                  )}
                  {s.kind === 'vision' && curUp === LOCAL && opts.length === 0 && (
                    <p className="rail-slot-warn">No vision-capable model is installed — install one to change this slot.</p>
                  )}
                  {!s.installed && (
                    <p className="rail-slot-warn">
                      This role resolves to nothing installed right now — pick an installed model.
                    </p>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      ))}
    </>
  )
}

// ---------------------------------------------------------------------------
// Users tab (unchanged behaviour; delegation-aware).
//   You can only grant apps you hold yourself, and only a super-admin can grant
//   super-admin or edit another super-admin.
// ---------------------------------------------------------------------------

function UsersTab({ meUsername }: { meUsername: string }) {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [catalog, setCatalog] = useState<AppEntry[]>([])
  const [grantable, setGrantable] = useState<string[]>([])
  const [err, setErr] = useState('')
  const [loaded, setLoaded] = useState(false)

  // new-user form
  const [nu, setNu] = useState('')
  const [np, setNp] = useState('')
  const [nAdmin, setNAdmin] = useState(false)
  const [nSuper, setNSuper] = useState(false)
  const [nApps, setNApps] = useState<string[]>([])

  const load = useCallback(async () => {
    try {
      const r = await platformApi.adminUsers()
      setUsers(r.users)
      setCatalog(r.catalog)
      setGrantable(r.grantable ?? [])
      setErr('')
    } catch (ex) {
      setErr((ex as Error).message)
    } finally {
      setLoaded(true)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const iAmSuper = useMemo(
    () => users.find((u) => u.username === meUsername)?.is_superadmin ?? false,
    [users, meUsername],
  )
  const canGrant = (id: string) => grantable.includes(id)
  // A row I'm not allowed to edit: a super-admin, unless I'm a super-admin too.
  const locked = (u: AdminUser) => u.is_superadmin && !iAmSuper

  const guard = async (fn: () => Promise<unknown>) => {
    try {
      await fn()
      await load()
    } catch (ex) {
      setErr((ex as Error).message)
    }
  }

  const create = () =>
    guard(async () => {
      await platformApi.adminCreate({
        username: nu.trim(), password: np, is_admin: nAdmin || nSuper, is_superadmin: nSuper, apps: nApps,
      })
      setNu(''); setNp(''); setNAdmin(false); setNSuper(false); setNApps([])
    })

  const toggleApp = (u: AdminUser, appId: string) => {
    const apps = u.apps.includes(appId) ? u.apps.filter((a) => a !== appId) : [...u.apps, appId]
    guard(() => platformApi.adminUpdate(u.id, { apps }))
  }

  const changeRole = (u: AdminUser, role: string) => {
    const payload: { is_admin?: boolean; is_superadmin?: boolean } = {}
    if (role === 'superadmin') {
      payload.is_superadmin = true
    } else if (role === 'admin') {
      payload.is_admin = true
      if (u.is_superadmin) payload.is_superadmin = false
    } else {
      payload.is_admin = false
      if (u.is_superadmin) payload.is_superadmin = false
    }
    guard(() => platformApi.adminUpdate(u.id, payload))
  }

  const resetPw = (u: AdminUser) => {
    const pw = prompt(`New password for ${u.username}:`)
    if (pw) guard(() => platformApi.adminUpdate(u.id, { password: pw }))
  }
  const remove = (u: AdminUser) => {
    if (confirm(`Delete user "${u.username}"?`)) guard(() => platformApi.adminDelete(u.id))
  }

  const toggleNewApp = (appId: string) =>
    setNApps((cur) => (cur.includes(appId) ? cur.filter((a) => a !== appId) : [...cur, appId]))

  const roleBadge = (u: AdminUser) =>
    u.is_superadmin ? <Badge tone="accent">super-admin</Badge>
      : u.is_admin ? <Badge tone="accent">admin</Badge>
      : <Badge tone="neutral">user</Badge>

  if (!loaded) return <div className="card"><div className="empty">Loading…</div></div>

  return (
    <>
      {err && <div className="card" style={{ marginBottom: 16 }}><p className="error-line" style={{ margin: 0 }}>{err}</p></div>}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Add user</h3>
        <div className="admin-form">
          <div className="fld">
            <label>Username</label>
            {/* autoComplete off + a non-login field name so the browser doesn't autofill the
                signed-in admin's saved credentials into this create-user form. */}
            <input type="text" name="new-user" autoComplete="off" value={nu}
                   onChange={(e) => setNu(e.target.value)} placeholder="e.g. teacher" />
          </div>
          <div className="fld">
            <label>Password</label>
            <input type="password" name="new-user-password" autoComplete="new-password" value={np}
                   onChange={(e) => setNp(e.target.value)} />
          </div>
          <div className="fld">
            <label>Apps</label>
            <div className="app-checks">
              {catalog.map((a) => (
                <label key={a.id} className={nApps.includes(a.id) ? 'on' : ''}
                       style={canGrant(a.id) && !nSuper ? undefined : { opacity: 0.45 }}
                       title={canGrant(a.id) ? undefined : 'you are not entitled to grant this app'}>
                  <input type="checkbox" checked={nApps.includes(a.id)} disabled={!canGrant(a.id) || nSuper}
                         onChange={() => toggleNewApp(a.id)} />
                  {a.label}
                </label>
              ))}
              {nSuper && <span className="muted" style={{ fontSize: 12.5 }}>a super-admin sees every app</span>}
            </div>
          </div>
          <div className="fld">
            <label>Role</label>
            <div className="row" style={{ gap: 10, flexWrap: 'wrap' }}>
              <label className={nAdmin || nSuper ? 'on' : ''} style={{ display: 'inline-flex', gap: 6, alignItems: 'center', border: '1px solid var(--border)', borderRadius: 999, padding: '3px 10px' }}>
                <input type="checkbox" checked={nAdmin || nSuper} disabled={nSuper}
                       onChange={(e) => setNAdmin(e.target.checked)} /> admin (manage users)
              </label>
              {iAmSuper && (
                <label className={nSuper ? 'on' : ''} style={{ display: 'inline-flex', gap: 6, alignItems: 'center', border: '1px solid var(--border)', borderRadius: 999, padding: '3px 10px' }}>
                  <input type="checkbox" checked={nSuper} onChange={(e) => setNSuper(e.target.checked)} /> super-admin (all apps)
                </label>
              )}
            </div>
          </div>
          <Button onClick={create} disabled={!nu.trim() || !np}>Create</Button>
        </div>
      </div>

      <div className="card">
        <h3 style={{ marginTop: 0 }}>Users</h3>
        <table className="admin-table">
          <thead>
            <tr><th>User</th><th>Apps</th><th>Role</th><th></th></tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>
                  {u.username}
                  {u.username === meUsername && <span className="muted"> (you)</span>}
                </td>
                <td>
                  {u.is_superadmin ? (
                    <span className="muted">all apps (super-admin)</span>
                  ) : (
                    <div className="app-checks">
                      {catalog.map((a) => {
                        const on = u.apps.includes(a.id)
                        const lock = !canGrant(a.id) || locked(u)
                        return (
                          <label key={a.id} className={on ? 'on' : ''}
                                 style={lock ? { opacity: 0.45 } : undefined}
                                 title={lock ? 'you are not entitled to grant this app' : undefined}>
                            <input type="checkbox" checked={on} disabled={lock}
                                   onChange={() => toggleApp(u, a.id)} />
                            {a.label}
                          </label>
                        )
                      })}
                    </div>
                  )}
                </td>
                <td>{roleBadge(u)}</td>
                <td>
                  <div className="row" style={{ gap: 6, alignItems: 'center' }}>
                    <select
                      value={u.is_superadmin ? 'superadmin' : u.is_admin ? 'admin' : 'user'}
                      disabled={locked(u) || u.username === meUsername}
                      onChange={(e) => changeRole(u, e.target.value)}
                    >
                      <option value="user">user</option>
                      <option value="admin">admin</option>
                      {(iAmSuper || u.is_superadmin) && <option value="superadmin">super-admin</option>}
                    </select>
                    <Button variant="ghost" size="sm" disabled={locked(u)} onClick={() => resetPw(u)}>reset pw</Button>
                    {u.username !== meUsername && (
                      <Button variant="danger" size="sm" disabled={locked(u)} onClick={() => remove(u)}>delete</Button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
