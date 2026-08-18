# Voice readiness — the questions to ask before promising a voice agent

> Source: AudioCodes 2026-05-19; CX Agent Studio deploy and language documentation
> As of: 2026-08 · Verified: 2026-08-18 · Status: practitioner guidance

## What must I check before committing to a GECX voice deployment?

Voice is where GECX is most impressive and where projects most often stall. Each question below maps
to a documented constraint or a named production risk.

**"Which languages arrive by phone, and are they in the audio-to-audio ten?"** The low-latency
speech-to-speech path covers **10 core languages**; agents converse in **40+**. A voice line in a
language outside the ten still works, but not via the premium path that sold the demo.

**"What is your telephony estate — SIP trunks, which CCaaS, which SBC?"** Direct platform
connections exist for **Google Cloud CCaaS**, **Google Telephony Platform**, **Twilio**, **Five9**,
and **AudioCodes**. **Genesys, Amazon Connect, NICE, and Cisco** need a voice partner to bridge
"proprietary VoIP APIs and specific SIP dialects". This answer determines whether telephony is a
configuration task or a workstream.

**"Do you need Microsoft Teams or WhatsApp calling?"** Neither is in Google's native
platform-connection list. AudioCodes Live Hub covers both. A requirement here changes the
architecture.

**"What happens when the bot fails mid-call?"** This is the question nobody asks and everybody
regrets. Production guidance is to implement "built-in resilience mechanisms that can handle bot
failures before and after call initiation, and can offer alternative bots when necessary." A fallback
bot and a human-escalation path are requirements, not enhancements — a failed AI agent on a phone
line is a dropped customer, and it is the most visible failure mode in the business.

**"Can you see into the media stream today?"** Diagnosing a voice failure requires knowing whether
it was the model, the recognition, or the media path. Look for SIP ladder diagrams, real-time call
logs, and performance dashboards. Without media-stream observability you will debug the wrong layer
for weeks.

**"What is your average call duration?"** Voice sessions include **300 seconds**, then bill at
**$0.0025 per second**. A ten-minute average adds roughly **$0.75** per call in overage against a
five-minute baseline — immaterial in a pilot, material at a million calls.

**"Do you have domain vocabulary that generic recognition will mangle?"** Product names, drug names,
part numbers, and place names all need **Speech-to-Text model adaptation**, and the telephony
recognition model must be selected explicitly via `sttConfig`. Leaving recognition on a default is a
common cause of "the bot cannot understand our callers."

## The sequencing rule for voice

Prove the conversation design on the **web widget** first, where there is no telephony integration
to debug, then move the same agent application to voice. Do not use widget latency to estimate voice
latency — different models and different infrastructure — and do not make voice the pilot. Recognise
that moving "from a lab environment to a production-grade enterprise deployment" needs specialised
infrastructure beyond model capability; that gap is where voice projects die.

## Do not quote a latency number

Neither Google nor AudioCodes publishes latency benchmarks for this path. Both describe low latency
qualitatively. Measure it in the customer's own environment with their own telephony and report that
figure instead of a borrowed one.
