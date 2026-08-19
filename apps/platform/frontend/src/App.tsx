import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { AppShell, Button, ModelWidget, VoiceControls, platformApi } from '@web-core'
import type { AppEntry, Me, PlatformStatus, Theme, ThemeState } from '@web-core'
import Login from './Login'
import AdminPage from './AdminPage'
import { madhackGif } from './madhack'
import { DeskPet } from './deskpet/DeskPet'

// Federated app remotes, loaded at runtime from the gateway (served under /<app>/).
const EduSuiteModule = lazy(() => import('edu_suite/module'))
const IepModule = lazy(() => import('iep_app/module'))
const RecipeBookModule = lazy(() => import('recipe_book/module'))
const WorkstationModule = lazy(() => import('workstation/module'))
const TerminalFunModule = lazy(() => import('terminal_fun/module'))
const AiPlaygroundModule = lazy(() => import('ai_playground/module'))
const CoWorkerModule = lazy(() => import('co_worker/module'))
const GeminiCxModule = lazy(() => import('gemini_cx/module'))
const SmbPartnerModule = lazy(() => import('smb_partner/module'))

const STATUS_MS = 5000

// The palette list is owned here (matches the CSS): adding a palette is CSS + this
// line, no backend change. The server persists whatever palette string it's given.
const FRONTEND_PALETTES = [
  'indigo', 'slate', 'graphite', 'evergreen', 'ocean', 'ember', 'plum', 'rose', 'gold',
  'monokai', 'solarized', 'dracula', 'nord', 'tokyo', 'onedark', 'nightowl',
  'gruvbox', 'catppuccin', 'github', 'abyss', 'kimbie',
]

// Effective theme (palette + mode) is mirrored in a cookie so boot paints the right
// colors with no flash; the server (/apps -> me.theme) reconciles it right after.
function initialTheme(): { palette: string; mode: Theme } {
  const m = document.cookie.match(/(?:^|;\s*)platform-theme=([a-z]+):(light|dark|system)\b/)
  return { palette: m?.[1] ?? 'indigo', mode: (m?.[2] as Theme | undefined) ?? 'system' }
}
function currentApp(): string {
  return location.hash.replace(/^#\/?/, '') || 'edu-suite'
}

function ComingSoon({ label }: { label: string }) {
  return (
    <div className="module">
      <div className="card">
        <div className="empty">{label} is coming to the platform.</div>
      </div>
    </div>
  )
}

export default function App() {
  const [{ palette, mode }, setAppTheme] = useState(initialTheme)
  const [themeMeta, setThemeMeta] = useState<{
    palettes: string[]; hasOverride: boolean; defaultPalette: string; defaultMode: Theme
  }>({ palettes: [], hasOverride: false, defaultPalette: 'indigo', defaultMode: 'system' })
  const [active, setActive] = useState<string>(currentApp)
  const [status, setStatus] = useState<PlatformStatus | null>(null)
  const [busy, setBusy] = useState(false)

  // auth / per-user rail
  const [me, setMe] = useState<Me | null>(null)
  const [apps, setApps] = useState<AppEntry[] | null>(null)
  const [authChecked, setAuthChecked] = useState(false)

  useEffect(() => {
    const root = document.documentElement
    if (mode === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', mode)
    root.setAttribute('data-palette', palette)
    // ~1 year; path=/ so it applies platform-wide, SameSite=Lax for normal navigations.
    document.cookie = `platform-theme=${palette}:${mode}; path=/; max-age=31536000; samesite=lax`
  }, [palette, mode])

  // Apply the server's effective theme + remember the default / override for the menu.
  const applyTheme = useCallback((t: ThemeState) => {
    setAppTheme({ palette: t.palette, mode: t.mode })
    setThemeMeta({
      palettes: t.palettes,
      hasOverride: t.override !== null,
      defaultPalette: t.default.palette,
      defaultMode: t.default.mode,
    })
  }, [])

  useEffect(() => {
    const on = () => setActive(currentApp())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])

  // The rail + identity come from the server, filtered to what this user may see.
  const loadApps = useCallback(async () => {
    try {
      const r = await platformApi.apps()
      setApps(r.apps)
      setMe(r.user)
      if (r.user.theme) {
        // If the server doesn't yet recognize our locally-chosen palette (a new palette
        // whose backend hasn't been rebuilt), keep the local one instead of reverting.
        const cookiePal = initialTheme().palette
        const known = r.user.theme.palettes.includes(cookiePal)
        applyTheme(known ? r.user.theme : { ...r.user.theme, palette: cookiePal })
      }
    } catch {
      setApps(null)
      setMe(null)
    } finally {
      setAuthChecked(true)
    }
  }, [applyTheme])

  const onPalette = useCallback((p: string) => {
    setAppTheme((s) => ({ ...s, palette: p }))
    platformApi.setTheme({ palette: p }).then(applyTheme).catch(() => {})
  }, [applyTheme])
  const onMode = useCallback((m: Theme) => {
    setAppTheme((s) => ({ ...s, mode: m }))
    platformApi.setTheme({ mode: m }).then(applyTheme).catch(() => {})
  }, [applyTheme])
  const onSetDefaultTheme = useCallback(() => {
    platformApi.setDefaultTheme({ palette, mode }).then(applyTheme).catch(() => {})
  }, [applyTheme, palette, mode])
  const onResetTheme = useCallback(() => {
    platformApi.setTheme({ clear: true }).then(applyTheme).catch(() => {})
  }, [applyTheme])

  useEffect(() => {
    loadApps()
  }, [loadApps])

  // Hysteresis: a single slow/failed status poll (e.g. the health probe timing out
  // while the GPU is busy loading a model) should NOT flip the widget to "offline".
  // Keep the last-known-good status and only surface offline after 3 consecutive misses.
  const statusMisses = useRef(0)
  const refreshStatus = useCallback(async () => {
    try {
      const s = await platformApi.status()
      if (s && s.broker_reachable) {
        statusMisses.current = 0
        setStatus(s)
      } else {
        statusMisses.current += 1
        if (statusMisses.current >= 3) setStatus(s ?? null)
      }
    } catch {
      statusMisses.current += 1
      if (statusMisses.current >= 3) setStatus(null)
    }
  }, [])

  // Only poll the GPU status once we know who the user is (endpoints require auth).
  useEffect(() => {
    if (!me) return
    refreshStatus()
    const id = setInterval(refreshStatus, STATUS_MS)
    return () => clearInterval(id)
  }, [me, refreshStatus])

  const runModel = useCallback(
    async (fn: () => Promise<unknown>) => {
      setBusy(true)
      try {
        await fn()
        await refreshStatus()
      } finally {
        setBusy(false)
      }
    },
    [refreshStatus],
  )

  const logout = useCallback(async () => {
    await platformApi.logout().catch(() => {})
    setMe(null)
    setApps(null)
    setStatus(null)
  }, [])

  const selectApp = (id: string) => {
    location.hash = `#/${id}`
  }

  if (!authChecked) return null // brief: waiting on the first /apps call
  if (!me || !apps) return <Login onLoggedIn={loadApps} />

  const railApps = apps
  const isAdminView = active === 'admin' && me.is_admin
  const activeEntry = railApps.find((a) => a.id === active) ?? railApps[0] ?? null

  let content
  if (isAdminView) {
    content = <AdminPage meUsername={me.username} />
  } else if (activeEntry?.id === 'edu-suite') {
    content = (
      <Suspense
        fallback={
          <div className="module">
            <div className="card"><div className="empty">Loading EDU-Suite…</div></div>
          </div>
        }
      >
        <EduSuiteModule />
      </Suspense>
    )
  } else if (activeEntry?.id === 'iep') {
    content = (
      <Suspense
        fallback={
          <div className="module">
            <div className="card"><div className="empty">Loading IEP Present Levels…</div></div>
          </div>
        }
      >
        <IepModule />
      </Suspense>
    )
  } else if (activeEntry?.id === 'recipe-book') {
    content = (
      <Suspense
        fallback={
          <div className="module">
            <div className="card"><div className="empty">Loading Recipe Book…</div></div>
          </div>
        }
      >
        <RecipeBookModule />
      </Suspense>
    )
  } else if (activeEntry?.id === 'workstation' && activeEntry.status === 'ready') {
    content = (
      <Suspense
        fallback={
          <div className="module">
            <div className="card"><div className="empty">Loading Workstation…</div></div>
          </div>
        }
      >
        <WorkstationModule />
      </Suspense>
    )
  } else if (activeEntry?.id === 'terminal-fun') {
    content = (
      <Suspense
        fallback={
          <div className="module">
            <div className="card"><div className="empty">Loading Terminal Fun…</div></div>
          </div>
        }
      >
        <TerminalFunModule />
      </Suspense>
    )
  } else if (activeEntry?.id === 'ai-playground') {
    content = (
      <Suspense
        fallback={
          <div className="module">
            <div className="card"><div className="empty">Loading AI Playground…</div></div>
          </div>
        }
      >
        <AiPlaygroundModule />
      </Suspense>
    )
  } else if (activeEntry?.id === 'co-worker') {
    content = (
      <Suspense
        fallback={
          <div className="module">
            <div className="card"><div className="empty">Loading Co-Worker…</div></div>
          </div>
        }
      >
        <CoWorkerModule />
      </Suspense>
    )
  } else if (activeEntry?.id === 'smb-partner-enablement') {
    content = (
      <Suspense
        fallback={
          <div className="module">
            <div className="card"><div className="empty">Loading SMB Partner Enablement…</div></div>
          </div>
        }
      >
        <SmbPartnerModule />
      </Suspense>
    )
  } else if (activeEntry?.id === 'gemini-cx') {
    content = (
      <Suspense
        fallback={
          <div className="module">
            <div className="card"><div className="empty">Loading Gemini CX…</div></div>
          </div>
        }
      >
        <GeminiCxModule />
      </Suspense>
    )
  } else if (activeEntry) {
    content = <ComingSoon label={activeEntry.label} />
  } else {
    content = (
      <div className="module">
        <div className="card"><div className="empty">No apps have been assigned to your account yet.</div></div>
      </div>
    )
  }

  return (
    <>
    <AppShell
      apps={railApps}
      activeAppId={isAdminView ? '' : activeEntry?.id ?? ''}
      onSelectApp={selectApp}
      title={isAdminView ? 'Admin' : activeEntry?.label ?? 'Platform'}
      onBrandClick={me.is_admin ? () => selectApp('admin') : undefined}
      brandActive={isAdminView}
      brandTitle="Admin"
      iconOverrides={{
        // Gemini CX gets the Gemini "spark" — a four-pointed star with concave sides in
        // Google's blue-purple-rose gradient. The gradient id is namespaced because every
        // federated remote shares this document and a bare id would collide.
        'gemini-cx': (
          <svg width={22} height={22} viewBox="0 0 24 24" aria-hidden="true" style={{ display: 'block' }}>
            <defs>
              <linearGradient id="shellGeminiSpark" x1="0" y1="0" x2="24" y2="24" gradientUnits="userSpaceOnUse">
                <stop offset="0" stopColor="#4285F4" />
                <stop offset="0.52" stopColor="#9B72CB" />
                <stop offset="1" stopColor="#D96570" />
              </linearGradient>
            </defs>
            <path
              d="M12 0C12 6.627 6.627 12 0 12c6.627 0 12 5.373 12 12 0-6.627 5.373-12 12-12C17.373 12 12 6.627 12 0z"
              fill="url(#shellGeminiSpark)"
            />
          </svg>
        ),
        // AI Playground gets a literal playground (slide) illustration rather than an emoji.
        'ai-playground': (
          <svg width={22} height={22} viewBox="0 0 32 32" aria-hidden="true" style={{ display: 'block' }}>
            <rect x="1" y="26" width="30" height="5" rx="2.5" fill="#86cf68" />
            <path d="M18 14 C 11 16, 7 20, 6.5 26" fill="none" stroke="#ff9f43" strokeWidth="3" strokeLinecap="round" />
            <rect x="20" y="13" width="1.8" height="13" rx="0.9" fill="#2ab7c8" />
            <rect x="25.2" y="13" width="1.8" height="13" rx="0.9" fill="#2ab7c8" />
            <rect x="20" y="16" width="7" height="1.6" rx="0.8" fill="#2ab7c8" />
            <rect x="20" y="19.5" width="7" height="1.6" rx="0.8" fill="#2ab7c8" />
            <rect x="20" y="23" width="7" height="1.6" rx="0.8" fill="#2ab7c8" />
            <rect x="16" y="12" width="12" height="2.2" rx="1" fill="#e05572" />
            <path d="M15.5 12.5 L 22 6.5 L 28.5 12.5 Z" fill="#7b52c9" />
            <rect x="21.4" y="2.6" width="1.2" height="4.4" rx="0.6" fill="#5b6472" />
            <path d="M22.6 3 L 26.4 4.4 L 22.6 5.8 Z" fill="#e05572" />
          </svg>
        ),
      }}
      themeMenu={{
        palette,
        mode,
        palettes: FRONTEND_PALETTES,
        isAdmin: me.is_admin,
        hasOverride: themeMeta.hasOverride,
        defaultPalette: themeMeta.defaultPalette,
        defaultMode: themeMeta.defaultMode,
        onPalette,
        onMode,
        onSetDefault: onSetDefaultTheme,
        onReset: onResetTheme,
      }}
      topbarExtra={
        <>
          {/* FALLBACK dictation, mounted once. The primary path is a per-rail DictateButton
              chip, which hands the transcript to the rail's own state setter and so never
              writes into a field it does not own. This mic exists for rails that have not
              adopted a chip: it fakes typing into whichever field has focus, which works only
              because federated remotes share this document — and needs four workarounds to
              survive React's value tracker (see VoiceControls). Prefer the chip. */}
          <VoiceControls />
          <ModelWidget
            status={status}
            busy={busy}
            isAdmin={me.is_admin}
            onUnload={(m) => runModel(() => platformApi.unload(m))}
            onCancel={(seq) => runModel(() => platformApi.cancel(seq))}
          />
          <span className="whoami">{me.username}</span>
          {me.is_admin && (
            <img src={madhackGif} alt="madhack" title="madhack — since the early 90s"
              width={48} height={48} style={{ verticalAlign: 'middle' }} />
          )}
          <Button variant="ghost" size="sm" onClick={logout}>Log Out</Button>
        </>
      }
    >
      {content}
    </AppShell>
    {/* Desktop pet — mounted once here so it roams across every rail. Scripted, offline
        brain; the active rail + label drive its rail-aware lines. */}
    <DeskPet
      rail={isAdminView ? 'admin' : activeEntry?.id ?? ''}
      railLabel={isAdminView ? 'Admin' : activeEntry?.label ?? 'Platform'}
      username={me.username}
    />
    </>
  )
}
