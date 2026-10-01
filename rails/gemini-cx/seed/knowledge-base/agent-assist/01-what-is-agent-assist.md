# Agent Assist — real-time help for the human on the call

> Source: docs.cloud.google.com/gemini-enterprise-cx/agent-assist
> As of: 2026-08 · Verified: 2026-08-18 · Status: GA

## What is Agent Assist in GECX?

Agent Assist supports the contact that a bot did **not** contain. It delivers real-time
suggestions and automation to human and virtual agents during a live conversation, to lift agent
productivity and conversation quality. Where CX Agent Studio tries to handle the contact without a
person, Agent Assist accepts that a person is involved and makes that person faster and more
consistent.

This distinction is the one to lead with in any GECX conversation, because buyers frequently
assume the two products compete. They do not: a mature deployment runs both, and CX Insights then
tells you which contacts to move from the second column to the first.

## Every named Agent Assist feature

**Generative Knowledge Assist** uses generative AI to surface contextual knowledge during the
conversation, with proactive variants that anticipate what the agent will need and filtering to
refine suggestions.

**Summarization with custom sections** generates conversation summaries against a section
structure you define, and includes automatic evaluation metrics for summary quality. The custom
sections matter operationally — a summary shaped to your after-call-work fields is one that can be
written straight into the CRM.

**Smart Reply** offers AI-generated response suggestions, supports custom model training, and has
a simulator for testing before deployment.

**Sentiment Analysis** analyses emotional tone within the conversation so the agent can adjust.

**Live Translation (A2A)** enables agent-to-agent translation during conversations for multilingual
interactions.

**AI Coach** provides real-time coaching via generative AI, with tool integration, OpenAPI
support, and datastore integration for reaching external information.

**Companion Agent** is virtual-agent support working alongside the human agent.

**Supervisor Assist** provides monitoring and guidance for supervising both human and virtual
agents, with separate user guides for each case.

**Voice Transcription** converts speech to text during calls, supporting **Chirp 3** models and
intermediate transcription via **Pub/Sub** for real-time processing.

**Real-Time Entity Extraction** identifies and extracts entities from the conversation as it
happens.

## What Agent Assist needs before it works

Three inputs, and a project that skips them stalls. **Conversation profiles** define which
suggestion features are enabled and how they behave — this is the central configuration object.
**Knowledge bases** hold the documents and FAQs that populate suggestions, with import/export and
specialised datastore formats supported. **Conversation datasets** supply historical conversation
data for model training and evaluation.

The platform uses prebuilt generative models including Gemini capabilities, and supports custom
model training for Smart Reply and knowledge-specific suggestions.

## Deprecated features — do not design around these

Agent Assist documentation carries deprecation notices for **Article Suggestion**, **FAQ Assist**,
and **Smart Compose**. These were the older, pre-generative suggestion features. If you inherit a
design document or a competitor comparison that references them, it is out of date; the current
equivalents are Generative Knowledge Assist and Smart Reply.
