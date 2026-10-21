// The two voice controllers wired to React, shared by the desktop module and the mobile SPA.
// Each controller is created INSIDE an effect and disposed in its cleanup, rather than once
// per component in a useState initialiser: under StrictMode's dev double-mount a controller
// created outside the effect would be disposed by the first cleanup and never come back.
import { useEffect, useRef, useState, type MutableRefObject } from 'react'
// The platform's dictation policy, the same function web-core's DictateButton calls.
import { chooseSttPath } from '@web-core/dictate_policy'
import { ask, askStream, speakText, transcribe } from './api'
import { ReadAloudControl, type ReadAloudState } from './readaloud'
import { initialVoiceState, VoiceSession, type VoiceState } from './voicesession'
import { browserSttAvailability, canListen, listen, record, speak, stopSpeaking } from './voice'

const timers = {
  setTimeout: (fn: () => void, ms: number) => window.setTimeout(fn, ms),
  clearTimeout: (id: unknown) => window.clearTimeout(id as number),
}

export function useVoiceSession(): [VoiceState, MutableRefObject<VoiceSession | null>] {
  const [state, setState] = useState<VoiceState>(initialVoiceState)
  const ref = useRef<VoiceSession | null>(null)
  useEffect(() => {
    const s = new VoiceSession(
      { askStream, ask, transcribe, record, listen, speak, stopSpeaking, ...timers,
        sttAvailability: () => browserSttAvailability('en-US'), hasRecognition: canListen,
        chooseSttPath }, setState)
    ref.current = s
    setState(s.state)
    return () => {
      s.dispose()
      if (ref.current === s) ref.current = null
    }
  }, [])
  return [state, ref]
}

export function useReadAloud(): [ReadAloudState, (text: string) => void] {
  const [state, setState] = useState<ReadAloudState>({ on: false, loading: false })
  const ref = useRef<ReadAloudControl | null>(null)
  useEffect(() => {
    const c = new ReadAloudControl({ speakText, speak, stopSpeaking }, setState)
    ref.current = c
    return () => {
      c.dispose()
      if (ref.current === c) ref.current = null
    }
  }, [])
  return [state, (text: string) => { void ref.current?.toggle(text) }]
}
