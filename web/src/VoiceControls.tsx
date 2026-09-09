// ⚙ Voice SETTINGS for the platform — the top-bar popover.
//
// This used to also host a GLOBAL "listen" mic that dictated into whatever field had focus,
// inferring intent by tracking focus and writing through the DOM. That was retired: dictation
// now lives as a per-rail <DictateButton> beside each rail's own text box — the rail knows which
// field it means, so there is nothing to infer. What remains here is genuinely global and worth
// one shared control: the read-aloud voice + speed, and the microphone / speaker DEVICE that the
// per-rail mics and playback use. Speech never leaves the machine (broker Kokoro / faster-whisper).
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  getAudioInputDevices, getAudioOutputDevices, getSelectedInput, getSelectedSink,
  lsSet, primeDevicePermission, setAudioInput, setAudioSink,
} from './voice'

const LANG_KEY = 'platform-voice-lang'
const VOICE_KEY = 'platform-voice-voice'
const SPEED_KEY = 'platform-voice-speed'

/**
 * The platform-wide playback voice: American English, female.
 *
 * The broker holds the same default (BROKER_KOKORO_VOICE) and applies it whenever a caller
 * names none, so a user who never opens this popover gets it anyway. This constant is what the
 * picker STARTS on; changing the shipped voice for everyone is a server-side setting.
 */
export const DEFAULT_VOICE = 'af_heart'

// Kokoro voices. Language and voice are bound together deliberately: selecting a Spanish voice
// with lang_code 'a' produces garbled audio, so picking a voice also sets its language.
const VOICES: { id: string; label: string; lang: 'a' | 'b' | 'e' }[] = [
  { id: DEFAULT_VOICE, label: 'Heart (US, f) · default', lang: 'a' },
  { id: 'af_bella', label: 'Bella (US, f)', lang: 'a' },
  { id: 'am_michael', label: 'Michael (US, m)', lang: 'a' },
  { id: 'bf_emma', label: 'Emma (UK, f)', lang: 'b' },
  { id: 'ef_dora', label: 'Dora (ES, f)', lang: 'e' },
  { id: 'em_alex', label: 'Alex (ES, m)', lang: 'e' },
]

const lsGet = (k: string, fb: string) => {
  try { return localStorage.getItem(k) || fb } catch { return fb }
}

export function VoiceControls() {
  const [open, setOpen] = useState(false)
  const [inputs, setInputs] = useState<MediaDeviceInfo[]>([])
  const [outputs, setOutputs] = useState<MediaDeviceInfo[]>([])
  // Why device enumeration failed, if it did — surfaced under the picker.
  const [deviceErr, setDeviceErr] = useState('')
  const [voice, setVoice] = useState(() => lsGet(VOICE_KEY, DEFAULT_VOICE))
  const [speed, setSpeed] = useState(() => Number(lsGet(SPEED_KEY, '1')) || 1)
  const pop = useRef<HTMLDivElement | null>(null)

  // Close on outside click / Escape.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!pop.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const refreshDevices = useCallback(() => {
    void getAudioInputDevices().then(setInputs)
    void getAudioOutputDevices().then(setOutputs)
  }, [])

  useEffect(() => {
    if (open) refreshDevices()
  }, [open, refreshDevices])

  const pickVoice = (id: string) => {
    setVoice(id)
    lsSet(VOICE_KEY, id)
    // Voice implies language; a mismatch is what produces garbled audio.
    lsSet(LANG_KEY, VOICES.find((v) => v.id === id)?.lang ?? 'a')
  }

  return (
    <div className="vc" ref={pop}>
      <button
        type="button"
        className="vc-btn vc-cog"
        onMouseDown={(e) => e.preventDefault()}
        onClick={() => setOpen((o) => !o)}
        title="Voice settings"
        aria-label="Voice settings"
      >
        <span aria-hidden="true">⚙</span>
      </button>

      {open && (
        <div className="vc-pop">
          <div className="vc-pop-h">Voice</div>

          <label className="vc-row">
            <span>Read-aloud voice</span>
            <select value={voice} onChange={(e) => pickVoice(e.target.value)}>
              {VOICES.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
            </select>
          </label>

          <label className="vc-row">
            <span>Speed</span>
            <input
              type="range" min={0.5} max={1.5} step={0.05} value={speed}
              onChange={(e) => {
                const v = Number(e.target.value)
                setSpeed(v); lsSet(SPEED_KEY, String(v))
              }}
            />
            <em>{speed.toFixed(2)}×</em>
          </label>

          <label className="vc-row">
            <span>Microphone</span>
            <select value={getSelectedInput()} onChange={(e) => setAudioInput(e.target.value)}>
              <option value="">System default</option>
              {inputs.filter((d) => d.label).map((d) => (
                <option key={d.deviceId} value={d.deviceId}>{d.label}</option>
              ))}
            </select>
          </label>

          {/* setSinkId is Chromium-only; hide the picker rather than offer a dead control. */}
          {typeof (new Audio() as HTMLAudioElement & { setSinkId?: unknown }).setSinkId === 'function' && (
            <label className="vc-row">
              <span>Speaker</span>
              <select value={getSelectedSink()} onChange={(e) => setAudioSink(e.target.value)}>
                <option value="">System default</option>
                {outputs.filter((d) => d.label).map((d) => (
                  <option key={d.deviceId} value={d.deviceId}>{d.label}</option>
                ))}
              </select>
            </label>
          )}

          {!inputs.some((d) => d.label) && (
            <button className="vc-perm" type="button"
                    onClick={() => void primeDevicePermission().then((reason) => {
                      setDeviceErr(reason ?? '')
                      refreshDevices()
                    })}>
              Allow microphone access to list devices…
            </button>
          )}

          {/* Why the button did nothing — outside a secure context the browser never prompts. */}
          {deviceErr && <p className="vc-fine vc-err">{deviceErr}</p>}

          <p className="vc-fine">
            Dictation and read-aloud run on this machine. Nothing is sent to a cloud service.
          </p>
        </div>
      )}
    </div>
  )
}

export default VoiceControls
