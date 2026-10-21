# GECX launch timeline and what "available" means as of August 2026

> Source: googlecloudpresscorner.com 2026-01-11 press release; product and documentation pages
> As of: 2026-08 · Verified: 2026-08-18 · Status: mixed — read the per-capability status

## When did Gemini Enterprise for Customer Experience launch?

Google Cloud announced Gemini Enterprise for Customer Experience on **11 January 2026**, timed
to **NRF 2026** (the National Retail Federation annual show), under the headline "Google Cloud
Brings Shopping and Customer Service Together with Gemini Enterprise for Customer Experience."
The retail-show timing explains the shape of the announcement: the commerce agents and the
retail customer logos led, and the contact-centre components that were already shipping were
positioned as supporting cast.

The launch announced GECX itself plus three named things: the **Shopping agent**, the
**Customer Experience Agent Studio**, and the **Food Ordering agent**. Google's stated
deployment claim was that the prebuilt agents "can be quickly deployed in days."

## "Announced", "GA", "Preview" and "coming soon" are four different answers

Anyone planning a GECX build needs the distinction, because the January announcement and the
August documentation do not agree on what exists:

- **GA and documented:** CX Agent Studio, Agent Assist, CX Insights. These have reference docs,
  client libraries in multiple languages, REST and RPC APIs, and IAM/audit/CMEK coverage.
- **Preview:** individual features inside those products, most notably the `gemini-3-flash`
  model option in CX Agent Studio and Google Maps tools.
- **Coming soon:** Commerce Agents, per their own documentation page as of August 2026.
- **Announced only:** the Shopping agent and Food Ordering agent as discrete configurable
  products. They were named in the January press release and demonstrated with named customers,
  but the documentation that would tell you how to configure and deploy them is not published.

The practical rule: if a capability came from the press release, treat it as a roadmap
commitment and verify current status with your Google Cloud account team. If it came from
`docs.cloud.google.com`, treat it as buildable today.

## What Google committed to on data handling at launch

Two commitments were made in the launch material and are worth repeating verbatim in any
security review, because they are the questions that come up first. Google stated that
**customer data is not used for model training**, and that the solution includes **built-in
brand policy and legal compliance mechanisms**. Both statements come from launch communications
rather than a contractual document, so confirm the specifics against your Google Cloud
agreement and the current Service Specific Terms before repeating them as contractual.

## Who at Google owns this publicly

Two names recur in GECX communications and are useful for sourcing. **Darshan Kantak**, VP of
Applied AI at Google Cloud, gave the launch quote describing GECX as "combining the best of
Google Cloud's AI and infrastructure with a business's own institutional intelligence to power a
truly agentic commerce journey." **Brian Stavis**, GTM Lead for Applied AI at Google, is the
voice in the partner-facing material, including the March 2026 TTEC Digital discussion of GECX.
