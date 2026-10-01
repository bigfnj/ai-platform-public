# Objection: "You keep saying agentic, but this is just a chatbot"

> Source: Forrester 2026-01-20; commerce-agents documentation status; CX Agent Studio tool documentation
> As of: 2026-08 · Verified: 2026-08-18 · Status: practitioner guidance

## Objection: "You keep saying agentic, but this is just a chatbot"

Give ground, because the objection is substantially right today. Forrester assessed the "agentic"
label as misleading, describing the capabilities as **"assistive, conversational experiences"**
rather than truly autonomous agents. Google's own documentation agrees more than its marketing
does: the components that execute autonomous consented actions — the **Commerce Agents** — are
marked **"coming soon"**.

Conceding this buys credibility for the part that is real, and the part that is real is not small.

## What is genuinely more than a chatbot today

**Tools that act.** Seventeen tool types, including OpenAPI, Integration Connectors, MCP, Python,
Salesforce, ServiceNow, and Jira. An agent that reads an order, changes a delivery date, and writes
the result back into ServiceNow is not answering a question — and that is buildable today in CX
Agent Studio without waiting for Commerce Agents.

**Asynchronous execution.** A tool can run for 5 to 60 seconds while the agent keeps the
conversation alive and then incorporates the result. That is task execution, not turn-taking.

**Multi-agent orchestration.** A root steering agent delegating to specialist sub-agents, with
deterministic handoff rules, is architecturally an agent system rather than a single prompt.

**Evaluation on task completion.** The composite metric is `User_Goal_Satisfied AND
no_hallucinations_detected AND Expectations_Satisfied` — the platform measures whether the job got
done, which is not a chatbot metric.

## The honest framing to offer

"Autonomous end-to-end commerce is Google's roadmap, and the documentation still says coming soon
for those prebuilt agents. What is real today is an agent that reasons over your systems and
performs multi-step tasks through tools, evaluated on whether the task completed. If you need
autonomous checkout specifically, we should confirm the Commerce Agent timeline with Google before
you plan around it."

That answer distinguishes you from whoever last read them the press release, and it is the version
that survives contact with their architecture team.

## Do not overcorrect

Having conceded the label, do not concede the capability. "It's just a chatbot" is also wrong — a
system with tool execution, async task handling, sub-agent delegation, and task-completion
measurement is meaningfully different from an intent-matching bot. The accurate position is
narrower than "agentic" and considerably more than "chatbot".
