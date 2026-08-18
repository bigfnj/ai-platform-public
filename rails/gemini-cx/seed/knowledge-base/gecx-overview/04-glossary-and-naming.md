# GECX glossary — the names, acronyms, and the ones that get confused

> Source: consolidated from Google Cloud GECX documentation and product pages
> As of: 2026-08 · Verified: 2026-08-18 · Status: reference

## What do the GECX acronyms and product names mean?

**GECX** — Gemini Enterprise for Customer Experience. The CX-specific solution.

**Gemini Enterprise** — the parent, company-wide agent platform. Not the same product as GECX.

**CX Agent Studio** — also written **Customer Experience Agent Studio**. The agent builder.

**Agent Assist** — real-time assistance for human contact-centre agents.

**CX Insights** — Customer Experience Insights. Conversation analytics.

**Commerce Agents** — prebuilt commerce agents (Shopping, Food Ordering). Documented as coming soon.

**CES** — Customer Engagement Suite. The predecessor brand, still visible in the API package name
`google.cloud.ces.v1`.

**CCAI** — Contact Center AI, Google's earlier contact-centre portfolio branding.

**ADK** — Agent Development Kit, the Google framework CX Agent Studio is built on.

**A2A** — used for two different things in this space. In GECX voice it means **audio-to-audio**,
the low-latency speech-to-speech path. In Agent Assist, **Live Translation (A2A)** means
**agent-to-agent** translation. Always check which one the context means.

**CCaaS** — Contact Center as a Service. **Google Cloud CCaaS** is Google's own offering and a
deployment target for CX Agent Studio.

**GTP** — Google Telephony Platform, another deployment target.

## Terms inside an agent application

An **agent application** is the whole deployable thing. An **agent** is one unit of behaviour
inside it — either the single **root agent** (also called the **steering agent**) or one of many
**sub-agents** (child agents). **Instructions** are the natural-language behaviour specification.
**Tools** connect to systems. **Toolsets** group tools. **Variables** hold state. **Guardrails**
enforce safety and policy. **Callbacks** are event handlers. **Handoff rules** transfer control
between parent and child agents. **Examples** are few-shot demonstrations. **Versions** and
**Deployments** manage change and runtime.

## Evaluation vocabulary

A **test case** is one scenario, of type **Scenario** (AI-simulated from a user goal) or **Golden**
(a pinned ideal path for regression). A **Run** is one execution of test cases against a version; a
**Result** is one test case within a run. **Personas** are simulated callers. **Tool fake** mocks
tool calls. **Stable replay** injects expected values for a consistent environment; **naive replay**
does not. **Hill climbing** is AI-driven improvement of the test suite itself.

## The four confusions worth pre-empting

**Gemini Enterprise versus GECX** — parent platform versus CX solution, different licensing and
different docs.

**40+ languages versus 10 languages** — 40+ for agent conversation, 10 for low-latency
audio-to-audio. Different modalities, different numbers.

**Handoff rules versus human escalation** — handoff rules move control between agents; reaching a
human is a guardrail outcome and a CCaaS function.

**Agent-as-a-tool versus handoff** — agent-as-a-tool returns a result and the caller keeps control;
handoff transfers control away.

## Two "agent" meanings in one sentence

Contact-centre people say "agent" meaning a human being. GECX documentation says "agent" meaning a
software component. Google's own guardrail outcome, "handoff to agent", means handoff to a **human**
— while a "sub-agent handoff" means software. Establish which sense is in play at the start of any
workshop, because the ambiguity produces requirements documents that mean the opposite of what their
authors intended.
