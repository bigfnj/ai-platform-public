# Every CX Agent Studio evaluation metric and exactly what it measures

> Source: docs.cloud.google.com/gemini-enterprise-cx/cx-agent-studio/evaluation
> As of: 2026-08 · Verified: 2026-08-18 · Status: GA

## What metrics does CX Agent Studio evaluation produce?

Six, and their scales differ, which matters when you build a dashboard on top of them.

**Tool Correctness** is the "percentage of expected parameters matched given an expected tool call
and its expected parameter values." A missed tool call scores 0. A call with no parameters scores
1 if the call is present.

**User Goal Satisfaction** is binary (0 = no, 1 = yes) and applies to Scenario test cases only. It
measures whether the simulated user believes the objective was achieved. It returns **-1 if the
goal is too vague** — which is a diagnostic about your test case, not about your agent.

**Hallucinations** is binary (0 = no, 1 = yes) and detects claims unsupported by context, where
context means preceding turns, variables, tool calls, and instructions. Two important scoping
facts: it is **only computed for turns with tool calls**, and it returns **N/A for responses that
contain no factual claims**. So a low hallucination count is not automatically good news — check
how many turns were eligible to be scored at all.

**Semantic Match** measures "the extent to which an observed agent utterance matches with an
expected agent utterance" on a **0–4 scale**, computed per turn for Golden cases. Note this is the
one metric that is neither binary nor a percentage.

**Scenario Expectations** is binary (0 = no, 1 = yes) and measures whether behaviour satisfied the
simulated user's expectations. Tool-call expectations are scored like Tool Correctness but **do
not penalise unexpected calls**; agent-response expectations check for expected strings.

**Task Completion** is the composite, defined as `User_Goal_Satisfied AND
no_hallucinations_detected AND Expectations_Satisfied`. This is the one to report upward, because
it is the only metric that fails when any dimension fails.

## Configuring pass/fail

Golden configuration exposes pass/fail criteria logic, **turn-level thresholds** (semantic
similarity, tool correctness, hallucinations), **expectation-level thresholds** (tool
correctness), the golden run method (**naive** or **stable replay**), and **tool fake** to use
mocked data instead of real API calls. Scenario configuration exposes pass/fail criteria logic,
the conversation initiator (user or model), and tool fake settings. Audio evaluation offers
recording-based assessment.

**Tool fake is the setting that makes evaluation safe to run often.** Without it, a regression
suite that exercises a checkout tool places real orders.

## Stable replay versus naive replay — pick deliberately

**Stable replay** validates in a consistent environment without shifting context or input, using
expected variables and tool responses as injection context. **Naive replay** does not use expected
values to alter the conversation flow. Stable replay isolates the change you are testing; naive
replay tells you what would really happen. Use stable replay for regression gating and naive
replay before a release.

## Finding causes, not just failures

When running an evaluation you can select specific agent versions or auto-save the draft as a new
version, and enable **"Find issues with AI"** to generate a **loss report** — Google notes this is
optimal with **three or more runs**. Results show as rectangular icons (red X failed, green
checkmark passed) with turn-by-turn comparisons, and an **"Ask Gemini"** helper agent offers
suggestions. The three-run guidance is worth honouring: a single run on a stochastic system tells
you very little about a marginal failure.
