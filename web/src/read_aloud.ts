// One read-aloud chip's reads: each its own request, and Stop ends the one in flight.
//
// Extracted from SpeakButton.tsx for the reason dictate_policy.ts was: it has no imports, so
// node's type stripping loads it directly and web/tests/voice-devices.test.js drives it, where a
// .tsx component has no harness in this repo (the browser half is railcheck.py
// cw-speak-stop-loading, through co-worker's built bundle).
//
// Stop while a read was still loading used to drop the late reply (a per-click generation) but
// leave its request running: the browser kept /api/platform/tts_light open for audio nobody would
// hear. Each read now has an AbortController of its own, handed to the request, and every new read
// aborts the one before it, as Stop and an unmount do. The abort also does the generation's job:
// a read whose signal is aborted neither plays nor falls back to the on-device voice.
export interface ReadSteps<T> {
  /** Ask for the audio. Hand `signal` to the request, so that an abort ends it. */
  ask: (signal: AbortSignal) => Promise<T>
  /** The answer to a read nobody stopped. */
  play: (answer: T) => void
  /** The request failed, and nobody stopped the read: the on-device voice. */
  fallback: () => void
}

export interface Reader {
  /** Start a read, aborting the one in flight. Settles once it has played, fallen back, or been
   *  stopped. */
  read<T>(steps: ReadSteps<T>): Promise<void>
  /** Stop: abort the read in flight, if any. */
  stop(): void
}

export function createReader(): Reader {
  let live: AbortController | null = null
  return {
    async read<T>({ ask, play, fallback }: ReadSteps<T>): Promise<void> {
      live?.abort()
      const mine = new AbortController()
      live = mine
      let answer: T
      try {
        answer = await ask(mine.signal)
      } catch {
        if (!mine.signal.aborted) fallback()
        return
      }
      // A request that ignored the abort may still answer; a stopped read plays nothing anyway.
      if (!mine.signal.aborted) play(answer)
    },
    stop(): void {
      live?.abort()
      live = null
    },
  }
}
