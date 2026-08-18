# The GECX plays worth leading with, in order of risk

> Source: synthesised from component availability, integration surface, and documented constraints
> As of: 2026-08 · Verified: 2026-08-18 · Status: practitioner guidance

## Which GECX play should I lead with?

Lead with the lowest-risk play that produces a measurable result, and let it fund the next one. In
increasing order of risk:

**Play 1 — Agent Assist overlay on the existing contact centre.** No containment rebuild, no
telephony migration, no replacement decision. Agent Assist integrates with Genesys Cloud,
LivePerson, Salesforce, Twilio Flex, and custom desktops. Generative Knowledge Assist, Summarization
with custom sections, and AI Coach deliver measurable handle-time and consistency improvements
against an existing baseline. This is the shortest path from signature to visible value, and it
survives a change of mind about everything else.

**Play 2 — CX Insights baseline and Quality AI.** Turn on analytics before building anything.
Establish containment, call drivers, sentiment, and quality scores; replace a manual QA process that
samples a few percent of calls with automated assessment across all of them, including virtual
agents. This is the play that makes every later play provable — and a customer who cannot state
their current containment rate needs this first regardless of what they asked for.

**Play 3 — containment rebuild in CX Agent Studio, one intent at a time.** Highest-volume intent
first, evaluated with Golden and Scenario test cases, shipped behind traffic splitting against the
incumbent. Incremental, measurable, reversible.

**Play 4 — cross-journey continuity between commerce and service.** The genuine differentiator, and
the one Forrester conceded as novel. Higher risk because the prebuilt Commerce Agents are still
"coming soon", so this is currently a custom build in CX Agent Studio against catalogue, cart, and
order APIs.

## Why this order and not the exciting one

The instinct after reading the launch material is to lead with Play 4, because the shopping agent
demo is the impressive part. Resist it. Play 4 depends on components that Google's own
documentation marks unavailable, on backend commerce APIs that may not exist, and on a journey
design the customer probably has not done. Leading with it maximises the chance of a stalled
flagship project.

Plays 1 and 2 depend on nothing that is not GA, integrate with what the customer already owns, and
produce the baseline that makes Play 4 justifiable later.

## The play to avoid selling

**Do not sell "replace your IVR with an autonomous agent" as a first project.** It combines every
risk at once: telephony integration, containment rebuild, voice latency, language coverage
constraints, regional restrictions, and the highest visibility failure mode in the business. If a
customer asks for it, sequence it — Agent Assist and CX Insights first, then containment
incrementally with traffic splitting, then the voice channel with a fallback bot in place.

## Where the voice conversation belongs

Voice is the strongest part of the GECX story and the riskiest part of a GECX project. The
audio-to-audio path with mid-conversation language switching is a genuine differentiator in **10
core languages**. But production voice needs enterprise telephony infrastructure, resilience for
bot failure before and after call initiation, media-stream observability, and a fallback bot.
Introduce voice once the conversation design is proven on the web widget — not as the pilot.
