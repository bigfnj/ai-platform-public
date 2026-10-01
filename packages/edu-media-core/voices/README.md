# Voice Reference Clips

XTTS v2 uses a short reference audio clip to define the voice it generates for all output.

⚠ **Both clips are already here and are COMMITTED**, so the "how to record" section below applies
only if you are replacing them. They also ship in the public mirror (`packages/` is published
wholesale) and their provenance is not recorded anywhere in this repo. A WAV cannot be scanned by
the publish pipeline's forbidden-string gates, so this is the one published asset no automated
check can vouch for. **Never a student's voice** -- that is the standing rule and it is why this
note exists.

## Required files

| File | Language | Description |
|---|---|---|
| `english_reference.wav` | English | Clear English speaker, 6–10 seconds of natural speech |
| `spanish_reference.wav` | Mexican Spanish (es_MX) | Clear Mexican Spanish speaker, 6–10 seconds of natural speech |

## Requirements

- Format: WAV, 22050 Hz or higher sample rate, mono or stereo
- Duration: 6–10 seconds (longer clips do not improve quality)
- Content: any clear natural speech — no music, no background noise
- The voice used here will be the voice heard in every audio file generated all year.
  Once chosen, do not swap the reference clips mid-year — consistency matters to a learner who
  hears this voice every day.

## How to record

Use any phone voice memo app or Audacity. Speak naturally for 8 seconds.
Export as WAV. Copy the file here and rename it to match the filename above.

## Tips for the Spanish reference

- Record a native Mexican Spanish speaker if possible
- Alternatively, use a clean clip from a Mexican Spanish YouTube video or podcast
  (ensure you have rights to use it, or record your own)
- Avoid accents from Spain or other regions, because the narration is es_MX and a listener
  who knows the difference will notice
