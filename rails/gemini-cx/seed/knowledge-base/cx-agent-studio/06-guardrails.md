# Guardrails in CX Agent Studio — the four types and their outcomes

> Source: docs.cloud.google.com/gemini-enterprise-cx/cx-agent-studio/guardrail
> As of: 2026-08 · Verified: 2026-08-18 · Status: GA

## What guardrails does CX Agent Studio provide?

Four types, reached via the guardrails button in the agent builder. Every agent application gets
a set of **default guardrails** which you can then modify, so an agent is not unprotected on day
one.

**Prompt Guard** protects against prompt-injection attacks — the documentation's own example is a
user saying "ignore your instructions." It has an enable/disable toggle and accepts a custom
prompt containing your own security-screening instructions.

**Blocklist** prevents specific words and phrases from appearing. Terms are supplied
comma-separated, and matching can be **whole words only**, **any mention** (substring), or
**regex pattern**. Critically, the **blocked content source** is configurable as user input,
agent response, or both — so a blocklist can police what the customer says, what the agent says,
or both directions.

**Safety** enforces responsible-AI practices at three levels. **Relaxed** allows flexible
generation while blocking illicit and harmful content. **Balanced** targets safe interactions and
stops unsafe content. **Strict** applies deep harm filtering and restricts sensitive elements.
Individual safety guardrails can also be adjusted separately from the level.

**Rules** are custom guardrails expressed either as natural-language instructions or as code via
an `after_model_callback` implementation. This is the escape hatch for policy that the other three
types cannot express.

## What happens when a guardrail is violated — the three outcomes

All four guardrail types share the same outcome options, and this is the part worth memorising:
**say exactly** (return a fixed string), **handoff to agent**, or **generate response**.
Violations therefore trigger a configurable outcome rather than an automatic hard block, which
Google frames as enabling graceful degradation. In practice that means a guardrail is also a
routing decision: "handoff to agent" turns a policy trip into a human conversation rather than a
refusal, and for regulated topics that is usually the right answer.

## Guardrails are where human escalation lives, not handoff rules

This is the confusion to head off. **Handoff rules** in CX Agent Studio move control between the
root agent and sub-agents — parent to child, child to parent. They are not the mechanism for
reaching a human. **Human escalation** comes from the guardrail outcome "handoff to agent" and
from the CCaaS platform integration. If someone asks "how do I transfer to a live agent", the
answer involves guardrail outcomes and the deployment channel, not the handoff-rules page.

## Documented limits on guardrails

The guardrails documentation does not specify quantity limits, and it does not publish a
comprehensive best-practice list. Do not assert a maximum number of guardrails or blocklist
entries; verify against current quotas if you are designing something large.
