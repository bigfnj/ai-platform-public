// The Voice Chat session, as plain state and methods: the desktop VoiceChat and the mobile Chat
// render it, and neither owns any of the logic below. They used to own a copy each, and the
// copies were where the defects lived:
//
//  - the mobile mic could not stop a recording: `if (hearing || ...) return` ran before the
//    `recorder.current` check, and onStart had already set hearing, so every server-STT
//    recording ran its full 30 s and then submitted itself;
//  - leaving the tab mid-answer left the stream, its 12 s watchdog and the read-aloud running
//    with no panel on screen (desktop), and the mobile build dropped the stream's cancel entirely;
//  - a browser recognition could not be cancelled (listen()'s handle was discarded), and one
//    that finished after the tab closed still submitted;
//  - a recording transcribed after the tab closed still submitted and spoke its answer.
//
// `dispose()` is the unmount: after it, no handler touches state, nothing is submitted and
// nothing starts speaking. Its one runtime import is askflow.ts, which has none, so
// `node --test` loads both directly (tests/voicesession.test.js) with fakes for every effect.
import type { AskDeps } from './askflow'
import type { Listener, Recorder, SttAvailability } from './voice'
import type { Turn, VoicePayload } from './types'
import { startAsk } from './askflow.ts'

export interface VoiceDeps extends AskDeps {
  transcribe: (audio_b64: string, suffix: string) => Promise<{ text: string }>
  record: (handlers: {
    onReady: (audio_b64: string, suffix: string) => void
    onError?: (detail: string) => void
    onStart?: () => void
  }) => Promise<Recorder | null>
  listen: (handlers: {
    onResult: (transcript: string, final: boolean) => void
    onError?: (detail: string) => void
    onEnd?: () => void
    processLocally?: boolean
  }) => Listener | null
  /** What the browser can recognise ON-DEVICE (voice.browserSttAvailability). */
  sttAvailability: () => Promise<SttAvailability>
  /** Whether a SpeechRecognition constructor exists at all (voice.canListen). */
  hasRecognition: () => boolean
  /** The platform's dictation policy: web-core dictate_policy.chooseSttPath, injected so this
   *  module keeps no runtime import a test cannot load. */
  chooseSttPath: (c: { hasCtor: boolean; availability: SttAvailability; allowCloudStt?: boolean })
    => 'ondevice' | 'cloud' | 'broker'
  speak: (payload: VoicePayload | undefined, onEnd?: () => void) => void
  stopSpeaking: () => void
}

export interface VoiceState {
  turns: Turn[]
  draft: string
  busy: boolean        // a question is being answered
  hearing: boolean     // a recording or a browser recognition is live
  recording: boolean   // ...and it is a recording, which only the user can end
  thinking: boolean    // a recording is being transcribed
  speaking: boolean    // an answer is being read aloud
  err: string          // the microphone's last problem, if any
}

/**
 * The opt-in web-core's DictateButton takes as `allowCloudStt`: whether a field may send the
 * microphone to the browser's CLOUD recogniser when on-device recognition is not ready. Off,
 * and the reason this exists is that it used to be on by omission: with the media worker down
 * this rail fell back to plain SpeechRecognition, which on most browsers is Google's or
 * Microsoft's servers, bypassing the local-by-default policy every other rail follows.
 */
export const ALLOW_CLOUD_STT = false

/** What the mic says when no local recogniser is available, instead of using a cloud one. */
export const NO_LOCAL_STT =
  'Speaking needs local transcription: the broker\'s speech-to-text is not answering and this ' +
  'browser has no on-device speech pack, and this rail does not send your voice to a cloud ' +
  'recogniser. Type the question instead.'

export const initialVoiceState = (): VoiceState => ({
  turns: [], draft: '', busy: false, hearing: false, recording: false, thinking: false,
  speaking: false, err: '',
})

const message = (e: unknown, fallback: string) =>
  String((e as { message?: string })?.message || fallback)

export class VoiceSession {
  state: VoiceState = initialVoiceState()
  private deps: VoiceDeps
  private onChange: (s: VoiceState) => void
  private nextId = 1
  private abortAsk: (() => void) | null = null
  private recorder: Recorder | null = null
  private listener: Listener | null = null
  private starting = false
  private disposed = false

  constructor(deps: VoiceDeps, onChange: (s: VoiceState) => void) {
    this.deps = deps
    this.onChange = onChange
  }

  private set(patch: Partial<VoiceState>): void {
    if (this.disposed) return
    this.state = { ...this.state, ...patch }
    this.onChange(this.state)
  }

  private patchTurn(id: number, fn: (t: Turn) => Turn): void {
    this.set({ turns: this.state.turns.map((t) => (t.id === id ? fn(t) : t)) })
  }

  setDraft(text: string): void {
    this.set({ draft: text })
  }

  /** Ask `text`. `spoken` turns get the short spoken prompt and their answer read aloud. */
  submit(text: string, spoken: boolean): void {
    const q = text.trim()
    // `disposed` is what stops a transcription or a recognition that finished after the tab
    // closed from asking (and then speaking) into a panel that is gone.
    if (!q || this.state.busy || this.disposed) return
    const id = this.nextId++
    this.set({
      turns: [...this.state.turns,
              { id, question: q, answer: '', citations: [], grounded: false, streaming: true }],
      draft: '', busy: true, err: '',
    })
    this.abortAsk = startAsk(this.deps, { question: q, speak: spoken }, {
      onCitations: (citations, grounded) => this.patchTurn(id, (t) => ({ ...t, citations, grounded })),
      onToken: (tok) => this.patchTurn(id, (t) => ({ ...t, answer: t.answer + tok })),
      onDone: ({ answer, voice, citations, grounded }) => {
        this.abortAsk = null
        this.patchTurn(id, (t) => ({ ...t, answer, streaming: false,
                                     ...(citations ? { citations } : {}),
                                     ...(grounded !== undefined ? { grounded } : {}) }))
        this.set({ busy: false })
        if (spoken && voice && !this.disposed) {
          this.set({ speaking: true })
          this.deps.speak(voice, () => this.set({ speaking: false }))
        }
      },
      onError: (detail) => {
        this.abortAsk = null
        this.patchTurn(id, (t) => ({ ...t, streaming: false, error: detail }))
        this.set({ busy: false })
      },
    })
  }

  /** The mic control. One press starts; the next press ends what the first started: a
   *  recording is stopped AND sent, a browser recognition is cancelled. The recording check
   *  comes FIRST, before anything that `hearing` gates, because a recording sets hearing. */
  mic(serverStt: boolean): void {
    if (this.disposed) return
    if (this.recorder) { this.stopRecording(); return }
    if (this.listener) { this.cancelListening(); return }
    if (this.starting || this.state.busy || this.state.thinking) return
    this.deps.stopSpeaking()
    this.set({ err: '', speaking: false })
    if (serverStt) void this.startRecording()
    else void this.startBrowser()
  }

  /** End the recording and send it for transcription. */
  stopRecording(): void {
    const r = this.recorder
    this.recorder = null
    r?.stop()
  }

  /** End a browser recognition and drop what it heard. */
  cancelListening(): void {
    const l = this.listener
    this.listener = null
    l?.abort()
    this.set({ hearing: false })
  }

  stopVoice(): void {
    this.deps.stopSpeaking()
    this.set({ speaking: false })
  }

  private async startRecording(): Promise<void> {
    this.starting = true
    let r: Recorder | null = null
    try {
      r = await this.deps.record({
        onStart: () => this.set({ hearing: true, recording: true }),
        onError: (detail) => {
          this.recorder = null
          this.set({ hearing: false, recording: false, err: detail })
        },
        onReady: (audio_b64, suffix) => { void this.transcribed(audio_b64, suffix) },
      })
    } finally {
      this.starting = false
    }
    // The tab closed while getUserMedia was asking for permission: release the mic it got.
    if (this.disposed) { r?.cancel(); return }
    this.recorder = r
  }

  private async transcribed(audio_b64: string, suffix: string): Promise<void> {
    this.recorder = null
    this.set({ hearing: false, recording: false, thinking: true })
    try {
      // A tab closed during transcription asks nothing: submit() refuses once disposed.
      const { text } = await this.deps.transcribe(audio_b64, suffix)
      if (!text) { this.set({ err: 'nothing was heard — try again' }); return }
      this.set({ draft: text })
      this.submit(text, true)
    } catch (e) {
      this.set({ err: message(e, 'transcription failed') })
    } finally {
      this.set({ thinking: false })
    }
  }

  /** The browser recognition path, under the platform's dictation policy: on-device when the
   *  browser has the pack, the cloud only on ALLOW_CLOUD_STT, and otherwise nothing, said so. */
  private async startBrowser(): Promise<void> {
    this.starting = true
    let availability: SttAvailability
    try {
      availability = await this.deps.sttAvailability()
    } catch {
      availability = 'unavailable'
    } finally {
      this.starting = false
    }
    if (this.disposed) return
    const path = this.deps.chooseSttPath({
      hasCtor: this.deps.hasRecognition(), availability, allowCloudStt: ALLOW_CLOUD_STT })
    // 'broker' is the policy's local floor, and the broker's speech-to-text is exactly what is
    // down when this path is taken: there is no local recogniser left to use.
    if (path === 'broker') {
      this.set({ err: NO_LOCAL_STT })
      return
    }
    this.startListening(path === 'ondevice')
  }

  private startListening(processLocally: boolean): void {
    this.set({ hearing: true, recording: false })
    // Results count only while THIS recognition is the live one: a cancelled one (or one
    // superseded by a newer press) can still deliver a late final result, and that must not
    // submit. listen()'s own abort drops them too; this does not lean on it.
    let l: Listener | null = null
    const live = () => !this.disposed && this.listener === l
    l = this.deps.listen({
      onResult: (t, final) => {
        if (!live()) return
        this.set({ draft: t })
        if (final) {
          this.listener = null
          this.submit(t, true)
        }
      },
      onEnd: () => {
        if (this.listener === l) this.listener = null
        this.set({ hearing: this.listener !== null })
      },
      onError: (detail) => {
        if (!live()) return
        this.listener = null
        this.set({ hearing: false, err: detail })
      },
      processLocally,
    })
    if (!l) { this.set({ hearing: false }); return }
    this.listener = l
  }

  /** The unmount. Ends the answer in flight (and its watchdog), the recording, the browser
   *  recognition and this session's read-aloud; after it no callback changes anything. */
  dispose(): void {
    if (this.disposed) return
    const speaking = this.state.speaking
    this.disposed = true
    this.abortAsk?.()
    this.abortAsk = null
    this.recorder?.cancel()
    this.recorder = null
    this.listener?.abort()
    this.listener = null
    if (speaking) this.deps.stopSpeaking()
  }
}
