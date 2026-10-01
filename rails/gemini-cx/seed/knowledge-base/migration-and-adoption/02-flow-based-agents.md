# Flow-based agents — reusing existing Dialogflow flows inside CX Agent Studio

> Source: docs.cloud.google.com/gemini-enterprise-cx/cx-agent-studio/flow
> As of: 2026-08 · Verified: 2026-08-18 · Status: GA

## Can I keep my existing Dialogflow flows?

Yes. CX Agent Studio supports **flow-based agents** specifically for this: Google's guidance is
that "if you must make use of your existing flows, you can use CX Agent Studio flow-based agents",
and — importantly — "it is important to use CX Agent Studio agents as the steering agents to manage
routing between CX Agent Studio agents and flows."

So the supported hybrid architecture is an **LLM steering agent on top, deterministic flows
underneath**. The root agent understands what the customer wants and routes; a legacy flow executes
the parts that are already built and working.

Note the phrasing Google chose: "if you must". Flow-based agents are positioned as a compatibility
accommodation, not as the recommended target architecture. Treat them as a bridge with an expected
end date rather than a permanent design.

## When keeping a flow is the right call

When the flow encodes a genuinely deterministic, compliance-sensitive, or heavily-tested process
that you do not want a model improvising inside. Identity verification, payment capture, regulated
disclosures, and anything with an audit requirement are good candidates: you want those steps to
happen the same way every time, and a flow guarantees that in a way an instruction does not.

This mirrors a general principle that holds across agent design — **deterministic in code,
judgement in the model**. Use the LLM for understanding intent, handling phrasing variety, and
choosing the path; use the flow for executing a sequence that must not vary.

## When to rebuild the flow instead

When the flow exists mainly to compensate for weak intent matching — long disambiguation trees,
re-prompt loops, "I didn't catch that" branches, and fallback handling. That is the bulk of most
mature Dialogflow agents and it is exactly what an LLM agent replaces. Porting it forward carries
the old system's limitations into the new one and gives you the worst of both: LLM cost with state
machine rigidity.

## Practical migration sequencing with flow-based agents

Stand up a CX Agent Studio steering agent, attach the existing flows as flow-based agents so the
system is functionally complete on day one, then replace flows one at a time — highest volume and
simplest first — measuring each with the evaluation framework and shipping each behind traffic
splitting. This gives a continuously working system and a defensible per-increment case, rather
than a long parallel build with a single high-risk cutover.
