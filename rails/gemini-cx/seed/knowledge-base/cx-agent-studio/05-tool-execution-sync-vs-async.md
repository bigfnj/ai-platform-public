# Synchronous versus asynchronous tool execution — and the latency thresholds

> Source: docs.cloud.google.com/gemini-enterprise-cx/cx-agent-studio/tool
> As of: 2026-08 · Verified: 2026-08-18 · Status: GA

## Should a tool be synchronous or asynchronous? The latency rule

Every tool in CX Agent Studio is configured as one of two execution types, and Google gives
explicit latency guidance for choosing. **Synchronous (blocking)** "should be used when the tool
execution should block agent response generation until the tool response is available", with an
ideal latency of **under 5 seconds**. **Asynchronous (non-blocking)** "should be used for
non-blocking execution of tools", with an ideal latency of **5 to 60 seconds**.

This is the single most important design decision for perceived quality on a voice channel. A
synchronous tool that takes eight seconds produces dead air, and dead air on a phone call reads
as a broken system. Move anything that slow to asynchronous and give the agent something to say.

## How an asynchronous tool response actually flows

The documented flow has four stages. First the **agent triggers the function call** and awaits a
response. Second the **tool returns a pending status**, literally `"status": "pending"`. Third the
**agent handles the pending response** — it must be instructed what to do while it waits, for
example "ask the user if they have any other questions." Fourth the **agent handles the final
response**: when the real result arrives it is injected as a context tag in this form:

```
<context>function [<tool_name>] completed with response <response JSON></context>
```

The consequence for authoring: an asynchronous tool needs three separate instructions, not one.
You must tell the agent what to say while pending, what to do when the real response lands, and
what to do on error. Omitting the pending instruction is the classic bug — the agent goes silent
mid-call because nobody told it that waiting was a state it could be in.

## Instruction requirements specific to asynchronous tools

Google's best-practice list for asynchronous tools is explicit that you should include detailed
instructions for three scenarios: **pending response handling**, **final response handling**, and
**error scenarios**. Treat that as a checklist per async tool. Error handling in particular tends
to be skipped, and the resulting behaviour — an agent that promises a result it never delivers —
is worse than a tool that fails fast.
