// Admin console. Six categories (tabs):
//   Rails      — the LLM each rail loads for a task; per-rail model roles, repointed live.
//   Rail Mgr   — what each rail IS and whether it runs at all (not which model it points at).
//   Models     — the workstation model pool: every installed model, its In-Use/Loaded state, and
//                Enable/Disable (reversible) + Delete (irreversible, blocked while In-Use).
//   Schedule   — per-rail maintenance tasks on an Outlook-style recurrence.
//   Users      — manage users, their role, and which apps each may see (delegation-aware).
//   Workspaces — shared rooms: whose DATA each user sees inside an app they can already reach.
//   Broker     — named, revocable access tokens for the GPU broker (SUPER-ADMIN only).
// Admin-only; the gateway's /api/platform/admin/* endpoints enforce the same rules server-side.
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { Badge, Button, platformApi } from '@web-core'
import type { AdminUser, AppEntry, BrokerToken, MediaOption, ModelCategory, ModelOption, ModelPoolEntry, RailManagerEntry, RailModelSlot, RailModels, RailSchedules, RailsSettings, Recurrence, UpstreamInfo, Workspace } from '@web-core'

type Tab = 'users' | 'workspaces' | 'rails' | 'manager' | 'models' | 'schedule' | 'broker'

export default function AdminPage({ meUsername }: { meUsername: string }) {
  const [tab, setTab] = useState<Tab>('rails')
  // A freshly minted broker token is shown ONCE and is held only in BrokerTab's state, so
  // switching tabs unmounts it and loses it -- even though the admin never dismissed it. The
  // hash-only storage is deliberate and stays; losing the one reveal to a stray click is not.
  // Only the FLAG lives up here, never the token: the secret's blast radius stays inside the
  // tab that minted it.
  const [tokenOnScreen, setTokenOnScreen] = useState(false)
  const go = (next: Tab) => {
    if (tokenOnScreen && next !== 'broker'
        && !window.confirm('The new token is still on screen and cannot be shown again. '
                           + 'Leave without copying it?')) return
    setTab(next)
  }
  return (
    <div className="module">
      <div className="admin-tabs" role="tablist" aria-label="Admin sections">
        <button className={`admin-tab ${tab === 'rails' ? 'on' : ''}`} role="tab"
                aria-selected={tab === 'rails'} onClick={() => go('rails')}>Rails</button>
        <button className={`admin-tab ${tab === 'manager' ? 'on' : ''}`} role="tab"
                aria-selected={tab === 'manager'} onClick={() => go('manager')}>Rail Manager</button>
        <button className={`admin-tab ${tab === 'models' ? 'on' : ''}`} role="tab"
                aria-selected={tab === 'models'} onClick={() => go('models')}>Models</button>
        <button className={`admin-tab ${tab === 'schedule' ? 'on' : ''}`} role="tab"
                aria-selected={tab === 'schedule'} onClick={() => go('schedule')}>Schedule</button>
        <button className={`admin-tab ${tab === 'users' ? 'on' : ''}`} role="tab"
                aria-selected={tab === 'users'} onClick={() => go('users')}>Users</button>
        <button className={`admin-tab ${tab === 'workspaces' ? 'on' : ''}`} role="tab"
                aria-selected={tab === 'workspaces'} onClick={() => go('workspaces')}>Workspaces</button>
        <button className={`admin-tab ${tab === 'broker' ? 'on' : ''}`} role="tab"
                aria-selected={tab === 'broker'} onClick={() => go('broker')}>Broker</button>
      </div>
      {tab === 'rails' ? <RailsTab />
        : tab === 'manager' ? <RailManagerTab />
        : tab === 'models' ? <ModelsTab />
        : tab === 'schedule' ? <ScheduleTab />
        : tab === 'workspaces' ? <WorkspacesTab />
        : tab === 'broker' ? <BrokerTab onRevealChange={setTokenOnScreen} />
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
  // One stable name per editor instance: the three radios must share it to be a real group
  // (arrow-key navigation, screen-reader grouping), and it must not change between renders.
  const freqName = useId()
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
            <input type="radio" name={freqName} checked={rec.freq === f} onChange={() => onChange({ freq: f })} />
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
  const savedTimer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(savedTimer.current), [])

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
    try {
      apply(await platformApi.adminSetSchedule(rail, tid, draft[k])); setSaved(k)
      window.clearTimeout(savedTimer.current)
      savedTimer.current = window.setTimeout(() => setSaved((s) => (s === k ? null : s)), 2200)
    }
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

function RailsTab() {
  const [rails, setRails] = useState<RailModels[]>([])
  // Keyed by upstream, because a slot's valid choices depend on WHERE it runs: point a role
  // off-site and the dropdown has to offer that box's inventory, not this card's.
  const [models, setModels] = useState<Record<string, ModelOption[]>>({})
  const [media, setMedia] = useState<MediaOption[]>([]) // image backends for image slots
  const [upstreams, setUpstreams] = useState<UpstreamInfo[]>([])
  const [sel, setSel] = useState<Record<string, string>>({}) // role -> pending selection
  const [selUp, setSelUp] = useState<Record<string, string>>({}) // role -> pending upstream
  const [err, setErr] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [busyRole, setBusyRole] = useState<string | null>(null)
  const [savedRole, setSavedRole] = useState<string | null>(null)
  const savedRoleTimer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(savedRoleTimer.current), [])

  const apply = useCallback((view: RailsSettings) => {
    setRails(view.rails)
    setModels(view.models ?? {})
    setMedia(view.media)
    setUpstreams(view.upstreams ?? [{ name: 'local', url: '', healthy: true }])
    // Reset each slot's pending selection to its now-current pattern. The stored pattern of a
    // delegated slot carries no `upstream::` prefix here — the gateway strips it and reports
    // the box separately — so the two dropdowns stay independent.
    const next: Record<string, string> = {}
    const nextUp: Record<string, string> = {}
    for (const r of view.rails) for (const s of r.slots) {
      next[s.role] = s.pattern
      nextUp[s.role] = s.upstream || 'local'
    }
    setSel(next)
    setSelUp(nextUp)
  }, [])

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

  const setRoleModel = async (role: string, model: string, upstream = 'local') => {
    setBusyRole(role)
    setErr('')
    try {
      apply(await platformApi.adminSetRailModel(role, model, upstream))
      setSavedRole(role)
      window.clearTimeout(savedRoleTimer.current)
      savedRoleTimer.current = window.setTimeout(() => setSavedRole((r) => (r === role ? null : r)), 2200)
    } catch (ex) {
      setErr((ex as Error).message)
    } finally {
      setBusyRole(null)
    }
  }

  const onApply = (slot: RailModelSlot) => {
    const model = sel[slot.role]
    const upstream = selUp[slot.role] ?? slot.upstream ?? 'local'
    // Changing only the BOX is still a change, so the pattern-equality guard alone is not
    // enough — but an empty model is not applyable either way, and switching boxes clears it.
    if (!model) return
    if (model === slot.pattern && upstream === (slot.upstream || 'local')) return
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
              // Options depend on the slot kind AND on where it runs. Image slots offer the
              // media backends (always local); a vision slot only offers vision-capable
              // models; chat offers any generative model — but all of them come from the
              // inventory of the box the slot is pointed at, not from this card's.
              const curUp = selUp[s.role] ?? s.upstream ?? 'local'
              const pool = models[curUp] ?? []
              const opts: { value: string; text: string }[] =
                s.kind === 'image'
                  ? media.map((m) => ({ value: m.name, text: m.note ? `${m.label} — ${m.note}` : m.label }))
                  : (s.kind === 'vision' ? pool.filter((m) => m.vision) : pool)
                      .map((m) => ({ value: m.name, text: m.name + (m.parameter_size ? ` · ${m.parameter_size}` : '') }))
              const isWild = /[*?[\]]/.test(s.pattern)
              const known = opts.some((o) => o.value === s.pattern)
              const current = sel[s.role] ?? s.pattern
              const upChanged = curUp !== (s.upstream || 'local')
              const changed = current !== s.pattern || upChanged
              const atDefault = s.pattern === s.default && (s.upstream || 'local') === 'local'
              const busy = busyRole === s.role
              // Images are never delegated: a media backend is loaded by THIS box's media
              // worker from its own HF cache, so there is nothing to forward.
              const canDelegate = s.kind !== 'image' && upstreams.length > 1
              return (
                <div className="rail-slot" key={s.role}>
                  <div className="rail-slot-head">
                    <span className="rail-slot-label">{s.label}</span>
                    {s.kind !== 'chat' && <span className="rail-slot-kind">{s.kind}</span>}
                    <span className="muted rail-slot-role" title={`model role · env ${s.env}`}>@{s.role}</span>
                  </div>
                  <p className="rail-slot-desc">{s.description}</p>
                  <div className="rail-slot-ctl">
                    {canDelegate && (
                      <select
                        value={curUp}
                        disabled={busy}
                        title="Which broker runs this role"
                        aria-label={`Broker for @${s.role}`}
                        onChange={(e) => {
                          const up = e.target.value
                          setSelUp((c) => ({ ...c, [s.role]: up }))
                          // The current model almost certainly is not installed on the box we
                          // just switched to, so clear it rather than leaving a stale name that
                          // would be saved against the wrong inventory.
                          setSel((c) => ({ ...c, [s.role]: '' }))
                        }}
                      >
                        {upstreams.map((u) => (
                          <option key={u.name} value={u.name} disabled={!u.healthy}>
                            {u.name === 'local' ? 'local' : u.name}{u.healthy ? '' : ' (unreachable)'}
                          </option>
                        ))}
                      </select>
                    )}
                    <select
                      value={current}
                      disabled={busy}
                      onChange={(e) => setSel((c) => ({ ...c, [s.role]: e.target.value }))}
                    >
                      {current === '' && <option value="">Select a model on {curUp}…</option>}
                      {(isWild || !known) && (
                        <option value={s.pattern}>
                          {isWild
                            ? `Auto: ${s.pattern}${s.model ? ` → ${s.model}` : ' (none installed)'}`
                            : `${s.pattern} (not installed)`}
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
                              onClick={() => setRoleModel(s.role, s.default)}>
                        revert to default
                      </button>
                    )}
                  </div>
                  {s.kind === 'vision' && opts.length === 0 && (
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

// ---------------------------------------------------------------------------
// Workspaces tab: shared workspaces ("rooms").
//   Entitlements (Users tab) say which APPS you may reach; a workspace says whose DATA you see
//   once inside one. Membership is symmetric and full-rights, a user may belong to several, and
//   their peer set is the union of those, minus themselves.
// ---------------------------------------------------------------------------

function WorkspacesTab() {
  const [rows, setRows] = useState<Workspace[]>([])
  const [usernames, setUsernames] = useState<string[]>([])
  const [err, setErr] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState<number | 'new' | null>(null)
  const [confirmDel, setConfirmDel] = useState<number | null>(null)
  const [names, setNames] = useState<Record<number, string>>({}) // id -> pending rename

  // new-workspace form
  const [nName, setNName] = useState('')
  const [nMembers, setNMembers] = useState<string[]>([])
  // Whom THIS admin may place in a room. The server decides (a workspace grant is stronger
  // than an app grant: it decides whose records you can read), and the UI mirrors it rather
  // than inventing a restriction -- the same shape as `grantable` in the Users tab. It has to
  // be mirrored, because these checkboxes PATCH on toggle: without it the admin is invited to
  // click something the server answers with a 403.
  const [manageable, setManageable] = useState<string[]>([])
  const canManage = (u: string) => manageable.includes(u)

  const load = useCallback(async () => {
    try {
      // Members are picked from the real user list, which is why the gateway's 400
      // "no such user(s)" is unreachable from this UI — it is still rendered if it turns up.
      const [w, u] = await Promise.all([platformApi.adminWorkspaces(), platformApi.adminUsers()])
      setRows(w.workspaces)
      setManageable(w.manageable ?? [])
      setUsernames(u.users.map((x) => x.username).sort())
      setNames({}) // reset each row's pending rename to its now-current name
      setErr('')
    }
    catch (ex) { setErr((ex as Error).message) }
    finally { setLoaded(true) }
  }, [])
  useEffect(() => { load() }, [load])

  const guard = async (key: number | 'new', fn: () => Promise<unknown>) => {
    setBusy(key); setErr('')
    try { await fn(); await load() }
    catch (ex) { setErr((ex as Error).message) }
    finally { setBusy(null) }
  }

  const create = () =>
    guard('new', async () => {
      await platformApi.adminWorkspaceCreate({ name: nName.trim(), members: nMembers })
      setNName(''); setNMembers([])
    })
  const rename = (w: Workspace) =>
    guard(w.id, () => platformApi.adminWorkspaceUpdate(w.id, { name: (names[w.id] ?? w.name).trim() }))
  const toggleMember = (w: Workspace, username: string) => {
    const members = w.members.includes(username)
      ? w.members.filter((m) => m !== username)
      : [...w.members, username]
    guard(w.id, () => platformApi.adminWorkspaceUpdate(w.id, { members }))
  }
  const del = (w: Workspace) =>
    guard(w.id, async () => { await platformApi.adminWorkspaceDelete(w.id); setConfirmDel(null) })

  const toggleNewMember = (username: string) =>
    setNMembers((cur) => (cur.includes(username) ? cur.filter((m) => m !== username) : [...cur, username]))

  if (!loaded) return <div className="card"><div className="empty">Loading…</div></div>

  return (
    <>
      {err && <div className="card" style={{ marginBottom: 16 }}><p className="error-line" style={{ margin: 0 }}>{err}</p></div>}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Add workspace</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          The <b>Users</b> tab says which apps someone may reach; a workspace says whose <b>data</b>{' '}
          they see once inside one. Membership is symmetric and full-rights — every member sees and
          edits every other member's records in the rails that honour workspaces. A user may belong
          to several, and their peer set is the union of those, minus themselves.
        </p>
        <div className="admin-form">
          <div className="fld">
            <label>Name</label>
            <input type="text" name="new-workspace" autoComplete="off" value={nName}
                   onChange={(e) => setNName(e.target.value)} placeholder="e.g. 3rd Grade Team" />
          </div>
          <div className="fld">
            <label>Members</label>
            <div className="app-checks">
              {usernames.map((u) => (
                <label key={u} className={nMembers.includes(u) ? 'on' : ''}
                       style={canManage(u) ? undefined : { opacity: 0.45 }}
                       title={canManage(u) ? undefined
                         : 'a workspace decides whose records this user can read, so it is limited to the users you may already manage'}>
                  <input type="checkbox" checked={nMembers.includes(u)} disabled={!canManage(u)}
                         onChange={() => toggleNewMember(u)} />
                  {u}
                </label>
              ))}
              {usernames.length === 0 && <span className="muted" style={{ fontSize: 12.5 }}>no users to add yet</span>}
            </div>
          </div>
          <Button onClick={create} disabled={!nName.trim() || busy === 'new'}>
            {busy === 'new' ? 'Creating…' : 'Create'}
          </Button>
        </div>
      </div>

      <div className="card">
        <h3 style={{ marginTop: 0 }}>Workspaces</h3>
        {rows.length === 0 ? (
          <div className="empty">No shared workspaces yet — everyone sees only their own records.</div>
        ) : (
          <table className="admin-table">
            <thead>
              <tr><th>Workspace</th><th>Members</th><th>Size</th><th></th></tr>
            </thead>
            <tbody>
              {rows.map((w) => {
                const b = busy === w.id
                const pending = names[w.id] ?? w.name
                const renamed = pending.trim() !== '' && pending.trim() !== w.name
                const confirming = confirmDel === w.id
                return (
                  <tr key={w.id}>
                    <td>
                      <div className="row" style={{ gap: 6, alignItems: 'center' }}>
                        <input type="text" value={pending} disabled={b} aria-label={`Name of workspace ${w.name}`}
                               onChange={(e) => setNames((cur) => ({ ...cur, [w.id]: e.target.value }))} />
                        <Button variant="ghost" size="sm" disabled={!renamed || b} onClick={() => rename(w)}>
                          {b ? '…' : 'rename'}
                        </Button>
                      </div>
                    </td>
                    <td>
                      <div className="app-checks">
                        {usernames.map((u) => {
                          const on = w.members.includes(u)
                          // An existing member the actor may not manage is shown, checked and
                          // locked: the server FREEZES them rather than dropping them, so
                          // hiding the row would make the UI lie about who is in the room.
                          const lock = b || !canManage(u)
                          return (
                            <label key={u} className={on ? 'on' : ''}
                                   style={lock ? { opacity: 0.45 } : undefined}
                                   title={canManage(u) ? undefined
                                     : 'a workspace decides whose records this user can read, so it is limited to the users you may already manage'}>
                              <input type="checkbox" checked={on} disabled={lock} onChange={() => toggleMember(w, u)} />
                              {u}
                            </label>
                          )
                        })}
                      </div>
                    </td>
                    <td>
                      {w.members.length === 0 ? <Badge tone="warning">nobody</Badge>
                        : w.members.length === 1 ? <Badge tone="neutral">1 member</Badge>
                        : <Badge tone="accent">{w.members.length} members</Badge>}
                    </td>
                    <td>
                      {confirming ? (
                        <div className="row" style={{ gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                          <span className="rail-slot-warn" style={{ margin: 0 }}>
                            Stop sharing in <b>{w.name}</b>? Nobody's records are touched — each stays
                            owned by whoever created it and simply stops being visible to the others.
                          </span>
                          <Button variant="danger" size="sm" disabled={b} onClick={() => del(w)}>
                            {b ? 'Deleting…' : 'Confirm delete'}
                          </Button>
                          <Button variant="ghost" size="sm" disabled={b} onClick={() => setConfirmDel(null)}>cancel</Button>
                        </div>
                      ) : (
                        <div className="row" style={{ gap: 6, justifyContent: 'flex-end' }}>
                          <Button variant="danger" size="sm" disabled={b}
                                  title="Removes the sharing only — no records are deleted"
                                  onClick={() => setConfirmDel(w.id)}>delete</Button>
                        </div>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}

// ── Broker ───────────────────────────────────────────────────────────────────
// Named, revocable access tokens for the GPU broker.
//
// WHY THIS EXISTS. BROKER_AUTH_TOKEN is one shared secret: to stop trusting a single machine you
// have to rotate it and then re-deploy every rail that sends it. A named token is per-host and
// revocable on its own, so decommissioning a workstation is one click.
//
// WHAT THE SERVER GUARANTEES, so this component does not have to pretend:
//   - the plaintext is returned EXACTLY ONCE, by the create call. The broker keeps a sha256
//     hash, so there is no endpoint that can show it again. Hence the one-time reveal panel.
//   - `last_seen` is in MEMORY on the broker and resets when it restarts, so "never" can mean
//     "not since the last restart". The column header says so rather than implying a log.
//   - the shared BROKER_AUTH_TOKEN is never a row here and cannot be revoked here. The gateway
//     authenticates WITH it to reach this very endpoint, so revoking it from this screen would
//     lock the console out of its own token manager on the first click.
function BrokerTab({ onRevealChange }: { onRevealChange: (on: boolean) => void }) {
  const [rows, setRows] = useState<BrokerToken[]>([])
  const [scopes, setScopes] = useState<string[]>(['inference', 'full'])
  const [sharedInUse, setSharedInUse] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<string | null>(null)

  const [nLabel, setNLabel] = useState('')
  const [nScope, setNScope] = useState('inference')
  // The one-time reveal. Held in component state only: never written anywhere, and cleared the
  // moment the admin dismisses it or leaves the tab.
  const [minted, setMinted] = useState<{ label: string; scope: string; token: string } | null>(null)
  const [copied, setCopied] = useState(false)

  const load = useCallback(async () => {
    try {
      const r = await platformApi.adminBrokerTokens()
      setRows(r.tokens)
      setScopes(r.scopes?.length ? r.scopes : ['inference', 'full'])
      setSharedInUse(!!r.shared_token_in_use)
      setErr('')
    }
    catch (ex) { setErr((ex as Error).message) }
    finally { setLoaded(true) }
  }, [])
  useEffect(() => { load() }, [load])
  // Tell the parent whether a one-time reveal is on screen, so it can guard a tab switch.
  // Cleared on unmount as well as on dismiss: if this tab goes away the reveal is already
  // gone, and a stale flag would block navigation with nothing to protect.
  useEffect(() => {
    onRevealChange(minted !== null)
    return () => onRevealChange(false)
  }, [minted, onRevealChange])

  // The SAME loss, one level up. Guarding the admin tabs stopped an in-app click discarding an
  // uncopied token and left the browser's own navigation wide open: a refresh, a back gesture
  // or closing the tab loses it just as completely, and there is no second copy anywhere.
  //
  // Registered ONLY while a reveal is live. A permanent beforeunload handler would nag on every
  // refresh of the admin page, which is how a warning gets dismissed reflexively and stops
  // being read. The browser shows its own generic wording -- Chrome ignores a custom string --
  // so the specific reason lives in the panel, not here.
  useEffect(() => {
    if (!minted) return
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [minted])

  const guard = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key); setErr('')
    try { await fn(); await load() }
    catch (ex) { setErr((ex as Error).message) }
    finally { setBusy(null) }
  }

  const create = () => guard('new', async () => {
    const out = await platformApi.adminBrokerTokenCreate({ label: nLabel.trim(), scope: nScope })
    setMinted({ label: out.label, scope: out.scope, token: out.token })
    setCopied(false)
    setNLabel('')
  })

  const seenLabel = (t: BrokerToken) => {
    if (!t.last_seen) return 'never'
    const mins = Math.max(0, Math.round((Date.now() / 1000 - t.last_seen) / 60))
    if (mins < 1) return 'just now'
    if (mins < 60) return `${mins} min ago`
    return `${Math.round(mins / 60)} hr ago`
  }

  if (!loaded) return <div className="card"><div className="empty">Loading…</div></div>

  return (
    <div className="admin-pane">
      {err && <div className="card error-banner">{err}</div>}

      {minted && (
        <div className="card" style={{ borderColor: 'var(--warning)' }}>
          <h3>Copy this token now</h3>
          <p className="muted" style={{ fontSize: 13 }}>
            <strong>This is the only time it is shown</strong>, and that is deliberate: the
            broker stores a sha256 hash rather than the token, so nothing in the system has a
            copy to show you. If <span className="mono">tokens.json</span> held the real values
            it would itself be the credential set. Lose this one and you revoke{' '}
            <strong>{minted.label}</strong> and generate another — the label frees up with it.
          </p>
          <div className="mono" style={{
            padding: '10px 12px', borderRadius: 6, background: 'var(--surface-2)',
            wordBreak: 'break-all', userSelect: 'all', marginBottom: 8,
          }}>{minted.token}</div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <Button onClick={() => {
              void navigator.clipboard?.writeText(minted.token).then(() => setCopied(true))
            }}>{copied ? 'Copied' : 'Copy'}</Button>
            <Button variant="ghost" onClick={() => setMinted(null)}>Done</Button>
            <span className="muted" style={{ fontSize: 12.5 }}>
              Set it as <span className="mono">BROKER_AUTH_TOKEN</span> on {minted.label}, or as
              the <span className="mono">api_key</span> of an OpenAI client pointed at
              <span className="mono"> /openai/v1</span>.
            </span>
          </div>
        </div>
      )}

      <div className="card">
        <h3>New token</h3>
        <p className="muted" style={{ fontSize: 13 }}>
          Label it after the machine it is for — that label is how you find the right row to
          revoke later.
        </p>
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <label style={{ display: 'grid', gap: 4 }}>
            <span className="muted" style={{ fontSize: 12.5 }}>Label</span>
            <input type="text" value={nLabel} placeholder="studio-desktop"
                   aria-label="Token label"
                   onChange={(e) => setNLabel(e.target.value)} />
          </label>
          <label style={{ display: 'grid', gap: 4 }}>
            <span className="muted" style={{ fontSize: 12.5 }}>Scope</span>
            <select value={nScope} aria-label="Token scope"
                    onChange={(e) => setNScope(e.target.value)}>
              {scopes.map((sc) => <option key={sc} value={sc}>{sc}</option>)}
            </select>
          </label>
          <Button disabled={!nLabel.trim() || busy === 'new'} onClick={create}>
            {busy === 'new' ? '…' : 'Generate'}
          </Button>
        </div>
        <p className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>
          <strong>inference</strong> — run work on the GPU and read state: chat, embeddings,
          media, voice, the OpenAI surface. What a remote workstation needs.<br />
          <strong>full</strong> — also change the platform: repoint a role, disable a model,
          load/unload/cancel, manage these tokens. Only for this box's own tooling.
        </p>
      </div>

      <div className="card">
        <h3>Tokens</h3>
        {rows.length === 0 ? (
          <div className="empty">
            No named tokens.{' '}
            {sharedInUse
              ? 'The shared BROKER_AUTH_TOKEN is in use and still works.'
              : 'The broker is currently OPEN — nothing is required to reach it.'}
          </div>
        ) : (
          <table className="admin-table">
            <thead>
              <tr>
                <th>Label</th><th>Scope</th><th>Token</th><th>Created</th>
                <th title="Tracked in memory by the broker; resets when it restarts, so 'never' can mean 'not since the last restart'">
                  Last seen
                </th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((t) => {
                const b = busy === t.id
                return (
                  <tr key={t.id}>
                    <td>{t.label}</td>
                    <td>
                      <Badge tone={t.scope === 'full' ? 'warning' : 'neutral'}>{t.scope}</Badge>
                    </td>
                    <td className="mono" title="Identifying prefix only; the rest is not stored">
                      {t.prefix}…
                    </td>
                    <td className="muted">{t.created?.slice(0, 10)}</td>
                    <td className="muted">{seenLabel(t)}</td>
                    <td>
                      {confirm === t.id ? (
                        <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                          <span className="muted" style={{ fontSize: 12.5 }}>
                            Revoke? That host stops working immediately.
                          </span>
                          <Button variant="ghost" size="sm" disabled={b}
                                  onClick={() => { setConfirm(null); void guard(t.id, () => platformApi.adminBrokerTokenRevoke(t.id)) }}>
                            {b ? '…' : 'yes, revoke'}
                          </Button>
                          <Button variant="ghost" size="sm" onClick={() => setConfirm(null)}>cancel</Button>
                        </span>
                      ) : (
                        <Button variant="ghost" size="sm" onClick={() => setConfirm(t.id)}>revoke</Button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
        {sharedInUse && (
          <p className="muted" style={{ fontSize: 12.5, marginTop: 10 }}>
            The shared <span className="mono">BROKER_AUTH_TOKEN</span> is also in use. It is not
            listed and cannot be revoked here: this console authenticates to the broker with it,
            so revoking it from this screen would lock you out of this screen. Change it in{' '}
            <span className="mono">deploy/.env</span> and the broker service environment.
          </p>
        )}
      </div>
    </div>
  )
}
