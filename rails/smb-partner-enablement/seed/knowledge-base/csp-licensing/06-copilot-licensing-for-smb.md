# Microsoft 365 Copilot licensing for SMB

> Source: https://learn.microsoft.com/en-us/microsoft-365/copilot/microsoft-365-copilot-licensing · https://learn.microsoft.com/en-us/partner-center/pricing/new-commerce-promotions · https://learn.microsoft.com/en-us/office365/servicedescriptions/office-365-platform-service-description/office-365-plan-options
> As of: 2026-08 · Retrieved: 2026-08-14

## How is Microsoft 365 Copilot licensed?

Microsoft 365 Copilot is sold as an **add-on plan**, not a standalone product. It sits on top of an existing eligible Microsoft 365 subscription, and Microsoft is explicit that for a Copilot licence to be successfully assigned to a user, that user must already have an eligible Microsoft 365 base licence. That has a practical consequence for a partner: a Copilot deal is really two line items — the qualifying base plan (which the customer may already own, or may need upgrading into) and the Copilot add-on per user. If the customer's base plan doesn't qualify, the Copilot conversation becomes a base-plan upgrade conversation first.

## Which base licences qualify for Microsoft 365 Copilot Business?

Microsoft 365 Copilot Business is the SMB-oriented variant, and its licensing prerequisites are the Business family plans: **Microsoft 365 Business Basic, Microsoft 365 Business Standard, Microsoft 365 Business Premium** and **Microsoft 365 Apps for business**. That is the whole list for this SKU. Because all four of these are Business-family plans, Copilot Business is naturally scoped to customers sitting inside the 300-seat Business family cap. For an SMB partner this is the most common Copilot attach: the customer is already on Business Standard or Business Premium and adds Copilot Business per user.

## Which base licences qualify for the full Microsoft 365 Copilot add-on?

The prerequisite list for the full Microsoft 365 Copilot add-on is much broader and includes the SMB plans as well as enterprise ones. **Microsoft 365 plans**: E7, E5, E3, F1, F3, Business Basic, Business Premium, Business Standard, Apps for business and Apps for enterprise. **Office 365 plans**: E5, E3, E1 and F3. **Microsoft Teams plans**: Teams Essentials, Teams Enterprise and Teams EEA. **Exchange plans**: Exchange Kiosk, Plan 1 and Plan 2. **SharePoint plans**: SharePoint Kiosk, Plan 1 and Plan 2. **OneDrive for work and school**: Plan 1 and Plan 2. **Planner and Project**: Microsoft Planner Plan 1 (formerly Project Plan 1), Project Plan 3, Project Plan 5 and Project Online Essentials. **Visio**: Plan 1 and Plan 2. Plus Microsoft Clipchamp. Microsoft also notes that customers with Education or Business subscriptions that don't include Teams can still purchase Microsoft 365 Copilot licences.

## What is the difference between Copilot Chat and a paid Microsoft 365 Copilot licence?

This distinction decides whether a customer needs to buy anything at all, so get it right. **Copilot Chat** is an AI prompt-and-response experience that is automatically included, at no extra cost, for organizations with an eligible Microsoft 365 subscription. It comes in two forms: **web-based chat**, which shows results from the internet and is the part included free with an eligible subscription; and **work-based chat**, which shows results the user's Microsoft Entra work or school account can access — and work-based chat requires a Microsoft 365 Copilot licence. So the free tier gives grounded-on-the-web chat; the paid licence is what grounds Copilot in the customer's own tenant data and lights up the in-app experiences.

## Is Copilot available for Government and Education customers?

Yes, with separate prerequisite lists. For **US Government**, Microsoft 365 Copilot is available in GCC, GCC-High and DoD cloud environments as an add-on to Microsoft 365 G3, G5 and F1; Office 365 G1, G3, G5 and F3; Exchange Plan 1, Plan 2 and Kiosk; SharePoint Plan 1 and Plan 2; OneDrive for Business Plan 1 and Plan 2; Project Online Essentials; and Visio Plan 1 and Plan 2. For **Education**, an academic offering is available for faculty, staff and students aged 13 and older, on Microsoft 365 A1/A3/A5 and Office 365 A1/A3/A5 — and Microsoft names CSP as one of the two purchase routes for these, alongside Enrollment for Education Solutions (EES).

## What does a partner have to do after selling Copilot licences?

Selling the licence is not the deployment. Microsoft points partners and admins at the Microsoft 365 Copilot setup guide in the Microsoft 365 admin center, which walks through assigning the required licences during the Rollout phase, and at the broader "Set up Microsoft 365 Copilot" admin guide covering the other things that need configuring — reviewing Microsoft 365 Apps privacy settings, setting update channels and so on. There are also app and network requirements to validate before rollout. Microsoft recommends using the Copilot License Details diagnostic to verify that a specific user account actually meets the requirements to access Copilot features, which is the fastest way to diagnose "I bought it but the user can't see it."

## Are there Copilot promotions a CSP partner should check for?

Yes, and they change. Microsoft's promotions documentation uses Copilot promotions as its worked examples, including a bundle promotion described as "Bundle & Save: Up to 10% off Microsoft 365 Business Premium and Microsoft 365 Copilot Business" with a seat constraint of 50–500 seats, and two overlapping Microsoft 365 Copilot promotions where the deeper discount is applied automatically. Treat those as illustrations of the mechanism, not as current offers — the authoritative sources are the promotions list downloadable from the Pricing workspace in Partner Center (filtered by market and segment), the `getPromotions` API, and the Global Promo Readiness Guide. Always confirm on the Partner Center review cart screen that the expected promotion has actually applied before submitting the order.

## What should a partner check before quoting Copilot to an SMB customer?

Work through four checks. First, does every intended user already hold a qualifying base licence, and if not, what is the upgrade cost of getting them there? Second, is the right SKU **Copilot Business** (Business-family base plans) or the full **Microsoft 365 Copilot** add-on, since their prerequisite lists differ? Third, does the customer actually need paid Copilot, or would the free web-based Copilot Chat included with their existing eligible subscription cover the stated use case? Fourth, has anyone validated the app and network requirements and the data readiness of the tenant — because work-based Copilot grounds on what the user can already access, and a tenant with poor permissions hygiene will surface that immediately.
