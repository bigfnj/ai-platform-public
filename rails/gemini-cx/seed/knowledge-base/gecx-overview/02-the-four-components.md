# The four components of GECX — what each one is for

> Source: docs.cloud.google.com/gemini-enterprise-cx (documentation landing page)
> As of: 2026-08 · Verified: 2026-08-18 · Status: three GA, Commerce Agents coming soon

## What are the four components of Gemini Enterprise for CX?

GECX is documented as four products under one solution, and a real deployment usually uses more
than one. **CX Agent Studio** is the builder: it creates AI agents that combine generative AI
with deterministic functionality, to deliver proactive, personalized self-service. **Agent
Assist** works the other side of the desk, giving in-the-moment help to human customer-care
representatives. **Customer Experience Insights (CX Insights)** analyses conversation data to
produce KPIs, topic categorisation, and improvement areas. **Commerce Agents** are prebuilt
end-to-end agents for commerce use cases, connecting chat and voice front ends directly to
backend tools.

The division of labour is the useful part: Agent Studio automates contacts, Agent Assist makes
the contacts that still reach a human go better, CX Insights tells you which contacts to go
after next, and Commerce Agents are a shortcut for retail and food-service journeys you would
otherwise build yourself.

## Which GECX components are actually available today?

CX Agent Studio, Agent Assist, and CX Insights are documented as live products with full
reference documentation, client libraries, and API surfaces. **Commerce Agents are not:** their
documentation page states "Commerce agents coming soon" as of August 2026, even though the
Shopping agent and Food Ordering agent were both announced as part of the January 2026 launch.
See the commerce-agents collection for the full detail on that gap — it is the single most
likely place for a GECX conversation to over-promise.

## How the four GECX components share conversation data

CX Insights is the integration point. Its documentation states that it "seamlessly integrates
with all other Gemini Enterprise for Customer Experience products", and it can import
conversations directly from Agent Assist and from the conversational-agents platform, in
addition to API ingestion of audio or transcript files and console upload. Analysis output can
be exported to BigQuery for custom analysis and visualised in Looker. This matters
architecturally: conversation data converges in CX Insights, so that is where a reporting or
data-residency conversation ends up, not in Agent Studio.

Google also markets the reverse direction — the Customer Experience Agent Studio "connects
directly with the Shopping agent to ensure every support interaction is informed by historical
context" — so context is intended to flow between the commerce journey and the support journey
rather than each starting cold.
