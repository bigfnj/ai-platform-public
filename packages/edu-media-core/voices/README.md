# Voice Reference Clips

XTTS v2 clones the voice it speaks in from one short reference clip per language. It reads two
files from this directory, or from `$VOICES_DIR` if that is set:

| File | Language | Description |
|---|---|---|
| `english_reference.wav` | English | Clear English speaker, 6–10 seconds of natural speech |
| `spanish_reference.wav` | Mexican Spanish (es_MX) | Clear Mexican Spanish speaker, 6–10 seconds of natural speech |

## If the clips are not here, supply your own

**The public snapshot of this repository ships no reference clips.** The two used in the private
repository it is generated from have no recorded provenance, and a recording of a person's voice
is not something to publish on a guess, so the publish pipeline withholds both. Until you add
them, every narration call fails with an error that names the missing clip and points at this
file.

To supply them, record your own (below) or use a clip whose licence explicitly allows voice
cloning, and save it here under the exact filename in the table. **Never a student's voice.**

The broker's speech worker reads this directory by default (`BROKER_MEDIA_VOICES_DIR`, which it
hands to the worker as `VOICES_DIR`). To keep your clips elsewhere, point that variable at the
directory that holds them.

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

- Record a native Mexican Spanish speaker who has agreed to it
- Do not lift a clip from a video or podcast unless its licence permits voice cloning: a voice
  is personal, and a public clip is not a licensed one
- Avoid accents from Spain or other regions, because the narration is es_MX and a listener
  who knows the difference will notice
