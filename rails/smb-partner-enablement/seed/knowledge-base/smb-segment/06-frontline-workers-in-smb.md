# Frontline worker scenarios in SMB and the licensing that fits

> Source: https://learn.microsoft.com/en-us/microsoft-365/frontline/flw-overview · https://learn.microsoft.com/en-us/microsoft-365/frontline/switch-from-enterprise-to-frontline · https://www.microsoft.com/licensing/terms/product/ForOnlineServices/EAEAS · https://learn.microsoft.com/en-us/microsoft-365/frontline/teams-for-retail-landing-page · https://learn.microsoft.com/en-us/microsoft-365/frontline/pin-teams-apps-based-on-license
> As of: 2026-01 · Retrieved: 2026-08-14

## What is a frontline worker in Microsoft's definition?

Microsoft's marketing definition describes frontline workers as "employees whose primary function is to work directly with customers or the public, selling products and providing services and support," adding that they "also include employees directly involved in the manufacturing and distribution of products or services." Microsoft's documentation gives examples such as retail associates, healthcare clinicians and nursing staff, and factory workers, and characterises the audience as "a mobile workforce that primarily interacts with customers, but also needs to stay connected to the rest of your organization."

The binding definition, however, is not the marketing one — it is in Microsoft's Product Terms, and it is device-based rather than role-based. Frontline Worker licences "may only be assigned to users who satisfy one or more of the following conditions: Uses a primary work device with a **single screen smaller than 10.9"**, or **Share their primary work device** with other users licensed with a Frontline Worker License, during or across shifts."

That distinction matters commercially. A partner cannot licence someone as frontline simply because their job title sounds frontline; the entitlement turns on screen size or device sharing. Getting this wrong creates a compliance exposure for the customer at true-up, so the device question belongs in discovery rather than at renewal.

## Why does frontline matter in SMB specifically?

Because it is where the seat volume is, and Microsoft says so in its audited filings. The FY2025 Annual Report states that "Microsoft 365 Commercial cloud revenue grew 15% with Microsoft 365 Commercial seat growth of 6% **driven by small and medium businesses and frontline worker offerings**, as well as growth in revenue per user."

The structural reason is that most small businesses in physical industries have an inverted employee profile compared with a professional-services firm. A 60-person restaurant group, retailer or care provider might have five people at desks and fifty-five on the floor. Licensing all sixty at Business Standard or Business Premium is unnecessary and expensive; licensing five at Business Premium and fifty-five at a frontline plan is both cheaper for the customer and a larger total seat count for the partner.

Frontline is therefore not a niche within SMB — for whole industries it is the majority of the estate, and the partner who only quotes desk-worker licensing is quoting for a fraction of the business.

## What is the difference between Microsoft 365 F1 and F3?

These are the two frontline base plans. Microsoft's documentation is explicit that "Microsoft 365 for frontline workers refers to the **Microsoft 365 F3 and F1 licenses**," though it notes an Enterprise licence (E3, E5) can also be used to implement any frontline scenario.

Neither plan includes desktop Office applications — "F plans don't include Microsoft 365 desktop apps." The substantive gaps between them are that **F1 gives read-only Office for the web and mobile while F3 gives full use**; **F1 has no Exchange mailbox at all** (its Kiosk plan supports Teams calendar only, and users "aren't entitled to use the mailbox") **while F3 includes a 2 GB mailbox**; F3 adds To Do and Forms, which F1 lacks; and critically **F3 includes Power Apps, Power Automate, Copilot Studio for Teams and Dataverse for Teams, while F1 includes none of them**. Both are capped at 2 GB of OneDrive.

The Power Platform line is usually the deciding one in SMB. If the plan is to digitise a paper process — a checklist, an incident form, a stock count — that needs F3. F1 suits a pure communications and scheduling deployment.

There is also a well-documented migration landmine: "OneDrive will become read-only for users who are over the 2 GB limit after the switch to an F plan." Anyone moving users down from an E plan must check OneDrive consumption first.

## Is there a Microsoft 365 F5 plan?

No, and this is a common and expensive misunderstanding. There is no Microsoft 365 F5 base plan. F1 and F3 are the only frontline base SKUs. What existed as "F5" was an **add-on family** — F5 Security, F5 Compliance, and F5 Security & Compliance — sold on top of F1, F3 or Office 365 F3.

Those add-ons have since been renamed to the **Microsoft Defender Suite FLW**, **Microsoft Purview Suite FLW**, and **Microsoft Defender and Purview Suite FLW**. Microsoft's own documentation still mixes the old and new naming in places, and no dated Microsoft announcement of the rename was located during research, so partners should expect to encounter both names and should verify current naming and pricing in Partner Center rather than relying on either.

The practical takeaway is to quote frontline security as an add-on line on top of F1 or F3, never as an "F5 plan."

## What do frontline workers actually get in Teams?

F-licensed users receive a distinct interface Microsoft calls the **tailored frontline app experience**, in which Teams pins a fixed set of apps in a fixed order: **Home, Activity, Chat, Engage, Walkie Talkie, Planner, Shifts, Approvals**. It is on by default, the order cannot be changed, and it applies only to F licences — Microsoft notes that a user with an E, A or G licence "doesn't get the tailored frontline app experience."

The two capabilities that sell this to a sceptical operator are Shifts and Walkie Talkie. **Shifts** is described as "the schedule management app in Microsoft Teams… built mobile first," letting managers build and bulk-import schedules, post open shifts, handle time-off and run timesheet reports, while workers view schedules, claim open shifts, swap or offer shifts, and clock in and out with breaks from their phone. **Walkie Talkie** is push-to-talk over Wi-Fi or cellular and is "included in all paid licenses of Teams" — worth knowing, because it costs nothing extra and it replaces a physical radio fleet.

Two caveats to carry: Walkie Talkie is not available in China, and Shifts has only one named first-party workforce-management connector (Reflexis Workforce Management), so any other WFM integration is custom Graph work and should be scoped as such.

## What is the "working time" capability and why does it close deals?

Working time limits a frontline worker's access to Teams on mobile when they are off shift. Microsoft's own stated driver for it is compliance: "Local laws and regulations require you to restrict access to work apps when employees are off shift."

This is worth knowing because it converts a productivity conversation into a risk conversation, which is a much easier sale to an owner-operator. In jurisdictions with right-to-disconnect rules or strict working-time regulation, an employer who has put a work chat app on every employee's personal phone has created an exposure, and this feature is the mitigation.

It requires a clock-in and clock-out signal from a workforce-management system, though Shifts is not required to provide it.

## What are the published Microsoft frontline scenarios for retail?

Retail is one of only four industries for which Microsoft publishes a dedicated frontline scenario set. The published retail scenarios are **in-store communication and collaboration**, **cross-store communication and collaboration**, **fittings and consultations**, **simplify business processes**, **corporate communications**, and **onboarding new employees**.

Microsoft also ships two Teams team templates for retail: **"Manage a Store"** (with General, Shift Handoff, Store Readiness and Learning channels) and **"Retail for Managers"** (General, Operations, Learning). These are genuinely useful in an SMB deployment because they give a small retailer a working structure on day one instead of an empty Teams tenant.

The recognisable customer problems these map to are shift handover done on a paper notepad behind the till, store-opening checklists nobody can audit, a WhatsApp group that includes staff who left months ago, and head-office announcements that reach managers but never reach the floor.

## Does Microsoft publish frontline scenarios for restaurants or field services?

**No — and this should be stated plainly rather than implied otherwise.** Microsoft's frontline documentation hub publishes industry scenario sets for exactly four industries: **Healthcare, Retail, Financial services, and Manufacturing**. Restaurants and food service appear only as illustrative prose in Microsoft material, not as a published scenario set with named scenarios and templates.

"Field service" is a different thing again and partners should not conflate the two. **Dynamics 365 Field Service** is a separate product for dispatch, work orders and mobile technicians; it is not part of the Microsoft 365 frontline worker scenario library, and it carries its own licensing.

None of this means the technology does not fit a restaurant or a plumbing firm — it plainly does, and the cross-industry scenario groups Microsoft does publish apply directly. Those groups are **communications**, **wellbeing and engagement**, **training and onboarding**, **schedule management**, **digitised processes**, and **appointments**. A restaurant group deploying Shifts for rotas, Walkie Talkie across the kitchen and front of house, Approvals for time-off, and Lists for opening checklists is running four published Microsoft scenarios. Sell those by name. Do not claim a Microsoft-published restaurant or field-service frontline scenario set exists, because a customer or a Microsoft seller can check.

## What about appointment-based scenarios in SMB?

Appointments are a published cross-industry scenario, but the tooling changed and the old answer is now wrong. Microsoft states that "**The Virtual Appointments app in Microsoft Teams is no longer available.**" The replacement is **Microsoft Bookings**, with the virtual appointment meeting template and the Virtual Appointment Graph API remaining available.

Note the licensing dependency: advanced capabilities such as SMS notifications and the scheduled queue require **Teams Premium**, which is a separate add-on and not part of any Business or frontline base plan. For an SMB selling appointments — a clinic, a salon, a consultancy, a repair shop — this is a real cost line that must be scoped rather than assumed.

## What does AI look like for frontline workers?

Microsoft has shipped **Frontline Agent**, described as "an AI-powered assistant in Microsoft 365 Copilot and Microsoft Teams designed to support frontline workers and managers." Its published scenarios are quick access to information and guidance, catching up on missed communications at the start of a shift, drafting end-of-shift handovers, following up and gathering information from the team, and completing site walkthroughs and checklists by voice.

The commercial catch is the prerequisite: Frontline Agent **requires a Microsoft 365 Copilot licence**. It is not included with F1 or F3. So the honest framing for an SMB is that the frontline AI story is real but sits a licensing tier above the base frontline deployment, and should be positioned as a later expansion rather than bundled into the initial quote.

The shift-handover scenario is the one worth leading with, because it is a task every shift-based business does badly and everyone recognises.

## How should devices be handled for SMB frontline deployments?

Shared devices are the norm rather than the exception in frontline SMB, and Microsoft supports them explicitly through **shared device mode**, an Entra ID capability for Android, iOS and iPadOS. Its documented benefits are single sign-on across apps, **single sign-out** — so one tap clears the previous worker's session at end of shift — and Conditional Access enforcement on the shared device.

Related supporting capabilities include domain-less sign-in and QR code authentication on mobile, both of which materially reduce the friction of getting a worker with no company email address signed in on a shared tablet.

This is worth raising early in discovery because it changes the licensing conversation. Device sharing is one of the two Product Terms conditions that qualifies a user for frontline licensing at all, so establishing that devices are shared both justifies the cheaper licence and points at the technical configuration the deployment will need.

## Currency warning

Frontline licensing is unusually volatile and one part of it is both the most valuable and the most date-sensitive fact here. The **Product Terms eligibility rule** (screen smaller than 10.9 inches, or shared primary work device) was retrieved from Microsoft's Product Terms site on 2026-08-14 with **no effective date rendered on the page**; Product Terms are revised monthly, so re-verify against the current dated monthly PDF before relying on it in a licensing position.

The F5-to-Suite-FLW rename could not be dated to a Microsoft announcement, current prices for the FLW suites were not located, and Microsoft's own documentation still mixes legacy and current naming. Product pages cited carry dates between 2024-10-21 and 2026-07-17. This file deliberately omits prices, which vary by geography, currency and channel and now update annually each January.

Finally, the four published industries (Healthcare, Retail, Financial services, Manufacturing) and the absence of published restaurant and field-service scenario sets reflect Microsoft's frontline hub as of a page dated 2025-11-13. Microsoft could add industries; check before asserting the absence to a customer.
