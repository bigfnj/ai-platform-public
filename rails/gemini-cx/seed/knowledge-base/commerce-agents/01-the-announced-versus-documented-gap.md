# Commerce Agents — announced at NRF, but the docs say "coming soon"

> Source: docs.cloud.google.com/gemini-enterprise-cx/commerce-agents; NRF launch release 2026-01-11
> As of: 2026-08 · Verified: 2026-08-18 · Status: COMING SOON per documentation

## Can I buy and deploy the Shopping agent or Food Ordering agent today?

Not according to Google's own documentation. As of August 2026 the Commerce Agents documentation
page states **"Commerce agents coming soon"**, and it publishes no configuration instructions, no
deployment procedure, and no backend integration specification. It does not even name the
individual agents.

This directly contradicts the impression left by the 11 January 2026 launch, which announced the
**Shopping agent** and the **Food Ordering agent** as headline capabilities with named customers
and stated they "can be quickly deployed in days." Both statements are real. The press release
describes intent and pilots; the documentation describes what is generally buildable. **If a
commerce-agent capability is load-bearing in a proposal, verify current status with the Google
Cloud account team before committing to it** — this is the single most likely place for a GECX
conversation to over-promise.

## What Commerce Agents are supposed to be

Google's definition is that "commerce agents provide complete end-to-end solutions for
commerce-specific use cases", connecting "frontend user interfaces like chat and voice directly to
backend tools such as product searches and adding items to a shopping cart." They are
differentiated from chatbots by handling "complex requests using complex reasoning, multimodal
interactions and executing consented actions." Chat and voice are the identified front ends.

## What the Shopping agent was announced as doing

From the launch material: complex reasoning for filtered product searches based on customer
preferences; multimodal interactions taking image, video, and voice input; executing consented
actions such as adding to cart and checking out; and cross-referencing specifications against
real-time availability. It was also described as delivering "a complete end-to-end solution,
connecting frontend interfaces like chat and voice, directly to backend tools", and as connecting
to Customer Experience Agent Studio "to ensure every support interaction is informed by historical
context."

## What the Food Ordering agent was announced as doing

A multimodal and multilingual agent offering conversational ordering across **mobile apps,
websites, telephone, kiosks, and in-car systems**, with intelligent upselling based on menu
context and business analytics for operators. The in-car and kiosk channels are the notable part —
this is a wider surface list than the other GECX components target.

## What to build instead while Commerce Agents are pending

A commerce journey can be assembled today in **CX Agent Studio** using the tool types that are
GA: OpenAPI tools or Integration Connector tools against catalogue, inventory, cart, and checkout
APIs, data store or file search tools for product content, and widget tools for rich product
display. That is more work than adopting a prebuilt agent, and it is honest about what exists.
Frame prebuilt Commerce Agents as a future accelerator, not as the plan of record.
