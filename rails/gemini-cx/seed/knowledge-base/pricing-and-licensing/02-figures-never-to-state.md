# Figures and claims to refuse rather than guess

> Source: synthesis of what could and could not be verified across Google Cloud pages, 2026-08-18
> As of: 2026-08 · Verified: 2026-08-18 · Status: standing rule

## What figures should I refuse to state about GECX?

Refuse these, and say why rather than approximating. **Base per-session prices** for CX Agent
Studio chat or voice — the pricing page exists but the figures were not verifiable at the time this
corpus was written. **Any containment, deflection, CSAT, or AHT improvement percentage** — Google
published no such figures at launch, and the named customers announced intent rather than results.
**Any latency figure in milliseconds** for voice or audio-to-audio — Google and AudioCodes both
describe low latency qualitatively and publish no benchmarks. **Any quota or system limit** for
tools per agent, guardrails per application, or instruction length — the relevant documentation
pages explicitly do not state them.

Also refuse **the specific membership of the 40+ language list or the 10-language audio-to-audio
list**. The counts are published; the enumerations are not, at least not in the material surveyed.
"Over 40" is a count, not a list, and a customer whose language turns out to be excluded after
sign-off has a legitimate grievance.

## Why refusing is the better answer

The failure mode this prevents is concrete. Someone asks what containment rate to expect, an
invented "typically 60–70%" becomes a slide, the slide becomes a business case, and the business
case becomes a target the delivery team cannot hit because it was never sourced. "Google has not
published containment benchmarks for GECX, and the launch customers announced adoption rather than
results — we should baseline your current containment and model from that" is a more useful answer
and a defensible one.

## Claims that are safe to make because they are sourced

For contrast, these are verifiable and can be stated directly: the **11 January 2026** launch date
at **NRF 2026**; the **four components** and which three are GA versus Commerce Agents being
"coming soon"; the **three supported Gemini models** and their GA/Preview status; **over 40
languages** for agents and **10 core languages** for audio-to-audio; the **17 tool types**; the
**four guardrail types** and their three outcomes; the **six evaluation metrics** and their scales;
the **$0.0025 per second** voice overage after **300 seconds**; the **us and eu only** restriction
on Google Cloud CCaaS deployment; and that **global endpoints do not support data residency**.

## The status vocabulary to use precisely

Say **GA** when documentation and reference material exist. Say **Preview** for `gemini-3-flash`
and Google Maps tools. Say **coming soon** for Commerce Agents, because that is the documentation's
own word. Say **announced** for the Shopping and Food Ordering agents as products. Four different
words for four different levels of commitment — collapsing them into "available" is the most common
way GECX gets over-sold.
