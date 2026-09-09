# What CX Agent Studio is — the GECX agent builder

> Source: docs.cloud.google.com/gemini-enterprise-cx/cx-agent-studio
> As of: 2026-08 · Verified: 2026-08-18 · Status: GA

## What is Customer Experience Agent Studio?

CX Agent Studio is the build surface of GECX: Google describes it as "a minimal code
conversational agent builder" that uses AI and a visual interface to guide you through building
an agent application. It is built on Google's **Agent Development Kit (ADK)** and is the
successor line to Dialogflow CX — same problem space, rebuilt around LLM agents with tools
rather than around intents and flows.

The pitch is that it collapses the specialist skill requirement. A conversational designer who
is not a developer can assemble a working multi-agent, multimodal support agent, because the
infrastructure, the integrations, and the security controls are handled by the platform rather
than assembled per project.

## What CX Agent Studio gives you that a raw model API does not

Five things, and they are the honest reason to use it over wiring Gemini up yourself. It
handles **infrastructure, integrations, and security** automatically. It supports
**asynchronous backend processing** so a slow tool call does not stall the conversation — the
agent keeps talking while the work happens. It provides **ultra-low-latency voice** through
bi-directional streaming rather than a transcribe-then-generate-then-synthesise chain. It ships
**built-in collaboration, versioning, changelogs, and rollback**, which is what makes a
conversational agent a maintainable product rather than a prompt someone owns. And it provides
a first-class **evaluation** framework with defined metrics.

## The AI-augmented authoring features

CX Agent Studio uses Gemini to build the thing you are building, in four named ways.
**Generate agents with Gemini** creates an agent application automatically from a description.
**Instruction restructuring** converts prose instructions into the XML structure the model
handles best. **Instruction refinement** rewrites a selected passage for clarity. **Test case
hill climbing** uses AI to make your evaluation suite more rigorous over successive rounds.

A fifth capability is worth calling out separately because it is the one practitioners react to:
you can **upload past call transcripts and have the system convert them into agent flows**, so
the design is anchored in how customers actually talk rather than how a designer imagines they
talk.

## The named building blocks of an agent application

An agent application is assembled from a fixed vocabulary of objects, and knowing the names is
most of knowing the product: **Agents** (the units of behaviour), **Instructions** (natural
language guidance), **Tools** (connections to systems), **Variables** (state),
**Guardrails** (safety and policy constraints), **Callbacks** (event handlers),
**Toolsets** (grouped tools), **Deployments** (runtime configurations), **Versions** (change
management), and **Examples** (few-shot demonstrations).

## The documented lifecycle phases

The documentation is organised around the phases of work, which is also the order to teach it
in: **Build** (agents, instructions, tools, variables, guardrails, callbacks, handoff rules,
flow-based agents, versions), **Test** (simulator, evaluations), **Deploy** (agent application
deployment, web widget, platform connections, API access, traffic splitting, networking),
**Analyze** (conversation history, monitoring dashboard), and **Secure** (IAM, audit logging,
VPC Service Controls, CMEK).
