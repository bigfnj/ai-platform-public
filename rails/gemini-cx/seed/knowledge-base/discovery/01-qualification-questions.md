# Discovery questions for a GECX opportunity

> Source: synthesised from the documented GECX constraints and the platform's own gating factors
> As of: 2026-08 · Verified: 2026-08-18 · Status: practitioner guidance

## Which questions actually qualify or disqualify a GECX deal?

Ask only what the customer can answer without homework, and ask for the constraint rather than the
architecture. Each of these maps to a documented GECX limit, so a bad answer changes the design
rather than just colouring it in.

**"Which region must customer conversations stay in?"** Google Cloud CCaaS deployment supports only
the **`us` and `eu`** multi-regions. Anything else means a different channel or a residency
exception, and it is better known in the first meeting than the fourth.

**"What is your contact centre platform today?"** Genesys, Amazon Connect, NICE, and Cisco have no
first-party CX Agent Studio connection and reach it through a voice partner. Google Cloud CCaaS,
Twilio, Five9, and AudioCodes are direct. This single answer determines whether telephony is a
line item or a project.

**"What languages do you take calls in, and which are voice?"** Agents cover **40+ languages**;
low-latency audio-to-audio covers **10 core languages**. If their voice languages fall outside the
ten, the premium voice experience is not on the table for those calls.

**"How long is your average call or chat?"** CX Agent Studio bills **per session with a 5-minute
(300 second) voice inclusion**, then **$0.0025 per second**. Average handle time is therefore a
direct cost driver, and their current AHT is the input to any credible estimate.

**"Do you have transcripts of past conversations, and can we use them?"** CX Agent Studio can
convert uploaded call transcripts into agent flows, and Agent Assist needs conversation datasets
for Smart Reply training. A customer with clean, usable transcript history can move much faster —
and one who cannot release them has a longer path.

## Questions that separate a real project from an experiment

**"What does the agent need to *do*, not just answer?"** GECX's differentiator is executing
multi-step tasks. If every use case is answering questions from documents, this is a retrieval
project and the buyer may be over-buying. If the use cases involve order changes, returns,
appointment moves, or payments, the tool integration story is the project.

**"Which backend systems must it touch, and do they have APIs?"** Tools connect via OpenAPI,
Integration Connectors, MCP, or Python. A use case behind a system with no API is a bigger problem
than any AI consideration, and it is the most common hidden blocker.

**"What is your containment rate today, and how do you measure it?"** If they cannot answer, the
first deliverable is **CX Insights** to establish a baseline — not an agent. Building containment
without a baseline produces a project nobody can prove worked.

**"Who owns the conversation design after go-live?"** GECX is a product to be operated, not a
project to be finished: versions, changelogs, evaluation runs, and traffic splitting all assume a
continuing owner. A deal with no named owner post-launch will regress.

## The question to ask about strategy, because Forrester is right about it

**"What experience are you trying to create, and how will you know it worked?"** Forrester's caution
was to "avoid the trap of simply buying a box of CX", because "creating effective, connected
experiences requires a sound strategy just as much as the tech to deliver it." A customer who
cannot articulate the target experience will buy components and get an expensive IVR. Asking this
early is what turns a licence sale into a programme.
