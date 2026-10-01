// Which speech-to-text path a dictation press may use.
//
// Extracted from DictateButton.tsx for the same reason speech_lang.ts was: it has no runtime
// imports at all, so node's type stripping loads it directly and `node --test` can cover it.
// DictateButton is .tsx, there is no React harness in this repo, and a privacy default nobody
// can test is a comment.
//
// THE DEFAULT IS LOCAL, AND THAT IS THE WHOLE POINT. This used to be `localOnly?: boolean`,
// opt-IN, and the header comment claimed cloud STT was used "NOT on localOnly rails (edu / IEP
// / finance)". A repo-wide grep found ONE of seven call sites passing it. The other six --
// including finance and co-worker, which reads the owner's email and calendar -- silently
// allowed the mic stream to reach Google's or Microsoft's cloud STT on any browser without an
// on-device pack.
//
// Inverting it changes which way a mistake falls. Forgetting `allowCloudStt` now costs a slower
// local transcription; forgetting `localOnly` used to cost audio leaving the box, silently.
export type SttPath = 'ondevice' | 'cloud' | 'broker'

/** Chrome 139+ `SpeechRecognition.available()`; null when the statics are absent entirely. */
export type SttAvailability = 'available' | 'downloadable' | 'downloading' | 'unavailable' | null

export interface SttChoice {
  /** Whether a SpeechRecognition constructor exists at all. */
  hasCtor: boolean
  /** What `available({processLocally: true})` reported, or null if it cannot be asked. */
  availability: SttAvailability
  /** Opt IN to cloud STT for this field. Omitted means local-only. */
  allowCloudStt?: boolean
}

export function chooseSttPath({ hasCtor, availability, allowCloudStt }: SttChoice): SttPath {
  // No browser API: the broker is the floor and always available.
  if (!hasCtor) return 'broker'
  // On-device is local. It is the best path and is allowed everywhere, including the rails
  // that must never cloud -- that is the distinction the old `localOnly` name obscured.
  if (availability === 'available') return 'ondevice'
  // Everything else means "not on-device right now": an older Chrome with no statics to ask
  // (null), a pack still downloading, or none available. Cloud only if this field opted in.
  return allowCloudStt ? 'cloud' : 'broker'
}
