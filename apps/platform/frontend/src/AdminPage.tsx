// Admin console. Three categories (tabs):
//   Rails  — the LLM each rail loads for a task; per-rail model roles, repointed live.
//   Models — the workstation model pool: every installed model, its In-Use/Loaded state, and
//            Enable/Disable (reversible) + Delete (irreversible, blocked while In-Use).
//   Users  — manage users, their role, and which apps each may see (delegation-aware).
// Admin-only; the gateway's /api/platform/admin/* endpoints enforce the same rules server-side.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Badge, Button, platformApi } from '@web-core'
import type { AdminUser, AppEntry, MediaOption, ModelOption, ModelPoolEntry, RailModelSlot, RailModels, RailSchedules, RailsSettings, Recurrence } from '@web-core'

type Tab = 'users' | 'rails' | 'models' | 'schedule'

export default function AdminPage({ meUsername }: { meUsername: string }) {
  const [tab, setTab] = useState<Tab>('rails')
  return (
    <div className="module">
      <div className="admin-tabs" role="tablist" aria-label="Admin sections">
        <button className={`admin-tab ${tab === 'rails' ? 'on' : ''}`} role="tab"
                aria-selected={tab === 'rails'} onClick={() => setTab('rails')}>Rails</button>
        <button className={`admin-tab ${tab === 'models' ? 'on' : ''}`} role="tab"
                aria-selected={tab === 'models'} onClick={() => setTab('models')}>Models</button>
        <button className={`admin-tab ${tab === 'schedule' ? 'on' : ''}`} role="tab"
                aria-selected={tab === 'schedule'} onClick={() => setTab('schedule')}>Schedule</button>
        <button className={`admin-tab ${tab === 'users' ? 'on' : ''}`} role="tab"
                aria-selected={tab === 'users'} onClick={() => setTab('users')}>Users</button>
      </div>
      {tab === 'rails' ? <RailsTab />
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

function ModelsTab() {
  const [models, setModels] = useState<ModelPoolEntry[]>([])
  const [err, setErr] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [confirmDel, setConfirmDel] = useState<string | null>(null)

  const load = useCallback(async () => {
    try { setModels((await platformApi.adminModels()).models); setErr('') }
    catch (ex) { setErr((ex as Error).message) }
    finally { setLoaded(true) }
  }, [])
  useEffect(() => { load() }, [load])

  const guard = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name); setErr('')
    try { await fn(); await load() }
    catch (ex) { setErr((ex as Error).message) }
    finally { setBusy(null) }
  }
  const toggle = (m: ModelPoolEntry) => guard(m.name, () => platformApi.adminModelToggle(m.name, !m.enabled))
  const del = (m: ModelPoolEntry) => guard(m.name, async () => { await platformApi.adminModelDelete(m.name); setConfirmDel(null) })

  if (!loaded) return <div className="card"><div className="empty">Loading…</div></div>

  return (
    <>
      {err && <div className="card" style={{ marginBottom: 16 }}><p className="error-line" style={{ margin: 0 }}>{err}</p></div>}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>Model pool</h3>
        <p className="muted" style={{ margin: 0 }}>
          Every model installed on the workstation. <b>Disable</b> hides a model from the rail
          pickers and frees its VRAM — reversible, and any rail already pointed at it keeps working.
          <b> Delete</b> removes it from disk for good and is blocked while a rail depends on it.
        </p>
      </div>

      <div className="card">
        <table className="admin-table">
          <thead>
            <tr><th>Model</th><th>Class</th><th>Status</th><th></th></tr>
          </thead>
          <tbody>
            {models.map((m) => {
              const b = busy === m.name
              const confirming = confirmDel === m.name
              return (
                <tr key={m.name} style={m.enabled ? undefined : { opacity: 0.6 }}>
                  <td>
                    <code>{m.name}</code>
                    {m.parameter_size && <span className="muted"> · {m.parameter_size}</span>}
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
            })}
          </tbody>
        </table>
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// Rails tab: per-rail model selection.
// ---------------------------------------------------------------------------

function RailsTab() {
  const [rails, setRails] = useState<RailModels[]>([])
  const [models, setModels] = useState<ModelOption[]>([])
  const [media, setMedia] = useState<MediaOption[]>([]) // image backends for image slots
  const [sel, setSel] = useState<Record<string, string>>({}) // role -> pending selection
  const [err, setErr] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [busyRole, setBusyRole] = useState<string | null>(null)
  const [savedRole, setSavedRole] = useState<string | null>(null)

  const apply = useCallback((view: RailsSettings) => {
    setRails(view.rails)
    setModels(view.models)
    setMedia(view.media)
    // Reset each slot's pending selection to its now-current pattern.
    const next: Record<string, string> = {}
    for (const r of view.rails) for (const s of r.slots) next[s.role] = s.pattern
    setSel(next)
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

  const setRoleModel = async (role: string, model: string) => {
    setBusyRole(role)
    setErr('')
    try {
      apply(await platformApi.adminSetRailModel(role, model))
      setSavedRole(role)
      setTimeout(() => setSavedRole((r) => (r === role ? null : r)), 2200)
    } catch (ex) {
      setErr((ex as Error).message)
    } finally {
      setBusyRole(null)
    }
  }

  const onApply = (slot: RailModelSlot) => {
    const model = sel[slot.role]
    if (!model || model === slot.pattern) return
    void setRoleModel(slot.role, model)
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
              // Options depend on the slot kind: image slots offer the media backends;
              // a vision slot only offers vision-capable models; chat offers any generative model.
              const opts: { value: string; text: string }[] =
                s.kind === 'image'
                  ? media.map((m) => ({ value: m.name, text: m.note ? `${m.label} — ${m.note}` : m.label }))
                  : (s.kind === 'vision' ? models.filter((m) => m.vision) : models)
                      .map((m) => ({ value: m.name, text: m.name + (m.parameter_size ? ` · ${m.parameter_size}` : '') }))
              const isWild = /[*?[\]]/.test(s.pattern)
              const known = opts.some((o) => o.value === s.pattern)
              const current = sel[s.role] ?? s.pattern
              const changed = current !== s.pattern
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
                    <select
                      value={current}
                      disabled={busy}
                      onChange={(e) => setSel((c) => ({ ...c, [s.role]: e.target.value }))}
                    >
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
