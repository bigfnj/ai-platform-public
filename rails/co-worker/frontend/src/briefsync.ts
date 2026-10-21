// The Brief tab's data flow, as plain state and methods: which lens is shown, its brief, and the
// synthesis run being waited on. BriefView renders it and owns none of it.
//
// It was inline React state, and two recorded defects lived in how the pieces were wired:
//
//  - The deadlock. The status poll was cleared by the cleanup of an effect keyed on the lens,
//    and `busy` only went false inside that poll. Switch lens tabs mid-synthesis and the poll
//    died with busy still true: "Re-synthesize" disabled and "Synthesizing…" for good, until a
//    remount. Here the poll belongs to the controller, which lives as long as the tab does, and
//    changing the lens never touches it.
//  - The cooldown loop. The staleness auto-trigger ignored `started`, and the backend answers a
//    trigger inside its cooldown with 200 {"started": false}, so the page started polling a run
//    that did not exist, reloaded, saw the brief still stale, triggered again: about 60
//    requests a minute for the whole 15-minute window, the banner flickering every 3 s.
//
// No runtime imports, so `node --test` loads it directly (tests/brief-sync.test.js).
import type { Brief, BriefPass, BriefStatus, Source } from './types'

export type Lens = Source | 'all'

export interface RefreshAnswer { started: boolean; reason?: string }

export interface BriefSyncDeps {
  getBrief: (qs: string) => Promise<Brief>
  /** POST a refresh. Rejects on a non-2xx, with the HTTP status on the error as `status`. */
  postRefresh: (path: string) => Promise<RefreshAnswer>
  getStatus: () => Promise<BriefStatus>
  setInterval: (fn: () => void, ms: number) => unknown
  clearInterval: (id: unknown) => void
}

export interface BriefSyncState {
  lens: Lens
  brief: Brief | null
  /** Per-lane coverage. Only the merged brief carries it, but the tab counts have to stay
   *  visible while a lane is selected, so it is kept apart from `brief`. */
  passes: Record<string, BriefPass>
  loading: boolean
  err: string
  busy: boolean
  note: string
}

export const POLL_MS = 3000

export const initialBriefState = (): BriefSyncState => ({
  lens: 'all', brief: null, passes: {}, loading: true, err: '', busy: false, note: '',
})

const statusOf = (e: unknown) => (e as { status?: number })?.status
const message = (e: unknown) => String(e instanceof Error ? e.message : e)

export const lensQuery = (lens: Lens) => (lens === 'all' ? '' : `?source=${lens}`)

export class BriefSync {
  state: BriefSyncState = initialBriefState()
  private deps: BriefSyncDeps
  private onChange: (s: BriefSyncState) => void
  private timer: unknown = null
  private ticking = false
  private loadSeq = 0
  private disposed = false

  constructor(deps: BriefSyncDeps, onChange: (s: BriefSyncState) => void) {
    this.deps = deps
    this.onChange = onChange
  }

  private set(patch: Partial<BriefSyncState>): void {
    if (this.disposed) return
    this.state = { ...this.state, ...patch }
    this.onChange(this.state)
  }

  /** On mount: a run started before this tab was opened (or before a remount) is waited on,
   *  so the button is not offered while it is in flight. */
  attach(): void {
    this.deps.getStatus().then((s) => {
      if (this.disposed || !s.running) return
      this.set({ busy: true, note: 'A synthesis run is in progress…' })
      this.startPolling()
    }, () => { /* the brief load below reports an unreachable backend */ })
  }

  /** Show another lens. Loads its brief and leaves the synthesis poll alone. */
  setLens(lens: Lens): void {
    if (lens === this.state.lens) return
    this.set({ lens })
    this.load()
  }

  load(): void {
    const lens = this.state.lens
    const seq = ++this.loadSeq
    this.set({ loading: true, err: '' })
    this.deps.getBrief(lensQuery(lens)).then((b) => {
      // A lens switched again before this answered: the newer load owns the page.
      if (this.disposed || seq !== this.loadSeq) return
      this.set({ brief: b, loading: false, ...(b.passes ? { passes: b.passes } : {}) })
      // Only the merged view auto-refreshes: a lane refresh rebuilds just that lane, so
      // letting three tabs each fire one would leave the merge inconsistent.
      if (b.stale_source && lens === 'all' && !this.state.busy) this.autoRefresh()
    }, (e) => {
      if (this.disposed || seq !== this.loadSeq) return
      this.set({ err: message(e), loading: false })
    })
  }

  private autoRefresh(): void {
    this.set({ note: 'Harvest data changed since the last pass — re-synthesizing…' })
    this.deps.postRefresh('/api/brief/refresh?auto=1').then((r) => {
      if (this.disposed) return
      if (r.started) {
        this.set({ busy: true })
        this.startPolling()
      } else {
        // The cooldown. Nothing is running, so there is nothing to wait on: keep the stale
        // brief and say nothing more until the next load.
        this.set({ note: '' })
      }
    }, (e) => {
      if (this.disposed) return
      if (statusOf(e) === 409) {
        // A run is already in flight (a manual one): wait on it rather than leave the page
        // showing a brief it is about to replace.
        this.set({ busy: true })
        this.startPolling()
      } else {
        this.set({ note: '' })
      }
    })
  }

  /** The manual Re-synthesize: every lane and the merge on the Top 10 view, one lane on a lane
   *  view. Never subject to the cooldown. `label` names the lane in the banner. */
  refresh(label?: string): void {
    if (this.state.busy) return
    const lens = this.state.lens
    this.set({
      busy: true,
      note: lens === 'all'
        ? 'Synthesizing — one pass per source, then merging. This takes a few minutes.'
        : `Synthesizing the ${label ?? lens} pass…`,
    })
    this.deps.postRefresh(`/api/brief/refresh${lensQuery(lens)}`).then(() => {
      this.startPolling()
    }, (e) => {
      if (this.disposed) return
      if (statusOf(e) === 409) {
        this.set({ note: 'A synthesis run is already in progress — waiting for it.' })
        this.startPolling()
        return
      }
      this.set({ busy: false, note: message(e) })
    })
  }

  private startPolling(): void {
    if (this.disposed || this.timer !== null) return
    this.timer = this.deps.setInterval(() => { void this.tick() }, POLL_MS)
  }

  private stopPolling(): void {
    if (this.timer !== null) {
      this.deps.clearInterval(this.timer)
      this.timer = null
    }
  }

  /** One poll. A slow status read is not overlapped by the next tick. */
  async tick(): Promise<void> {
    if (this.ticking || this.disposed) return
    this.ticking = true
    try {
      const s = await this.deps.getStatus()
      if (this.disposed || s.running) return
      this.stopPolling()
      this.set({ busy: false, note: s.last_error ? `Synthesis failed: ${s.last_error}` : '' })
      this.load()
    } catch {
      if (this.disposed) return
      this.stopPolling()
      // Say so, rather than leave "Synthesizing…" up (reddened, since busy is now false) for a
      // run nobody is watching any more.
      this.set({ busy: false,
                 note: 'Lost track of the synthesis run (its status did not answer). Reload to check.' })
    } finally {
      this.ticking = false
    }
  }

  get polling(): boolean {
    return this.timer !== null
  }

  dispose(): void {
    this.disposed = true
    this.stopPolling()
  }
}
