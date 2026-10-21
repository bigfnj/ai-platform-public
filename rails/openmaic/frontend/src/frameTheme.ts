// Telling the framed OpenMAIC app which light/dark mode the shell is in. No imports, so plain node
// can test it (frontend/tests/frame-theme.test.js).
//
// THE DEFECT. The shell publishes its mode as data-theme on its own <html> (App.tsx), and the
// framed app never looks there. Upstream's theme hook (OpenMAIC lib/hooks/use-theme.tsx) reads
// localStorage 'theme' ONCE, on mount, falls back to the OS prefers-color-scheme, and from then on
// only toggles the 'dark' class on its own <html>. So a shell set to Dark on a light OS framed a
// white app, and the reverse framed a near-black one.
//
// THE HAND-OFF. The frame is same-origin (the rail proxies it under /openmaic/api/app/), so this
// side can reach the app without touching the upstream code, at three moments:
//   * before the app mounts: localStorage 'theme', which is the value its hook starts from. The
//     shell's own choice is passed through as it is, 'system' included, so on System the app
//     follows the OS live through its own media-query listener.
//   * before the frame's document first paints: the 'dark' class on that document's <html>
//     (watchFrameDocuments), held there until the app applies the same mode itself
//     (holdFrameClass). Without it the server-rendered page painted light for as long as the app
//     took to hydrate (the first 2-3 s of every load, under a dark shell).
//   * when the shell's mode changes with the app already up: the app's OWN setTheme, found in its
//     React tree (findAppTheme), so its state, its theme button's icon and its stored choice move
//     with the colours. Toggling the class alone left the button on the icon it mounted with. The
//     class is still toggled too, and is all that happens when the app's theme context cannot be
//     found (not hydrated yet, or reshaped by an upstream change).
// Only the mode crosses. The palette does not: the app has its own stylesheet, which this rail
// deliberately leaves alone (theme.css).

export type ShellMode = 'light' | 'dark' | 'system'

/** The key upstream's theme hook reads (OpenMAIC lib/hooks/use-theme.tsx). Nothing in this repo
 *  uses the same key, so writing it on this origin cannot move another rail's setting. */
export const FRAME_THEME_KEY = 'theme'

const MODES: readonly string[] = ['light', 'dark', 'system']

/** The shell's mode from the data-theme attribute App.tsx sets: absent means System. */
export function shellMode(dataTheme: string | null | undefined): ShellMode {
  return dataTheme === 'light' || dataTheme === 'dark' ? dataTheme : 'system'
}

/** What the mode looks like on screen, given whether the OS prefers dark. */
export function resolvedMode(mode: ShellMode, prefersDark: boolean): 'light' | 'dark' {
  return mode === 'system' ? (prefersDark ? 'dark' : 'light') : mode
}

interface StorageLike { setItem(key: string, value: string): void }
interface RootLike { classList: { toggle(token: string, force?: boolean): boolean } }

/** The framed app's own theme API, as its ThemeProvider hands it to its components
 *  (lib/hooks/use-theme.tsx: `{ theme, setTheme, resolvedTheme }`). */
export interface AppTheme {
  theme: ShellMode
  resolvedTheme: 'light' | 'dark'
  setTheme(theme: ShellMode): void
}

/** Whether a React context value is the framed app's theme context: exactly the shape above. */
export function isAppTheme(v: unknown): v is AppTheme {
  if (!v || typeof v !== 'object') return false
  const o = v as Record<string, unknown>
  return typeof o.setTheme === 'function' && typeof o.theme === 'string' && MODES.includes(o.theme)
    && (o.resolvedTheme === 'light' || o.resolvedTheme === 'dark')
}

interface FiberLike { return?: FiberLike | null; memoizedProps?: { value?: unknown } | null }
interface ElementListLike { readonly length: number; readonly [i: number]: object }
interface BodyLike { querySelectorAll(selectors: string): ElementListLike }

/** React keeps each element it rendered in an own property named `__reactFiber$<random suffix>`,
 *  pointing at that element's fiber. Measured on React 18.3 (railcheck om-frame-boot, the rail's
 *  own copy) and 19.2 (the version upstream ships). */
function fiberOf(el: object): FiberLike | null {
  for (const key of Object.keys(el)) {
    if (key.startsWith('__reactFiber$')) return (el as Record<string, FiberLike>)[key] ?? null
  }
  return null
}

/** The framed app's theme context, found from its DOM: walk up from the elements React rendered
 *  in `body` to the context provider whose value is an AppTheme. Upstream's app/layout.tsx wraps
 *  the whole body in its ThemeProvider, so a short climb from almost any of them reaches it;
 *  `maxElements` bounds how many are tried, for an element mounted outside the provider (a
 *  portal) coming first. null when nothing is found: the app has not hydrated, or its context
 *  changed shape. A fiber can be the previous render's copy, so the value found can be one render
 *  old; its setTheme is still the app's (it calls the state setter, which React keeps for the life
 *  of the component), so calling it is safe. Its theme field is not read for that reason. */
export function findAppTheme(body: BodyLike | null | undefined, maxElements = 50,
  maxDepth = 400): AppTheme | null {
  try {
    const els = body?.querySelectorAll('*')
    if (!els) return null
    let tried = 0
    for (let i = 0; i < els.length && tried < maxElements; i++) {
      let f = fiberOf(els[i])
      if (!f) continue
      tried++
      for (let depth = 0; f && depth < maxDepth; depth++, f = f.return ?? null) {
        const value = f.memoizedProps?.value
        if (isAppTheme(value)) return value
      }
    }
  } catch {
    // A frame mid-navigation can hand back a document that is going away.
  }
  return null
}

/** Hand the shell's mode to the frame. `storage`, `frameRoot` and `app` may each be null (blocked
 *  storage, a frame not yet created or not same-origin, an app not yet hydrated); each part is then
 *  skipped and the others still apply. Never throws: a theme hand-off must not take the rail down
 *  with it. */
export function syncFrameTheme(
  mode: ShellMode,
  prefersDark: boolean,
  storage: StorageLike | null,
  frameRoot: RootLike | null,
  app: AppTheme | null = null,
): void {
  try {
    storage?.setItem(FRAME_THEME_KEY, mode)
  } catch {
    // Storage can throw (quota, blocked site data). The class below still fixes the open frame.
  }
  try {
    frameRoot?.classList.toggle('dark', resolvedMode(mode, prefersDark) === 'dark')
  } catch {
    // A frame mid-navigation can hand back a document that is going away.
  }
  try {
    // The app's own setter: its state, its icon and its stored choice follow. React skips the
    // re-render when the value is unchanged, so calling it on every hand-off is cheap.
    app?.setTheme(mode)
  } catch {
    // An app whose setter throws keeps the class above, which is what it had before this.
  }
}

/** The frame's <html> as the document watch and the hold read it. */
export interface ClassRootLike {
  classList: { toggle(token: string, force?: boolean): boolean; contains(token: string): boolean }
}

/** A frame document as this file reads it. */
export interface FrameDocLike {
  readonly URL: string
  readonly documentElement: ClassRootLike | null
  readonly defaultView?: { addEventListener(type: string, fn: () => void, opts?: { once?: boolean }): void } | null
}

export interface FrameWatchEnv {
  /** One call per animation frame of the host page: the host's frame callbacks run before the
   *  same-origin frame's style and paint in that rendering update. */
  raf(fn: () => void): number
  caf(id: number): void
  now(): number
}

/** How long one watch looks for the next document before giving up, in ms. A frame that takes
 *  longer than this to commit its navigation is not loading. */
export const WATCH_MS = 30000

/** Call `onDocument` once for each NEW document the frame loads, as soon as that document's <html>
 *  exists, which is before it can paint: the app's server-rendered page waits for its stylesheets,
 *  and the check runs in every animation frame of the host page until then. After a document is
 *  found the watch sleeps until that document is unloaded (its pagehide), then looks for the next.
 *  Returns the stop. */
export function watchFrameDocuments(
  frame: () => { readonly contentDocument: FrameDocLike | null } | null,
  onDocument: (doc: FrameDocLike) => void,
  env: FrameWatchEnv,
): () => void {
  let seen: FrameDocLike | null = null
  let id = 0
  let until = 0
  let stopped = false
  const tick = () => {
    id = 0
    if (stopped) return
    let doc: FrameDocLike | null = null
    try {
      doc = frame()?.contentDocument ?? null
    } catch {
      doc = null
    }
    if (doc && doc !== seen && doc.URL !== 'about:blank' && doc.documentElement) {
      seen = doc
      try { onDocument(doc) } catch { /* the watch outlives one bad hand-off */ }
      try { doc.defaultView?.addEventListener('pagehide', arm, { once: true }) } catch { /* gone */ }
      return
    }
    if (env.now() < until) id = env.raf(tick)
  }
  function arm() {
    if (stopped) return
    until = env.now() + WATCH_MS
    if (!id) id = env.raf(tick)
  }
  arm()
  return () => {
    stopped = true
    if (id) env.caf(id)
    id = 0
  }
}

/** The longest a hold lasts, in ms, for an app that never applies a mode of its own. */
export const HOLD_MS = 20000

export interface HoldEnv {
  /** Watch `root`'s class attribute; `onRecords` gets how many mutation records arrived. */
  observe(root: ClassRootLike, onRecords: (count: number) => void): { disconnect(): void }
  setTimeout(fn: () => void, ms: number): unknown
  clearTimeout(id: unknown): void
}

/** Put the 'dark' class on a new frame document's <html> as `wantDark()` says, and keep it there
 *  until the app applies the same mode itself. Returns the release.
 *
 *  WHY A HOLD. Upstream's ThemeProvider starts from the state 'system' with the OS taken as light
 *  (use-theme.tsx's initial useState values), so its first render removes the class, and only the
 *  re-render after it has read storage puts the stored mode back. Measured against upstream's real
 *  hook (React 19.2, Next 16.3) without the hold: two painted light frames at hydration in one run
 *  of six under a dark mode; with it, none in six. The hold puts the class back the
 *  moment the app takes it away (a MutationObserver callback runs before the next paint), and lets
 *  go as soon as a change the app made leaves the class where the shell wants it: from then on the
 *  app's own state agrees, and a later change, the user's own pick in the app's menu included, is
 *  the app's to make. HOLD_MS bounds a hold the app never ends. */
export function holdFrameClass(root: ClassRootLike, wantDark: () => boolean, env: HoldEnv): () => void {
  let own = 0           // records our own corrections will produce, not to be read as the app's
  let done = false
  const correct = () => {
    const want = wantDark()
    if (root.classList.contains('dark') !== want) {
      own++             // toggle with a force records a mutation only when it changes the class
      root.classList.toggle('dark', want)
    }
  }
  let observer: { disconnect(): void } | null = null
  let timer: unknown = null
  const release = () => {
    if (done) return
    done = true
    observer?.disconnect()
    if (timer !== null) env.clearTimeout(timer)
  }
  try {
    // Observing first: the first correction's own record must be delivered, or the count above
    // would swallow the app's first change as if it were ours.
    observer = env.observe(root, (count) => {
      if (done) return
      const mine = Math.min(own, count)
      own -= mine
      if (count - mine === 0) return              // only our own corrections
      if (root.classList.contains('dark') === wantDark()) release()
      else correct()
    })
    correct()
    timer = env.setTimeout(release, HOLD_MS)
  } catch {
    release()           // a document that is going away; the class above is all it gets
  }
  return release
}
