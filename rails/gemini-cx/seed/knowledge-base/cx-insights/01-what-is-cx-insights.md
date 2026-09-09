# Customer Experience Insights — the analytics layer, and the CCAI Insights lineage

> Source: docs.cloud.google.com/gemini-enterprise-cx/insights
> As of: 2026-08 · Verified: 2026-08-18 · Status: GA

## What is CX Insights?

CX Insights analyses contact-centre interaction data so teams can "detect and visualize patterns
in their contact center data." It applies machine learning to conversations to surface business
intelligence and to flag interactions worth a human review. Its core ML capabilities are **agent
and caller sentiment detection**, **entity identification**, **call topic categorisation**, and
**automatic interaction flagging for review**.

Its pricing page still lives at `cloud.google.com/contact-center/insights/pricing`, which is the
clearest single piece of evidence that GECX is a rebuild of Google's Contact Center AI portfolio
rather than a new product line. If a customer already licenses CCAI Insights, they are already
partway into GECX.

## The named CX Insights feature set

The documented features are **Analysis rules**, **Autolabeling and correlation rules**,
**Generative FAQ**, **Quality AI** (with sub-components for conversation assessments, sampling
rules, and agent/virtual-agent platforms), **Smart highlights**, and **Topic modeling**.

**Quality AI is the one to lead with for a contact-centre operations buyer.** Automated quality
assessment against sampling rules replaces a manual QA process that typically reviews a
single-digit percentage of calls, and it covers virtual-agent platforms as well as human agents —
so the same scorecard can be applied to the bot.

**Topic modeling** is documented as its own feature set with an overview, operations, best
practices, and export/import of topic models. The exact algorithm is not published, so describe it
as topic modelling over conversation data rather than claiming a specific method.

## How conversation data gets into CX Insights

Five routes. **API ingestion** of audio or transcript files, **console upload** of files directly,
**agent integration** importing straight from Conversational Agents and Agent Assist, **bulk
analyze conversations** for volume, and **datasets** for organising conversations. **SIPREC** is
covered for audio conversation data, and **Dialogflow runtime integration** is documented for live
capture.

## Where the data goes afterwards

Analysis output exports to **BigQuery** for custom analysis, with **17 documented BigQuery schema
versions** — a detail worth knowing before someone builds a rigid pipeline on one of them.
Visualisation is supported through **Looker**, there is a **Google Sheets** integration, and
**Cloud Pub/Sub** notifications are available for event-driven flows. Metrics can also be queried
through the REST API, and there is a performance-overview query surface.

This is the component that makes GECX auditable, so it is where a reporting, retention, or
data-residency conversation naturally lands — not in CX Agent Studio.

## Access control is finer-grained here than elsewhere

CX Insights documents a **fine-grained access control** framework, **authorized views** management,
and **CMEK**. That matters because conversation transcripts are among the most sensitive data an
enterprise holds — they contain whatever the customer said out loud, including things no form
would ever have captured. Expect this to be the component that attracts the most scrutiny in a
security review, and note that **data retention policies** are separately documented.
