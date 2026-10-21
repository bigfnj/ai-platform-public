# Test cases in CX Agent Studio — Scenario versus Golden

> Source: docs.cloud.google.com/gemini-enterprise-cx/cx-agent-studio/evaluation
> As of: 2026-08 · Verified: 2026-08-18 · Status: GA

## What are the two test case types, and when do you use each?

A **test case** is a self-contained scenario for assessing agent performance, and CX Agent Studio
has two kinds that answer different questions.

A **Scenario** test case is AI-powered exploration: you describe a **user goal** and the system
automatically simulates conversations to test robustness. You are asking "can the agent get this
person to their outcome, however they phrase it." The documentation's example goal is "Securely
book a specific room at a chosen hotel and receive a confirmation."

A **Golden** test case is regression testing: it pins a specific ideal conversation path with
expected tool calls. You are asking "does the agent still do exactly what it did before." Use
Scenario cases while you are still shaping behaviour, and Golden cases to stop a fix from
breaking something that already worked.

A **Run** is one complete execution of test cases against an agent version, a **Result** is a
single test case execution within a run, and **Tags** organise test cases.

## How to create Golden test cases — five routes

Golden cases can be created by saving a simulator conversation (menu → **Save as golden**),
importing from conversation history, building from scratch manually, batch-uploading a CSV, or
converting a scenario simulation into a golden conversation. The two worth designing a process
around are **save from simulator** and **import from conversation history** — both turn something
that actually happened into a regression test, which is cheaper and more representative than
authoring cases from imagination.

## What a test case can assert

**Scenario expectations** support conditions of **"Must have"**, **"Must not have"**, **"After
tool call"**, and **"Variable value"**, and can assert on a message (expected agent response) or a
tool call (a specific invocation with inputs and outputs). Scenarios also take **Variables** used
during execution.

**Golden expectations** can assert a **Message** (expected text, evaluated for semantic match
rather than string equality), a **Tool call** (specific tool with expected parameters), or an
**Agent Handoff** (transfer to a human or another bot). Note that message matching is semantic —
a Golden test does not break because the wording changed, only because the meaning did.

## Personas — simulate a specific kind of caller

Scenario testing supports customisable simulated user **personas**, with a name, a personality,
and context such as age, location, and reason for calling. If no persona is specified, one is
selected at random. Create them under Evaluate tab → Persona management → Add persona, then
select one for a scenario run. This is the mechanism for testing the difficult caller
deliberately rather than hoping the random simulation produces one.

## Import and export

Test cases, runs, and results export in **YAML or JSON** as a zip file, and import by uploading a
zip; conflicts are flagged for resolution. This is what makes an evaluation suite promotable
between environments and reviewable in source control.
