# How does Microsoft define the SMB segment?

> Source: https://learn.microsoft.com/en-us/partner-center/insights/insights-customer-opportunities · https://learn.microsoft.com/en-us/partner-center/insights/insights-data-definitions · https://learn.microsoft.com/en-us/microsoft-365/business-premium/microsoft-365-business-faqs · https://learn.microsoft.com/en-us/microsoft-365/copilot/copilot-business-faq
> As of: 2026-05 · Retrieved: 2026-08-14

## What are the SMB sub-segments and their employee-count boundaries?

Microsoft's Partner Center documentation for CloudAscent, the propensity-scoring engine partners use to target SMB customers, states the sub-segmentation directly: "The small to medium business (SMB) segment is divided into three subsegments," namely **Upper Medium business** at "300+ employees", **Medium business** as "customers with 25 to 300 employees", and **Small business** as "customers with 1 to 24 employees".

A second Microsoft Learn page, the Partner Center Insights data dictionary, gives the same three bands but puts a ceiling on the top one, defining the `SMECTypeSummary` field as "Upper Medium 300 - 999 employees", "Medium includes customers with 25 - 300 employees", and "Small includes customers with 1-24 employees". The same dictionary confirms these are employee counts rather than licence counts, defining `OrgSize` as "the number of employees within an Account".

Two caveats a partner should carry. Microsoft's own two pages disagree on whether Upper Medium is bounded (300–999) or open-ended (300+). And the boundary at 300 is claimed by both Medium and Upper Medium, so it is not a clean cut in Microsoft's published wording.

## Which SMB sub-segments does Microsoft prioritise?

Microsoft is explicit that not all of SMB is weighted equally. The CloudAscent documentation states: "The **Upper Medium** and **Medium** business subsegments are important customers for Microsoft and Microsoft partners because of their high value in sales. These subsegments are the primary focus for driving growth in the SMB segment."

That single sentence should shape a partner's targeting. The 25-to-300-employee band and above is where Microsoft concentrates its growth attention, which means it is also where co-sell interest, incentives and campaign support are most likely to be available. The 1-to-24 band is still served — CloudAscent "prioritizes the profiling, scoring, and modeling of all Upper medium, Medium, and Small business accounts" — but it is not described as a growth focus, and a partner building a practice purely at the very small end should expect less Microsoft pull behind them.

## What is the 300-seat boundary, and why does it matter more than the others?

Because it is the only SMB boundary Microsoft enforces technically rather than merely describing. The Microsoft 365 Business family FAQ states that "Our Microsoft 365 Business base per user plans are designed for organizations with up to 300 users only," and that "Organizations may only provision up to 300 seats total across all of our Business family of plans (Business Basic, Business Standard, Business Premium)."

Critically, the cap is per tenant across the whole family, not per SKU. Microsoft's worked example: "if an organization is provisioned for 250 seats of Business Premium, the organization is eligible to provision only 50 more seats total across the Business family of plans." Microsoft adds that it "reserves the right to enforce the tenant limit of 300 provisioned licenses" and that "Organizations with more than 300 users should consider subscribing to Microsoft 365 for enterprise plans."

For a partner this is the boundary with real commercial consequences: it determines which SKUs a customer can buy, and it is the point at which a growing customer must be migrated to enterprise licensing. A customer approaching 300 employees is a repackaging conversation, not just a renewal.

## Is there an explicit Microsoft statement of what counts as an SMB customer?

Yes, and the cleanest one is in the Microsoft 365 Copilot Business FAQ, which asks and answers the question directly: "How does Microsoft define a small and medium-sized business customer? In the context of Copilot Business, SMB customers are defined as organizations with **300 or fewer users** who hold a license for a Microsoft 365 Business Basic, Business Standard, or Business Premium plan."

The same product-level line is drawn elsewhere. Microsoft Defender for Business is documented as "designed for small and medium sized business up to 300 users." Microsoft's own SMB market research — the Voice and Attitudes to Technology study — defined its population as businesses with 1–300 employees. And Microsoft 365 Business Premium is described as "designed and built for small and medium-sized businesses (1-300 users)."

Note the scoping phrase in the Copilot answer: "in the context of Copilot Business." Microsoft's definitions are product- and program-specific rather than universal, which is why the next section matters.

## Why does Microsoft appear to have two different SMB definitions?

Because it does, and a partner who does not notice will misread their own targets. There is a **go-to-market and propensity definition**, used in CloudAscent and partner targeting tools, which runs from 1 to roughly 999 employees across three sub-segments. And there is a **product and licensing definition**, used in the Business family, Copilot Business and Defender for Business, which stops hard at 300 users.

These overlap but do not agree. A 500-employee company is "Upper Medium business" and therefore SMB in partner targeting tooling, while being ineligible for every Microsoft 365 Business SKU and therefore not SMB in licensing terms. Microsoft's own market research uses the narrower 1–300 definition, adding a third usage.

The practical rule is to always ask which definition is in play. If the question is "who should I prospect," use the CloudAscent bands. If the question is "what can they buy," use 300 seats. If the question is "does this count toward my partner-program metric," use the program's own wording, because that is different again.

## How do Microsoft's partner programs define SMB?

Differently again, and by licence count rather than headcount. The Solutions Partner for Modern Work designation defines its tracks as follows: "Enterprise track: Each customer tenant that has at least one workload with paid licenses greater than 300 is counted as an eligible tenant," while "SMB Track: Each customer tenant that has least one workload with paid licenses between **11 to 300** is counted as an eligible tenant." Note the floor of 11 — tenants below that do not count toward the SMB track at all.

The Solutions Partner for Azure designation classifies the *partner* rather than the customer, and does so by customer mix: "Microsoft classifies your organization as either Enterprise or Small and Medium Business (SMB) based on your customer base. You're classified as SMB if 80% or more of your customers are under the SMB segment. Otherwise, you're classified as Enterprise." Azure eligibility thresholds also differ by classification, with the SMB threshold set at USD 500 Azure Consumed Revenue in one of the last two months against USD 1,000 for Enterprise.

## What do SMC, SMC-Corporate and SME&C mean, and how do they relate to SMB?

These are Microsoft organisational and segment names that sit *around* SMB rather than inside it, and partners routinely conflate them. **SMC** stands for Small, Medium and Corporate — a Microsoft sales organisation covering SMB plus the Corporate segment above it. **SME&C** stands for Small, Medium Enterprises and Channel and is the current name for that organisation; Microsoft Learn describes "Microsoft Small Medium Enterprises & Channel (SME&C) sellers" in its Joint Planning documentation.

**SMC-Corporate** is the segment above SMB, not a part of it. Microsoft's documentation separates the two by tooling: the SPARK propensity reports cover "enterprise and small and medium corporates (SMC-Corporate) customers," while "For the small-to-medium (SMB) customer segment, use CloudAscent propensity data." The CloudAscent FAQ confirms the exclusion from the other direction: "CloudAscent is a small to medium business program and only includes customers in the Small to Mid Size Business segment. Corporate and Enterprise customers aren't included in your downloads."

Microsoft does not publish an employee threshold for where Corporate begins or Enterprise starts. CloudAscent's 300–999 Upper Medium band implies Corporate starts somewhere near 1,000, but that is an inference and should not be quoted as a Microsoft figure.

## Currency warning

Segment definitions, seat caps and partner-program thresholds are all mechanics Microsoft revises, and several of the pages cited here carry different revision dates: CloudAscent customer opportunities (ms.date 2026-02-20), Partner Center data definitions (2025-09-23), Microsoft 365 Business FAQ (2026-01-20), Copilot Business FAQ (2025-12-01), Solutions Partner Modern Work (2026-02-04), Solutions Partner Azure (2026-06-30).

The 300-seat cap has been stable for years and is the safest boundary to rely on. The CloudAscent sub-segment bands, the 11-licence SMB-track floor, the 80% customer-mix rule and the Azure Consumed Revenue thresholds are program mechanics that change with Microsoft's fiscal year. The organisation name has already changed once (SMC to SME&C) and Microsoft Learn still uses both. Re-verify against the specific Microsoft Learn page for the program you are being measured on before treating any of these numbers as current.
