# GECX enterprise security and governance controls

> Source: docs.cloud.google.com/gemini-enterprise-cx/cx-agent-studio (Secure section); insights and agent-assist docs
> As of: 2026-08 · Verified: 2026-08-18 · Status: GA

## What security controls does CX Agent Studio provide?

Four, and they are the standard Google Cloud enterprise set rather than anything bespoke:
**IAM access control** for role-based permissions, **audit logging** with granular records,
**VPC Service Controls** for perimeter enforcement, and **CMEK** — customer-managed encryption
keys. CMEK also appears in Agent Assist and in CX Insights, so key management can be consistent
across the components.

The practical significance is that a GECX security review is largely a Google Cloud security
review. If an organisation already has an approved pattern for IAM, VPC-SC, and CMEK on Google
Cloud, most of the work is done and the novel questions are about conversation data specifically.

## Which component attracts the most security scrutiny, and why

**CX Insights.** It holds conversation transcripts, and transcripts contain whatever a customer
said out loud — including personal, financial, and health detail that no structured form would have
captured. It is also where conversation data from every other component converges. Accordingly it
has the most granular controls of the three: a **fine-grained access control** framework,
**authorized views** management, CMEK, documented **data retention policies**, and audit logging.

Scope a GECX security review around CX Insights first, then Agent Assist (which surfaces knowledge
to agents), then CX Agent Studio.

## Reaching internal systems safely

Agents need tools, and tools usually call internal APIs. The relevant controls are **VPC Service
Controls** plus the deploy section's **inbound and outbound networking** configuration. Plan the
egress path from an agent's tools to internal systems early — it is the point where an agent
project meets the network team, and it is more often the schedule risk than the AI work is.

## Guardrails are a governance control, not just a safety feature

The **Prompt Guard**, **Blocklist**, **Safety**, and **Rules** guardrails are the enforcement
point for brand policy and regulatory language, and each supports the outcome **handoff to agent**.
For a regulated topic, the correct design is frequently not to have the agent answer carefully but
to have a guardrail route the conversation to a human. Present guardrails in a compliance
conversation as controls with auditable outcomes, and see the guardrails file in the
cx-agent-studio collection for the exact types and configuration.
