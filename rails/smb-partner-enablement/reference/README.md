# Reference — the original prototype

Reconstruction target for this rail: an **SMB partner enablement chatbot / voice agent** built
as a team hackathon prototype in June 2026. The original source was lost; this directory is the
spec we rebuild from.

Screenshots live in `screenshots/`. This file is the written spec derived from them — read it
first, because it records the things a screenshot cannot (model choices, flow, intent).

## Product framing (from the "What This Is" tab)

> "A two-minute AI coach that turns a partner's basic knowledge about their next customer into
> a complete, Microsoft-specific battle plan for the meeting."

| | |
|---|---|
| **Who it's for** | Microsoft's reseller partners — the local IT companies selling Microsoft 365 and Azure to small businesses. |
| **The problem** | Before a customer meeting they need to prep. Right now they either skip it or spend 30 minutes cobbling it together. Inconsistent, slow, and the bad ones cost deals. |
| **What it does** | Pick an industry, answer four quick questions. Thirty seconds in, you get a full meeting kit — what to ask, what products fit, how to handle objections, and exactly what to do in Partner Center after. |
| **Why it matters** | Microsoft's SMB revenue runs through these partners. A prepared partner closes more deals. This makes every partner as good as the best one — in two minutes, on any device. |

The pitch, verbatim, in three beats:

1. Microsoft's SMB channel lives and dies on partner quality. Right now your top 10% of partners
   walk into customer meetings prepared — the rest wing it, pitch the wrong product, and walk
   out without registering the deal.
2. This tool closes that gap in two minutes. Partner answers four questions about their
   customer, gets back a complete meeting prep kit — right questions to ask, right products to
   position, right close. On any device, runs offline, no procurement required.
3. More consistent partners means more deals registered, better product mix, higher close
   rates. At channel scale, that's material revenue.

The value framing to protect: **scalable to all partners · portable from web to mobile · large
SMB TAM**. And on the demo itself — *"the voice is going to carry it."*

## Shell

Top bar: Microsoft logo · `Partner Center` / `SMB Readiness` breadcrumb · an elapsed-time
counter · an **AI Status** pill (green dot) · user avatar.

Three tabs: **What This Is** (💡) · **Scenario Builder** (📋) · **Voice Chat** (🎤).

Footer: `© 2026 Microsoft · Partner Center SMB Readiness · Built for SME&C Account Planning
Hackathon`. Dark theme throughout, Microsoft blue accent, card surfaces a shade above the page.

## The AI Status popover — the two-model claim

This is the detail that drives this rail's architecture. Opening the top-right **AI Status** pill
drops a small popover listing **two models, both resident at once**:

```
LLM (llama3.2:3b)      ● GPU · ready
Voice TTS (Kokoro)     ● GPU · ready
```

Both green, both "GPU · ready", simultaneously. See `../MODELS.md` for how that maps onto this
platform's broker and what had to change to honour it.

## Flow 1 — Scenario Builder

**Step 1 · Pick a scenario.** Four cards, each with an emoji, a title, a blue solution-fit
subtitle, and a one-paragraph situation:

| Scenario | Solution fit | Situation |
|---|---|---|
| 🚗 Auto Dealership | Azure Migration + Copilot for Sales | SMB dealership group moving off on-prem infrastructure, wants sellers using AI-generated cold call scripts. |
| 🍽️ Restaurant Group | Azure Consolidation + Copilot for Frontline Managers | Multi-location SMB restaurant group with disconnected POS systems, paper scheduling, and no centralized data visibility. |
| 🛍️ Retail Chain | Teams Frontline + Copilot for Store Ops | Multi-location SMB retailer with frontline staff, paper schedules, and no shared communication layer across stores. |
| 💼 Professional Services | M365 Security + Copilot for Knowledge Work | Accounting, legal, or consulting firm handling sensitive client data with security gaps and partners asking about AI. |

Caption: *"These scenarios represent common SMB partner situations. Select the one closest to
your customer's situation to begin your pre-call readiness diagnostic."*

**Step 2 · Four questions.** One at a time. Header shows `Question 1 of 4`, a progress bar,
`25%`, and the chosen scenario chip. Each question has a muted **"Why this matters"** note
under it explaining the signal the answer gives, then 4 radio options.

Example (Retail Chain, Q1): *"How many store locations does this retailer operate?"* →
`2–5 locations` / `6–15 locations` / `16–50 locations` / `50+ locations`.

**Step 3 · Generation theatre.** A centered scenario icon with a progress ring, "Building your
sales package", the scenario + solution-fit line, then a checklist that ticks green in sequence:

```
✓ Analyzing diagnostic answers…
✓ Grounding in Microsoft product catalog…
✓ Applying MSEM sales methodology…
✓ Generating discovery playbook…
✓ Building customer Q&A pack…
✓ Drafting ROI summary…
● Finalizing directional close…
```

This is not decoration — each line names a real grounding or generation step, and it is what
covers the model's latency. Worth preserving.

**Step 4 · Diagnostic Complete.** A blue gradient banner: `DIAGNOSTIC COMPLETE — Directional
Close · Action begins here`, holding the **"Your next move"** paragraph. This is the payload;
everything else supports it. Verbatim from the Retail Chain run:

> **Your next move:** Lead with Teams for Frontline Workers, not Azure — the owner will engage
> on staff communication before infrastructure. Propose a 30-day Teams pilot for one location
> using M365 F3 licenses (includes Shifts, Walkie Talkie, Tasks, and Viva Learning). Register
> the deal under **Modern Work** in Partner Center and check for SMB CSP accelerator eligibility
> on Frontline SKUs. If the customer has 15+ locations, flag for co-sell — Microsoft's SMB field
> team has a Frontline motion that can accelerate the deal.

Below it, four output tabs, each with a **🔊 Read aloud** button:

- **📋 Scenario Card** — Customer Profile (industry, size, tech posture, decision-maker type) ·
  Primary Pain / Trigger Event · Microsoft Solution Fit (specific SKUs) · Azure Consumption
  Estimate (a monthly $ range) · Deal Registration Motion.
- **🔍 Discovery Playbook** — the questions to ask on the call.
- **💬 Customer Q&A** — objections and answers.
- **📊 ROI Summary** — customer name placeholder, biggest operational headache, current
  processes, a boxed SOLUTION checklist, then a **BUSINESS IMPACT** row of three stat tiles
  (e.g. `3` monthly cost savings · `95%` improved shift scheduling accuracy · `50%` enhanced
  inventory management efficiency), and a yellow **NEXT STEP** box.

## Flow 2 — Voice Chat (the mobile surface)

Reached from a **floating "📱 Mobile" pill fixed at the bottom-right** of the desktop app — not
a tab. Clicking it overlays a **live in-page phone frame** on top of whatever tab you were on,
captioned "Live mobile preview · tap outside or press `Esc` to exit".

That affordance is what sold "portable from web to mobile" in the room: the presenter never left
the desktop app, and the phone simply appeared over it mid-demo. Preserved in the rebuild as a
`position: fixed` pill plus an overlay that renders the real mobile build in an iframe — an
iframe specifically so the preview cannot drift from what a partner actually gets on a phone.

Empty state: 🎤 glyph, **"Ask anything about Microsoft SMB sales"**, and a muted example line —
*MCEM stages · Azure migration · Copilot · deal registration · co-sell*. A large round blue mic
button with **"Tap to speak"** beneath.

Active state: a **`● Live · Ollama`** green badge next to the "Voice Assistant" title. User turns
are blue right-aligned bubbles; assistant turns are grey left-aligned cards that open with a
muted italic restatement of the situation ("*The customer is expressing hesitation about
purchasing a Copilot 365 license.*") before the substantive answer, then bullets. While speaking,
a red **⏹ Stop voice** pill floats over the transcript.

On narrow widths the tab labels shorten (`What This Is` → `This Is`, `Scenario Builder` →
`Builder`, `Voice Chat` → `Chat`).

Sample exchange from the demo:

> **Partner:** "I'm getting pushback on the sale of Copilot 365 licenses. Can you please give me
> some help?"
>
> **Assistant:** *The customer is expressing hesitation about purchasing a Copilot 365 license.*
> I understand that budget and ROI are top concerns for your organization. However, Copilot 365
> can bring significant productivity gains through AI-powered assistance in Microsoft 365 apps
> like Word, Excel, and Outlook. By streamlining tasks and automating repetitive work, you'll
> free up more time to focus on high-value activities. — followed by bullets on the 30-day
> money-back guarantee, the M365 Business Basic/Standard tie-in, and partner proof points.

## What to carry forward, and what not to

**Carry forward:** the two-minute promise; four questions and no more; the generation checklist;
"Your next move" as the headline output; Read-aloud on every output; the phone preview.

**Do not carry forward as fact:** every Microsoft specific in these screenshots — SKU names,
incentive mechanics, the ROI percentages, the Azure consumption ranges — was generated by a 3B
model during a one-hour hackathon build. Treat all of it as *shape*, not truth. The numbers in
the ROI tiles are illustrative and one is visibly broken (a tile reading `3` under the label
"Estimated monthly cost savings $,000 - $5,500"). Real values belong in
`../seed/knowledge-base/`, sourced and dated, which is the entire reason this rebuild has a RAG
corpus and the original did not.
