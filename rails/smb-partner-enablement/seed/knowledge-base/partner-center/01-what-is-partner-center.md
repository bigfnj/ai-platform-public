# Partner Center — the single console for membership, referrals, transactions and payouts

> Source: https://learn.microsoft.com/en-us/partner-center/ · https://learn.microsoft.com/en-us/partner-center/enroll/overview · https://learn.microsoft.com/en-us/partner-center/account-settings/permissions-overview · https://learn.microsoft.com/en-us/partner-center/insights/insights-overview
> As of: 2026-08 · Retrieved: 2026-08-14

## What is Partner Center?

Partner Center is the web console at `partner.microsoft.com/dashboard` where a Microsoft partner runs its entire commercial relationship with Microsoft. Microsoft's own description is that Partner Center "streamlines several business processes to make it easier for Microsoft partners to manage their relationship with Microsoft and their customers." It is not a marketing site and not a learning portal — it is the transactional system of record. Everything a partner does commercially with Microsoft eventually lands here: enrolment, membership and benefits, referrals and co-sell, customer subscriptions, marketplace offers, incentive claims, analytics, and payouts. If a partner asks "where do I do X with Microsoft," the answer is almost always Partner Center.

## What can I actually do in Partner Center?

Microsoft lists the core jobs Partner Center supports, and they map closely to the reasons a partner logs in on any given day. You use Partner Center to manage your Microsoft account and users, engage with customers, build relationships with other partners, enrol in incentive programs, manage customer subscriptions, bill and get paid, contact Microsoft support, and publish or explore offers on Microsoft Marketplace. From the account side you can view and modify tenants, business locations, users and permissions, tax information, and the specific programs your company is enrolled in. What you see is scoped by your role and permissions, so two people at the same company can have very different Partner Center experiences.

## What are the Partner Center workspaces?

Partner Center is organised into workspaces, each covering one area of the partner relationship, and Microsoft's documentation hub is structured the same way. The workspaces documented are Enroll, Membership, Benefits, Referrals, Customers, Billing, Pricing, Incentives, Marketplace offers, Earnings, Insights, Account settings, Security, and Help and support. A partner does not see all of them — workspace visibility follows both the programs the company is enrolled in and the roles the individual user holds. For example, Microsoft notes that the Insights workspace only shows a "Microsoft Marketplace offers" section if you also have access to the Marketplace offers workspace.

## Which workspaces matter most for selling with Microsoft?

Four workspaces carry the selling motion. **Referrals** holds leads, co-sell opportunities, business profiles, co-sell solution configuration and deal registration — this is where a partner receives and creates deals. **Marketplace offers** is where an ISV publishes and maintains its offers. **Insights** is the analytics hub covering membership, marketplace, referrals and co-sell performance. **Earnings** shows what the partner has earned and been paid across incentives, marketplace and store programs. **Membership** and **Benefits** sit behind these, holding designations, specializations and the benefit entitlements that gate eligibility for several of the selling motions.

## How do I get access to Partner Center, and why can't I see a workspace?

Users sign in to Partner Center with a work account in the company's Microsoft Entra tenant. Being a member of the tenant gets you in the door; roles decide which workspaces you can open. Microsoft describes provisioning as two steps: add the user to Entra as a member, then grant Partner Center roles. If a workspace is missing, the cause is almost always a missing role rather than a broken account. Go to **Account settings → Overview → View permissions** to see what you currently hold, then use **User management** to find the right admin and request access. Note that role assignments made in the Azure portal can take up to an hour to appear in Partner Center.

## Which roles exist and who can assign them?

Some Microsoft Entra roles extend automatically into Partner Center — Global Admin, Billing Admin, Admin Agent, User Administrator (User Management Admin), Security Admin, Compliance Admin, Helpdesk agent and Sales agent. Everything else is Partner Center-specific. Role assignment rights differ by program: Global Admin or User Management Admin can assign roles for the Microsoft AI Cloud Partner Program, CSP and Surface; Account Admin can assign for MAICPP; and Owner or Manager assign roles for Azure Marketplace and developer programs. Some roles can be scoped to a single partner location — Incentives Admin/User and Referrals Admin/User are location-scopable — while Account Admin, User Management Admin and Compliance Admin are organisation-scoped.

## Which roles do I need for referrals, co-sell and deal registration?

Referrals work is gated behind four specific roles and getting them wrong is a common blocker. **Referrals admin** can manage the business profile, manage co-sell opportunities and leads, view and register deals that are marked won and eligible, and assign team members to a deal. **Referrals user** can create and manage co-sell opportunities and register eligible deals, but only where they have been assigned as a team member on that deal. **Business profile admin** manages the Microsoft Marketplace business profile that generates leads. **Co-sell solution admin** is the role required to open the Co-sell → Solutions page and configure an offer for co-sell status. Microsoft's FAQ is explicit: if you cannot see co-sell opportunities, ask an admin for the Referrals admin role.

## Which roles do I need to publish and get paid on Microsoft Marketplace?

Marketplace and developer programs use a separate role set from the membership programs. **Owner** has full seller control including users, publisher accounts, offers and payouts, and is assigned to whoever enrols the location account. **Manager** can manage publisher accounts, all offer types and pricing. **Developer** can upload packages and submit offers. **Business Contributor** handles pricing and earnings without engineering access. **Finance Contributor** manages bank and tax setup plus revenue and earnings reports. **Marketer** can respond to customer reviews and see non-financial insights. Microsoft documents that getting paid for marketplace offers requires Owner or Financial Contributor.

## Currency warning

Workspace names, role names and program names in Partner Center change with Microsoft's fiscal year and with platform releases — the partner program itself has been renamed twice (MPN → Microsoft Cloud Partner Program → Microsoft AI Cloud Partner Program), and the commercial marketplace was rebranded to Microsoft Marketplace with Azure Marketplace and AppSource folded into a single storefront. The role model documented here reflects Microsoft Learn as retrieved on 14 August 2026, with the roles and permissions article carrying a Microsoft date of 2025-12-26. Before telling a partner which role to request, confirm the current role list in **Account settings → User management** in their own tenant, because role availability also varies by which programs the company is enrolled in.
