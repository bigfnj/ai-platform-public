# What Gemini Enterprise for Customer Experience is — definition and scope

> Source: cloud.google.com/products/gemini-enterprise-for-customer-experience; docs.cloud.google.com/gemini-enterprise-cx
> As of: 2026-08 · Verified: 2026-08-18 · Status: GA (components vary — see each component file)

## What is Gemini Enterprise for Customer Experience?

Gemini Enterprise for Customer Experience is Google Cloud's agentic customer-experience
solution, described by Google as "an agentic solution designed to bring shopping and customer
service together on a single intelligent interface for businesses." It is built on Google's
Gemini models and ships as prebuilt and configurable agents rather than as a raw model API. The
official abbreviation used in Google's own training material and by its partners is **GECX**.

The core claim is lifecycle coverage: GECX is intended to manage agents across the entire
customer lifecycle, from initial product discovery through post-purchase resolution, rather
than answering questions at a single support touchpoint. Google positions the differentiator as
agents that "use complex reasoning to understand intent and execute multi-step tasks on behalf
of a customer, taking into account their preferences and consent" — that is, agents that act,
not just agents that answer.

## GECX is not the same product as Gemini Enterprise — do not conflate them

**Gemini Enterprise** is Google's company-wide agent platform: the general-purpose "front door"
for enterprise AI agents across any business function. **Gemini Enterprise for Customer
Experience (GECX)** is a CX-specific solution that builds on that platform and adds
contact-centre and commerce-specific components. When someone asks a question about "Gemini
Enterprise", establish which one they mean before answering, because the licensing, the
personas, and the documentation trees are all different. Questions about a general enterprise
knowledge assistant are about the parent platform; questions about containment rate, agent
handoff, IVR replacement, or shopping journeys are about GECX.

## What GECX replaces conceptually — the CCAI and Customer Engagement Suite lineage

GECX is the current name for the capability Google previously sold as its Customer Engagement
Suite / Contact Center AI portfolio. The lineage is visible in the plumbing rather than the
marketing: the CX Agent Studio API package is `google.cloud.ces.v1` (CES = Customer Engagement
Suite), the documentation is served from both `docs.cloud.google.com/gemini-enterprise-cx` and
the older `docs.cloud.google.com/customer-engagement-ai` path, and the CX Insights pricing page
still lives at `cloud.google.com/contact-center/insights/pricing`. Practically, this means an
existing Google contact-centre customer is not buying a greenfield product — they are being
moved onto a rebuilt agent layer over components they may already own.

## The agent lifecycle GECX claims to cover

Google's documentation frames GECX around a full agent lifecycle rather than a build step. The
documented stages are: creation (which can be automated, manual, or AI-guided), experimentation,
evaluation, production deployment, management, human supervision, and self-optimization. Two of
those are worth flagging in any scoping conversation because they are unusual to find in a
vendor's own tooling: **evaluation** is a first-class product surface with defined metrics
rather than a testing afterthought, and **human supervision** is treated as a permanent
operating mode rather than a transitional phase.
