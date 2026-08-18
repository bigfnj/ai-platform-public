# Handoff rules in CX Agent Studio — deterministic transfer between agents

> Source: docs.cloud.google.com/gemini-enterprise-cx/cx-agent-studio/handoff
> As of: 2026-08 · Verified: 2026-08-18 · Status: GA

## What are handoff rules, and why use them instead of instructions?

Handoff rules give **deterministic** control over transfers between a parent agent and a child
agent. Google positions them explicitly against the two alternatives: instructions "lack
determinism", and callbacks "require code". A handoff rule is the middle option — reliable
without being a coding exercise. If a transfer must happen every time a condition holds, an
instruction is the wrong tool, because the model may or may not comply.

## Handoff rules only move between agents — they are not human escalation

The documented directions are **forward handoffs** (parent agent to child or sub-agent) and
**backward handoffs** (child or sub-agent back to parent). The handoff documentation does not
cover transfer to a human representative at all. Human escalation is a **guardrail outcome**
("handoff to agent") and a function of the CCaaS or telephony integration. Confusing the two
sends you looking for a capability on the wrong page.

## How to configure a handoff rule

The flow is: click the **+** button under an agent node, select **Add handoff rules**, choose a
child agent from the list, select the transfer direction (to parent or to child), define the
conditions through the UI or in code, and save.

## Conditions can be variable-based or code-based, with real constraints

**Variable-based conditions** support only **text, number, and boolean** variable types. Multiple
conditions can be linked, but with a **single AND/OR operator** — you cannot mix logical
operators in the UI. If you need mixed boolean logic you must drop to code.

**Code-based conditions** implement a callback returning a boolean:

```
def should_trigger_transfer_callback(callback_context: CallbackContext) -> bool:
      return True
```

Variables are read inside it via `callback_context.variables['variable_name']`.

## Three limitations to design around

First, **blocking rules support only variable conditions, not code** — so the more expressive
option is not available everywhere. Second, **complex rules created through the API appear
read-only in the UI**, which means an API-authored rule can become something your designers can
see but not edit. Third, the single-operator constraint above. Between them these push any
non-trivial routing logic toward code, so decide early which layer owns routing rather than
discovering the ceiling halfway through.
