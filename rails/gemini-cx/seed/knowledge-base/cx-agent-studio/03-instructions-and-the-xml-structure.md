# Instructions in CX Agent Studio — and the recommended XML structure

> Source: docs.cloud.google.com/customer-engagement-ai/conversational-agents/ps/instruction
> As of: 2026-08 · Verified: 2026-08-18 · Status: GA

## What are instructions in CX Agent Studio?

Instructions "provide detailed guidance for the model on what it should do", written in natural
language. They are the primary way behaviour is specified — CX Agent Studio is prompt-driven,
not intent-driven, and that is the single biggest conceptual break from Dialogflow CX. An
instruction can carry the overall goal, behavioural guidelines, a persona definition, and typed
references to sub-agents, tools, and variables.

## The recommended XML tag structure for instructions

Google recommends structuring instructions as XML rather than prose, because the model handles
delimited structure more reliably. The documented tags are `<role>` for the agent's core
function, `<persona>` for personality and tone, `<primary_goal>` for the main objective,
`<constraints>` for rules and limitations, `<taskflow>` for conversation flows, `<subtask>` for
a section of the conversation, `<step>` for an individual action, `<trigger>` for the condition
that initiates a step, `<action>` for the agent's response to that trigger, and `<examples>` for
few-shot demonstrations.

You do not have to hand-write this. The **Restructure** button converts natural-language
instructions into the XML structure, and the **Refine** feature uses AI to improve a selected
passage. The recommended workflow is therefore write prose, restructure, then refine — not
author XML from a blank page.

## Global instructions versus per-agent instructions

Global instructions are set in the advanced application settings, apply to every agent in the
application, and are **sent with every conversational turn**. Use them for brand tone, shared
variables, and generic guidance. Per-agent instructions belong on the individual agent. The
distinction has a cost dimension as well as a design one: anything you put in the global
instruction is paid for on every single turn, so a long global block is a recurring tax across
the whole application.

## How to make an agent's output readable — Google's formatting guidance

Google gives specific output-formatting guidance, and it is unusually concrete for vendor docs.
Use single-sentence text blocks with line breaks between ideas. Bold the critical data —
product names, prices, dates, order numbers, deadlines. Convert multi-item content into bulleted
or numbered lists. Avoid dense paragraphs. This is worth following literally, because the
failure mode it prevents is the one customers complain about: a technically correct answer
delivered as an unreadable wall of text.

## Use few-shot examples sparingly, and give them all four parts

Google's position is that few-shot examples should be used **sparingly** — to resolve a specific
quality failure or to demonstrate complex formatting, not as a general teaching method. Examples
should be "descriptive, not exhaustive." A complete example has four components: the `[user]`
input, the `[model]` response including `tool_code` and `tool_outputs`, and the model's
explanation. An example that omits the tool interaction teaches the model the wrong shape.

## There is no documented instruction length limit

The documentation does not specify a maximum instruction length. That is a genuine gap rather
than a generous allowance — treat it as unbounded-but-metered, keep global instructions tight
because they are billed every turn, and verify against current quotas if you are designing
something unusually large.
