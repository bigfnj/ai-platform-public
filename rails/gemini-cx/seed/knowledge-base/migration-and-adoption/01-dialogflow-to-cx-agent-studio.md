# Migrating from Dialogflow to CX Agent Studio

> Source: docs.cloud.google.com/dialogflow/cx/docs/how/migrate; CX Agent Studio flow docs; community migration tooling
> As of: 2026-08 · Verified: 2026-08-18 · Status: no first-party CX→Agent Studio migration guide found

## Is there an official Dialogflow CX to CX Agent Studio migration tool?

Not one that this research could locate. Google publishes a first-party migration guide for
**Dialogflow ES → Dialogflow CX**, but no equivalent first-party **Dialogflow CX → CX Agent
Studio** guide surfaced. What exists is community tooling that exports the agent, parses the JSON
package, extracts the files, and calls the **CES APIs** to create the corresponding apps and
agents.

Say this plainly to a customer rather than implying a supported upgrade path. A CX → Agent Studio
move is currently a **rebuild assisted by tooling**, not a migration wizard, and it should be
scoped and priced as such. Verify current tooling status with the Google Cloud account team, since
this is exactly the kind of gap that gets filled without fanfare.

## Why it is a rebuild rather than a conversion

Because the architecture is different in kind. Dialogflow CX is built on **intents, flows, pages,
and transitions** — a designed state machine. CX Agent Studio is built on **agents, instructions,
and tools** — a prompt-driven multi-agent system. There is no faithful mechanical translation from
a state machine into an instruction, because the state machine encodes decisions that the model is
now expected to make. Tooling can carry over the assets (training phrases, webhook definitions,
entity lists); it cannot carry over the design.

The useful reframe: a migration is an opportunity to delete. Most mature Dialogflow CX agents
contain large amounts of flow logic that exists only to compensate for weak intent matching, and
that logic is precisely what an LLM agent makes unnecessary.

## What Dialogflow ES → CX migration teaches about the pattern

The ES → CX migration tool copies the bulk of the data and then "writes to a TODO file with a list
of items that must be manually migrated". Google's own recommended process is an **automated and
manual hybrid**: run the tool, then "re-create your complete Dialogflow CX agent using best
practices, the TODO list, and the data that was migrated by the tool."

Expect the same shape for CX → Agent Studio, and set that expectation up front. The tool gets you
the inventory; a human rebuilds the behaviour. Anyone promising a lift-and-shift is
under-scoping.

## The lower-risk sequencing for an existing Dialogflow customer

Three steps, in this order. First, adopt **Agent Assist** over the existing contact centre — it is
an overlay, requires no containment rebuild, and delivers value while the rest is planned. Second,
turn on **CX Insights** to baseline containment, topics, and quality, so the migration has a
measurement it can be judged against. Third, rebuild containment in **CX Agent Studio**
incrementally, highest-volume intent first, using **traffic splitting** to run new against old on
live traffic.

Decouple the telephony migration from the agent migration. A voice partner such as AudioCodes
positions specifically for "migration from Dialogflow to CX Agent Studio without disrupting
existing telephony connections", and keeping the SIP layer stable while the agent layer changes
removes the largest single source of rollout risk.
