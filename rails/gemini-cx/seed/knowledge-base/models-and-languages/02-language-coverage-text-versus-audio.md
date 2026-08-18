# Language coverage — 40+ for agents, 10 for audio-to-audio. These are different numbers.

> Source: cloud.google.com/gemini-enterprise-cx/cx-agent-studio; TTEC Digital (2026-03); NRF launch release
> As of: 2026-08 · Verified: 2026-08-18 · Status: GA

## How many languages does CX Agent Studio support?

There are two different correct answers, and giving the wrong one loses credibility fast.

**Agents support over 40 languages.** Google's marketing states that CX Agent Studio deploys
multilingual and multimodal AI agents with human-like voices in **over 40 languages**. The
January 2026 launch release similarly claimed natural language support in **40+ languages**. This
is the number for text conversation and for synthesised speech generally.

**Direct audio-to-audio supports 10 core languages.** The low-latency speech-to-speech path —
built with **Google DeepMind** — covers **ten languages**. This is the number for the premium
voice experience where audio goes in and audio comes out without a transcribe-generate-synthesise
round trip.

So: 40+ languages for what the agent can converse in, 10 languages for the fastest and most
natural-sounding voice path. If someone asks "does it support our language", ask which channel
they mean before answering.

## Why the audio-to-audio distinction matters commercially

Audio-to-audio (A2A) is what produces the demo moment. In a demonstration described by TTEC
Digital, a speaker "moved between English and Mandarin without telling the system, and the agent
adapted instantly" — **mid-conversation language switching with no explicit handoff**. That
behaviour is the strongest single differentiator in the GECX voice story, and it is available in
ten languages, not forty. Promising it for a language outside the ten is the most likely way to
create a delivery problem in a voice project.

## The wider Google speech stack has different, larger coverage again

Do not merge these numbers with the agent numbers. Google's speech recognition and transcription
covers roughly **125 languages**, and speech synthesis offers **220+ voices across 40+
languages**. Agent Assist voice transcription supports **Chirp 3** models. These are the
underlying speech services, so a question about recognising a regional accent, or about how many
voices are available for branding, is answered from the speech stack — and its coverage is not the
same as the agent's conversational coverage or the A2A list.

## You build one agent, not one agent per language

Design and write the agent in **English**. Google's guidance is that agents automatically detect
the input language and respond in the same language, so multilingual support is a runtime
behaviour rather than a per-language build. This is a genuine effort saving worth stating in a
scoping conversation, but pair it with the caveat above: automatic response in 40+ languages does
not mean the fast audio-to-audio path in 40+ languages.

## Verify the specific language list before committing

Google publishes a languages reference page for CX Agent Studio, and the specific membership of
both the 40+ set and the 10-language A2A set should be checked there for any deal that depends on
a particular language. Do not infer membership from the count — "over 40" is not a list, and the
A2A ten are described as "core languages" without an enumeration in the marketing material.
