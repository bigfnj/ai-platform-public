# Agents in CX Agent Studio — root agents and sub-agents

> Source: docs.cloud.google.com/customer-engagement-ai/conversational-agents/ps/agent
> As of: 2026-08 · Verified: 2026-08-18 · Status: GA

## What is an agent in CX Agent Studio?

An agent is one specialised unit of behaviour inside a multi-agent system. Google's wording is
that "an agent application is composed of one or more agents, where each agent can be either the
root agent or a sub-agent." So "agent" in CX Agent Studio does not mean the whole bot — it means
one component of it. The whole bot is the **agent application**.

## Root agent versus sub-agent — what each is for

The **root agent** (also called the steering agent) "acts as the primary entry point and
orchestrator for the overall agent application" and "handles the main interaction with the
end-user." There is exactly one, and it is created automatically when you create an agent
application.

A **sub-agent** (child agent) is "designed to handle a specific task, domain, or capability" and
exists to "promote modularity and reusability." Root agents can invoke sub-agents, and
sub-agents can invoke further sub-agents, so the structure is a hierarchy rather than a flat
list. The design instinct to encourage is one sub-agent per bounded task — returns, order
status, billing — rather than one enormous instruction block trying to cover everything.

You create a sub-agent by clicking the plus sign at the bottom of the root agent and selecting
**Add sub-agent**. Instructions and tools are attached to each agent through the same plus-sign
UI.

## How agents refer to each other and to tools inside instructions

References are typed and syntactic, not free text. A sub-agent is referenced as
`{@AGENT: Agent Name}` and a tool as `{@TOOL: tool_name}`. Variables are snake_case wrapped in
single braces, like `{order_number}`. In the instruction editor these become coloured "chips"
when correctly formed, which is the fastest way to spot a typo — a reference that stays plain
text is not bound to anything. Typing `@` or `{` opens a context menu for inserting agents,
tools, or variables.

## Language detection is automatic, but author in English

Google's guidance is that "agents can automatically detect the language of end-user input, and
they will automatically respond using the same language", while the agent itself should be
designed and written in English for best model understanding. This surprises people: you do not
build one agent per language. You build one agent in English and it answers in the customer's
language. See the models-and-languages collection for how far that actually stretches, because
the text answer and the spoken answer do not cover the same language set.

## Model selection is per application or per sub-agent

The model can be chosen globally for the application or overridden on an individual sub-agent.
That per-sub-agent override is the practical lever for cost and latency: a voice-optimised model
on the sub-agent that talks, a cheaper text model on a sub-agent that only classifies. The
specific model names and their status are in the models-and-languages collection.
