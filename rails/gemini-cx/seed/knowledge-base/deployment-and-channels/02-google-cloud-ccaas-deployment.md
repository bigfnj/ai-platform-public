# Deploying CX Agent Studio to Google Cloud CCaaS — and the us/eu region limit

> Source: docs.cloud.google.com/gemini-enterprise-cx/cx-agent-studio/deploy/ccaas
> As of: 2026-08 · Verified: 2026-08-18 · Status: GA

## How does CX Agent Studio connect to Google Cloud CCaaS?

Through a **conversation profile** with **bidirectional streaming enabled**. The conversation
profile is the integration object: CCaaS talks to your agent application through it. The critical
field is **`useBidiStreaming`**, which must be set to `true`. The other key fields are
**`displayName`**, **`languageCode`** (for example `en-US`), and
**`automatedAgentConfig.agent`**, which references your CX Agent Studio agent.

Note that this path runs through the **Dialogflow API** — further evidence of the Dialogflow
lineage under the GECX branding.

## Prerequisites for a Google Cloud CCaaS deployment

Six things must be true. Your Google Cloud project must be associated with **both** the agent
application and the CCaaS instance, **in the same region**. The **Dialogflow API** must be enabled.
The service account needs **`roles/dialogflow.admin`**. You need a configured CX Agent Studio
agent application and a configured Google Cloud CCaaS contact centre. And critically —

## Only the us and eu multi-regions are supported for CCaaS deployment

**Google Cloud CCaaS deployment supports only the `us` and `eu` multi-regions.** This is a hard
constraint and it is the first thing to check in any non-US, non-European deployment. An
organisation in Australia, Japan, India, Canada, or Latin America that requires in-country data
handling cannot deploy this path today as documented, and needs either a different channel
(telephony partner, web widget, API) or a residency exception. Do not discover this after
architecture sign-off. Verify current regional coverage before committing, since region lists
expand over time.

## Connecting a new versus an existing conversation profile

For a **new** profile, create it with the embedded agent reference in a single POST to the
Dialogflow API endpoint, specifying the agent name. For an **existing** profile, it is a two-step
PATCH: first connect the agent application by setting `automatedAgentConfig.agent`, then enable
bidirectional streaming by setting `useBidiStreaming: true`. The two-step form matters — a profile
that has the agent attached but streaming still disabled will appear configured and will not behave
correctly.

## Voice specifics

Telephony integration is configured through speech-to-text settings, using **`sttConfig`** with a
**telephony model**. Selecting a telephony-tuned recognition model rather than a general one is
what makes recognition acceptable over a compressed phone channel; leaving it on a default is a
common cause of "the bot cannot understand our callers."

## What this page does not tell you

The Google Cloud CCaaS deployment documentation does **not** elaborate human-handoff mechanics or
advanced troubleshooting. Human escalation is governed by guardrail outcomes and by the CCaaS
platform's own routing, so plan that part from the guardrails documentation and your CCaaS
configuration rather than expecting it here.
