// One "Read aloud" button's state, as plain state and methods, so the parts that went wrong can
// be tested without a browser (tests/voice-flows.test.js).
//
// What went wrong in the inline version: `toggle` awaited the synthesis request and then spoke,
// with nothing asking whether the button still existed. Leave the package view (or the rail)
// while Kokoro is synthesising and the answer started playing a second later, from a page
// that was gone. And its unmount called stopSpeaking() unconditionally, so removing one button
// cut off audio another button had started.
//
// No runtime imports, so `node --test` loads it directly.
import type { VoicePayload } from './types'

export interface ReadAloudDeps {
  speakText: (text: string) => Promise<VoicePayload>
  speak: (payload: VoicePayload | undefined, onEnd?: () => void) => void
  stopSpeaking: () => void
}

export interface ReadAloudState { on: boolean; loading: boolean }

export class ReadAloudControl {
  state: ReadAloudState = { on: false, loading: false }
  private deps: ReadAloudDeps
  private onChange: (s: ReadAloudState) => void
  private disposed = false
  private generation = 0

  constructor(deps: ReadAloudDeps, onChange: (s: ReadAloudState) => void) {
    this.deps = deps
    this.onChange = onChange
  }

  private set(patch: Partial<ReadAloudState>): void {
    if (this.disposed) return
    this.state = { ...this.state, ...patch }
    this.onChange(this.state)
  }

  async toggle(text: string): Promise<void> {
    if (this.disposed) return
    if (this.state.on) {
      this.deps.stopSpeaking()
      this.set({ on: false })
      return
    }
    if (this.state.loading) return
    const mine = ++this.generation
    this.set({ loading: true })
    let payload: VoicePayload
    try {
      payload = await this.deps.speakText(text)
    } catch {
      // The broker is unavailable: the browser's own speech, so the button never does nothing.
      payload = { mode: 'browser', text, lang: 'en-US' }
    }
    // Gone, or stopped and pressed again, while the request was in flight: say nothing.
    if (this.disposed || mine !== this.generation) return
    this.set({ loading: false, on: true })
    this.deps.speak(payload, () => { if (mine === this.generation) this.set({ on: false }) })
  }

  /** The unmount. Stops the audio only if THIS button started it and it is still playing. */
  dispose(): void {
    if (this.disposed) return
    const playing = this.state.on
    this.disposed = true
    this.generation++
    if (playing) this.deps.stopSpeaking()
  }
}
