# GECX pricing — what is actually published, and what is not

> Source: cloud.google.com/products/gemini-enterprise-for-customer-experience/cx-agent-studio/pricing; cloud.google.com/agent-assist/pricing; cloud.google.com/contact-center/insights/pricing
> As of: 2026-08 · Verified: 2026-08-18 · Status: partially published — verify every figure before quoting

## How is CX Agent Studio priced?

**Per session**, with a voice overage. The one figure that is reliably documented is the voice
overage: for voice conversations, **after every 5 minutes (300 seconds) you are billed at an
overage rate of $0.0025 per second instead of per session**. Base per-session rates for chat and
voice exist on the pricing page but could not be reliably extracted at the time of writing, so
**do not quote a base session price from this knowledge base** — read it off the live pricing page.

The commercially important insight is the shape rather than the number. A per-session model with a
five-minute voice inclusion means **your cost is driven by conversation length, not by token
volume**. That inverts the usual LLM cost intuition: a long, rambling, badly-designed conversation
is directly more expensive, and every minute of latency you remove is money. At $0.0025 per second,
a call running ten minutes instead of five adds roughly **$0.75** in overage per call — trivial
once, material across a million calls.

## There are separate pricing pages per component

GECX is not one price. **CX Agent Studio** has its own pricing page. **Agent Assist** has its own
at `cloud.google.com/agent-assist/pricing`. **CX Insights** has its own at
`cloud.google.com/contact-center/insights/pricing`. A GECX estimate is therefore an addition across
components, and a customer adopting all three is on three meters. Always establish which components
are in scope before producing any number.

## Do not confuse GECX pricing with Gemini Enterprise seat pricing

The parent **Gemini Enterprise** platform is licensed **per seat** — publicly cited as starting
around **$21 per seat per month** for a Business edition and **$30 and up** for Standard/Plus, with
consumption charges beyond quota. **GECX is not seat-priced.** A contact-centre agent solution
priced per employee seat makes no sense when the users are customers, so GECX is metered on
sessions and usage instead.

This is a live confusion in the market because the names are nearly identical. If someone quotes a
per-seat figure for GECX, they are quoting the wrong product. Establish which product before
discussing money, and treat the seat figures above as market-reported rather than verified — they
came from third-party summaries, not from a Google pricing page read directly.

## What else bills separately

Expect additional metered charges outside the component prices. Speech-to-Text and Text-to-Speech
usage, data store and grounding usage, Integration Connectors, and BigQuery storage and query for
CX Insights exports all have their own pricing. A voice deployment through a partner such as
AudioCodes also carries that partner's licensing plus SBC infrastructure. None of these were
verified against a live page for this file, so treat them as items to confirm rather than as a
priced list.

## The honest answer to "what will this cost us"

"It depends on which components, how many conversations, and how long they run — and I will get you
a modelled figure rather than guess." Then build the model from the live pricing pages with the
customer's own volumes and average handle time. Google Cloud is pay-as-you-go with custom quotes
available from sales for an organisation's specific usage, so a real number comes from the account
team. An invented per-conversation price is the fastest way to lose a commercial conversation.
