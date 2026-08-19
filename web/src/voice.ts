// Shared voice primitives: speech OUT (Kokoro via the gateway) and speech IN (faster-whisper
// via the gateway). Used by VoiceControls (dictation, in the shell top bar) and SpeakButton
// (read-aloud, mounted by a rail).
//
// Both directions go through /api/platform/*, never to the broker directly: the gateway holds
// the broker token and enforces the session, so no rail needs either.
//
// ─────────────────────────────────────────────────────────────────────────────────────────────
// THERE IS DELIBERATELY NO BROWSER SpeechRecognition FALLBACK.
//
// The Web Speech *recognition* API is not on-device in Chrome or Edge: it streams microphone
// audio to a cloud service. A "graceful fallback" to it would mean that whenever the broker is
// down, everything anyone dictates — into a rail that may be handling client or personal data —
// is silently uploaded to a third party. Nothing in the UI would say so, because from the user's
// side it just kept working.
//
// So when the broker is unavailable, dictation reports itself unavailable. That is the honest
// failure and the safe one.
//
// speechSynthesis (OUTPUT) is a different matter and is fine as a fallback: it is on-device, and
// the text being spoken is already rendered on screen. See speakLocal().
// ─────────────────────────────────────────────────────────────────────────────────────────────

export interface TtsResult {
  audio_b64: string
  sample_rate: number
  voice: string
  lang: string
}

export interface SttResult {
  text: string
  language: string
  duration: number
  model: string
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    // Read the body once — a second read throws "body stream already read" and masks the error.
    const raw = await res.text().catch(() => '')
    let detail = raw
    try {
      const parsed = JSON.parse(raw)
      detail = typeof parsed?.detail === 'string' ? parsed.detail : JSON.stringify(parsed)
    } catch {
      /* not JSON — keep the raw text */
    }
    throw new Error(detail || `${res.status} ${res.statusText}`)
  }
  return (await res.json()) as T
}

// --- speech out -------------------------------------------------------------

export interface SpeakOptions {
  /** Kokoro voice id. Omit to take the platform default (a broker setting). */
  voice?: string
  /** Kokoro language code ('a'=en-us, 'b'=en-gb, 'e'=es, 'f'=fr, 'j'=ja). */
  langCode?: string
  speed?: number
}

/** Synthesize via the broker. Voice and language are omitted unless BOTH are chosen. */
export function synthesize(text: string, opts: SpeakOptions = {}): Promise<TtsResult> {
  const body: Record<string, unknown> = { text }
  // Sent as a pair or not at all: Kokoro voice ids are language-scoped by prefix, so a Spanish
  // voice phonemised as English is noise rather than an accent. Sending one without the other
  // is the single easiest way to produce audio that sounds broken for no visible reason.
  if (opts.voice && opts.langCode) {
    body.voice = opts.voice
    body.lang_code = opts.langCode
  }
  if (typeof opts.speed === 'number') body.speed = opts.speed
  return post<TtsResult>('/api/platform/tts_light', body)
}

let current: HTMLAudioElement | null = null

/** Play a base64 WAV. Resolves when playback ends; rejects if the element errors. */
export function playWav(audioB64: string, onEnd?: () => void): Promise<void> {
  stopSpeaking()
  const el = new Audio(`data:audio/wav;base64,${audioB64}`)
  current = el
  return new Promise<void>((resolve, reject) => {
    el.onended = () => {
      if (current === el) current = null
      onEnd?.()
      resolve()
    }
    el.onerror = () => {
      if (current === el) current = null
      onEnd?.()
      reject(new Error('audio playback failed'))
    }
    void el.play().catch((e) => {
      if (current === el) current = null
      onEnd?.()
      reject(e instanceof Error ? e : new Error(String(e)))
    })
  })
}

/** Stop broker audio AND any on-device utterance. Safe to call when nothing is playing. */
export function stopSpeaking(): void {
  if (current) {
    current.pause()
    current.src = ''
    current = null
  }
  if (typeof globalThis.speechSynthesis !== 'undefined') globalThis.speechSynthesis.cancel()
}

export function isSpeaking(): boolean {
  return current !== null
}

/** On-device speech synthesis. A legitimate OUTPUT fallback: nothing leaves the machine. */
export function speakLocal(text: string, onEnd?: () => void): boolean {
  if (typeof globalThis.speechSynthesis === 'undefined') return false
  stopSpeaking()
  const u = new SpeechSynthesisUtterance(text)
  if (onEnd) u.onend = () => onEnd()
  globalThis.speechSynthesis.speak(u)
  return true
}

// --- markdown -> speakable text --------------------------------------------

const STRIP: [RegExp, string][] = [
  [/```[\s\S]*?```/g, ' '], // fenced code
  [/`([^`]*)`/g, '$1'], // inline code
  [/\[\d+(?:,\s*\d+)*\]/g, ''], // [1] and [1, 2] citation markers
  [/!\[[^\]]*\]\([^)]*\)/g, ' '], // images
  [/\[([^\]]+)\]\([^)]*\)/g, '$1'], // links -> their text
  [/\*\*?([^*]+)\*\*?/g, '$1'], // bold / italic
  [/^\s{0,3}#{1,6}\s*/gm, ''], // headings
  [/^\s{0,3}[-*+]\s+/gm, ''], // bullets
  [/^\s{0,3}>\s?/gm, ''], // blockquotes
  [/\|/g, ' '], // table pipes
]

/**
 * Flatten markdown into something worth listening to.
 *
 * A synthesizer reads scaffolding literally — "asterisk asterisk", "bracket one" — so passing
 * raw markdown to TTS produces text that is technically correct and unlistenable. This is
 * load-bearing rather than cosmetic for any rail whose answers carry citations.
 */
export function speakable(md: string): string {
  let out = md ?? ''
  for (const [re, to] of STRIP) out = out.replace(re, to)
  return out.replace(/\s+/g, ' ').trim()
}

// --- speech in --------------------------------------------------------------

export function canRecord(): boolean {
  return (
    typeof navigator !== 'undefined' &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof MediaRecorder !== 'undefined'
  )
}

/**
 * base64 for an arbitrary blob.
 *
 * Chunked because String.fromCharCode(...bytes) spreads every byte as an argument, and a few
 * seconds of audio exceeds the JS engine's argument limit — it throws RangeError on exactly the
 * inputs that matter (long dictation) while working fine on short test clips.
 */
async function blobToBase64(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer())
  const CHUNK = 0x8000
  let binary = ''
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK))
  }
  return btoa(binary)
}

function extFor(mime: string): string {
  if (mime.includes('webm')) return '.webm'
  if (mime.includes('ogg')) return '.ogg'
  if (mime.includes('mp4') || mime.includes('mpeg')) return '.mp4'
  if (mime.includes('wav')) return '.wav'
  return '.webm'
}

let inputDeviceId: string | null = null
let outputDeviceId: string | null = null

export function setAudioInput(deviceId: string | null): void {
  inputDeviceId = deviceId
}
export function setAudioOutput(deviceId: string | null): void {
  outputDeviceId = deviceId
}
export function getAudioInput(): string | null {
  return inputDeviceId
}
export function getAudioOutput(): string | null {
  return outputDeviceId
}

/**
 * Enumerate audio devices. Labels are empty until mic permission has been granted once —
 * that is a browser privacy rule, not a bug, so callers should show a "grant access" affordance
 * rather than an unlabelled list.
 */
export async function audioDevices(): Promise<{ inputs: MediaDeviceInfo[]; outputs: MediaDeviceInfo[] }> {
  if (typeof navigator === 'undefined' || !navigator.mediaDevices?.enumerateDevices) {
    return { inputs: [], outputs: [] }
  }
  const all = await navigator.mediaDevices.enumerateDevices()
  return {
    inputs: all.filter((d) => d.kind === 'audioinput'),
    outputs: all.filter((d) => d.kind === 'audiooutput'),
  }
}

export interface Recorder {
  /** Stop capture and resolve the transcript. */
  stop: () => Promise<SttResult>
  /** Abandon capture without transcribing. */
  cancel: () => void
}

/**
 * Start recording. Resolves once capture is actually running, so a caller can show a live
 * indicator without guessing.
 *
 * The selected input device is honoured because capture happens here and the audio is posted to
 * the broker — which is precisely what a browser SpeechRecognition path could never do, since it
 * always uses the OS default microphone.
 */
export async function record(language?: string): Promise<Recorder> {
  if (!canRecord()) throw new Error('this browser cannot record audio')
  const constraints: MediaStreamConstraints = {
    audio: inputDeviceId ? { deviceId: { exact: inputDeviceId } } : true,
  }
  const stream = await navigator.mediaDevices.getUserMedia(constraints)
  const rec = new MediaRecorder(stream)
  const chunks: Blob[] = []
  rec.ondataavailable = (e) => {
    if (e.data.size) chunks.push(e.data)
  }
  rec.start()

  const release = () => stream.getTracks().forEach((t) => t.stop())

  return {
    cancel: () => {
      try {
        if (rec.state !== 'inactive') rec.stop()
      } finally {
        release()
      }
    },
    stop: () =>
      new Promise<SttResult>((resolve, reject) => {
        rec.onstop = () => {
          release()
          const mime = rec.mimeType || 'audio/webm'
          const blob = new Blob(chunks, { type: mime })
          if (!blob.size) {
            reject(new Error('nothing was recorded'))
            return
          }
          blobToBase64(blob)
            .then((audio_b64) =>
              post<SttResult>('/api/platform/transcribe', {
                audio_b64,
                suffix: extFor(mime),
                ...(language ? { language } : {}),
              }),
            )
            .then(resolve)
            .catch(reject)
        }
        try {
          rec.stop()
        } catch (e) {
          release()
          reject(e instanceof Error ? e : new Error(String(e)))
        }
      }),
  }
}

/** Route playback to a chosen output device where the browser supports it (Chrome/Edge). */
export async function applyOutputDevice(el: HTMLAudioElement): Promise<void> {
  const sinkable = el as HTMLAudioElement & { setSinkId?: (id: string) => Promise<void> }
  if (outputDeviceId && typeof sinkable.setSinkId === 'function') {
    try {
      await sinkable.setSinkId(outputDeviceId)
    } catch {
      /* unsupported or device gone — fall back to the default sink rather than failing */
    }
  }
}
