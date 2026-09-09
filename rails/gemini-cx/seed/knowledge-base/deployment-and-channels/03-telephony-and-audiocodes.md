# Enterprise telephony for CX Agent Studio — the gap AudioCodes fills

> Source: audiocodes.com blog, "Bridging the Gap: Seamlessly Connecting Google CX Agent Studio to the Global Voice Ecosystem", 2026-05-19
> As of: 2026-08 · Verified: 2026-08-18 · Status: partner solution, AudioCodes is a validated Google partner

## Why do I need a voice partner if CX Agent Studio already does voice?

Because conversational quality and telephony connectivity are different problems. CX Agent Studio
generates excellent conversation; it does not natively bridge the "proprietary VoIP APIs and
specific SIP dialects" used by established contact-centre platforms. AudioCodes identifies four
gaps that appear when moving "from a lab environment to a production-grade enterprise
deployment".

The **connectivity gap** is the inability to speak the SIP dialects of incumbent platforms —
**Genesys, Amazon Connect, Five9, NICE, and Cisco** are named as examples requiring proprietary API
bridging. **Context-aware escalation** is seamless human handoff with full conversation context
preserved. **Scalability and redundancy** means "geo-redundant architectures to ensure service
continuity even during regional outages". **Observability** means "deep visibility into the media
stream, including SIP ladder diagrams, real-time call log and performance dashboards".

That last one is the underrated item. When a voice agent fails in production, the question is
almost always whether the failure was in the model, the recognition, or the media path, and
without media-stream visibility you cannot tell.

## The two AudioCodes deployment options

**Live Hub** is a cloud-based self-service portal that connects CX Agent Studio bots to "any voice
channel — such as SIP trunks, phone numbers, contact center platforms, Microsoft Teams, WhatsApp
calling and WebRTC — in just a few clicks." **VoiceAI Connect** is the on-premises or private-cloud
option for scenarios "where privacy and security are paramount." Both are built on AudioCodes
**Mediant session border controllers (SBCs)**.

The channel list is wider than Google's own: **Microsoft Teams** and **WhatsApp calling** in
particular are not in the native CX Agent Studio platform-connection list, so a requirement for
either points toward this path.

## Voice deployment guidance worth following

Three practitioner points. Recognise that production voice needs specialised infrastructure beyond
model capability — this is the mistake that turns a successful pilot into a stalled rollout.
Implement "built-in resilience mechanisms that can handle bot failures before and after call
initiation, and can offer alternative bots when necessary" — a fallback bot is a real requirement,
not a nicety, because a failed AI agent on a phone line is a dropped customer. And use monitoring
tooling for real-time debugging rather than post-hoc log archaeology.

## Migration without ripping out telephony

AudioCodes positions itself for "migration from Dialogflow to CX Agent Studio without disrupting
existing telephony connections", and is described as "a validated partner for deploying CX Agent
Studio at scale" per Google Cloud documentation. For an organisation with years of SIP integration
work behind its current bot, decoupling the agent migration from the telephony migration is the
difference between a manageable project and a rebuild of everything at once.

## The AI components in the AudioCodes path

The stack uses **Google STT**, **TTS**, and **Gemini speech-to-speech models**. Note that
AudioCodes' own material provides **no quantitative latency or quality benchmarks** — it states
that generative AI enables "lower latency, better handling of interruptions and a fluid,
conversational flow" compared with intent-based predecessors, but publishes no figures. Do not
quote a latency number for this path; measure it in your own environment.
