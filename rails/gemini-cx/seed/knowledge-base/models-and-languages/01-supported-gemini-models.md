# Which Gemini models CX Agent Studio supports

> Source: docs.cloud.google.com/customer-engagement-ai/conversational-agents/ps/agent
> As of: 2026-08 · Verified: 2026-08-18 · Status: two GA, one Preview

## Which models can a CX Agent Studio agent use?

Three, and the choice is between text-optimised and voice-optimised rather than between sizes.
**`gemini-2.5-flash`** is text-optimised and **GA** — the default safe choice for chat.
**`gemini-3-flash`** is text-optimised and in **Preview**, so it should not carry a production
commitment yet. **`gemini-3.1-flash-live`** is voice-optimised and **GA** — this is the model
behind the low-latency speech experience, and the `-live` suffix is the signal that it is built
for bi-directional streaming rather than request/response.

Model selection is configurable **globally for the application or per sub-agent**, so a single
agent application can run a voice-optimised model on the sub-agent that speaks and a text model
elsewhere.

## Notice what is not on the list — no Pro tier

All three options are **Flash**-class models. There is no Gemini Pro or Ultra option documented
for CX Agent Studio. This is a deliberate product decision and it is the right one for the
workload: contact-centre turns are latency-critical and high-volume, and a slower, more expensive
model would degrade the experience it is meant to improve. If a stakeholder asks for "the best
Gemini model" on a CX agent, the honest answer is that the platform does not offer one, because
turn latency matters more than peak reasoning depth here.

## The documentation does not publish generative parameters

CX Agent Studio's agent documentation does not expose temperature, top-k, top-p, or other
sampling parameters, and provides no configuration code samples for them. Behaviour is shaped
through instructions, examples, and guardrails rather than through decoding parameters. Do not
promise a customer that they can tune sampling — verify current capability before committing.

## Do not confuse the agent model with the speech models

The Gemini model above generates the agent's reasoning and language. Speech is separate: Agent
Assist voice transcription supports **Chirp 3** models, and Google's wider speech stack provides
recognition across roughly 125 languages and synthesis in 220+ voices. A question about
recognising an accent or choosing a voice is a speech-stack question, not a Gemini model
question, and they have different coverage.
