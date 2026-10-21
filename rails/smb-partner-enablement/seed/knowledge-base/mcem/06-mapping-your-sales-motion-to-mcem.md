# How does a partner's own sales motion map onto the MCEM stages?

> Source: https://learn.microsoft.com/en-us/partner-center/referrals/manage-co-sell-opportunities · https://learn.microsoft.com/en-us/partner-center/referrals/mcem-for-partners
> As of: 2026-07 · Retrieved: 2026-08-14

## Do I have to change my CRM stages to use MCEM?

No. Microsoft states this plainly in the Partner Center co-sell documentation: "Your company doesn't have to use the same sales stages." Partner Center maintains its own set of standard sales stages and publishes how they translate to MCEM, so a partner keeps their own pipeline vocabulary and lets the translation happen at the boundary.

The translation is also automatic when you integrate. Microsoft notes that "if you're passing these values using the API, this is how Partner Center recognizes the deal sales stages and automatically maps the stages of your company to these standard stages." So the practical work is to decide, once, which of your internal stages corresponds to each Partner Center sales stage, and then keep the Partner Center record moving as your own deals move. What you should not do is invent a parallel MCEM-shaped pipeline alongside your real one.

## What is the official mapping from deal sales stages to MCEM stages?

Microsoft publishes this mapping in the Partner Center co-sell documentation, including the percentage each sales stage carries and the MCEM exit criterion attached to it. Reproduced verbatim, it runs as follows.

| Sales stage | % | Definition | MCEM stage | MCEM stage exit criteria |
| --- | --- | --- | --- | --- |
| Created | 10% | Creating an outbound deal | Listen and Consult | Qualified opportunity |
| Accepted | 10% | Accepting an inbound deal | Listen and Consult | Qualified opportunity |
| Qualified | 20% | Qualifying the value of the deal and the customer requirements before proceeding further | Listen and Consult | Qualified opportunity |
| Developed | 40% | Developing the deal further to understand the detailed requirements to either prepare a POC or any other artifacts required for a formal proposal | Inspire and Design | Customer Aligned to solution & business case |
| Proposed | 60% | Making a formal proposal to the customer based on their requirements | Empower and Achieve | Customer Agreement in place |
| Negotiated | 80% | Negotiating the final terms based on the proposal to get to the final state – winning or losing the deal | Empower and Achieve | Customer Agreement in place |
| Won | 100% | Marking the deal as won | Realize Value | Customer has executed the agreement and deployment can begin |

Two things are worth noticing. Three separate sales stages all sit inside MCEM stage 1, which tells you Microsoft expects most of the qualification effort to happen before design begins. And the table stops at *Won* — there is no sales stage corresponding to MCEM stage 5, because by then the opportunity record is closed.

## Where does a typical partner sales process line up with MCEM?

Most partner sales processes already contain the MCEM shape under different names, and mapping is usually a renaming exercise rather than a re-engineering one. A partner whose stages run *lead → discovery → scoping → proposal → close → onboarding → account management* maps almost one-for-one: lead and discovery are Listen and consult, scoping is Inspire and design, proposal and close are Empower and achieve, onboarding is Realize value, and account management is Manage and optimize.

The friction points are consistent across partners. Partners typically qualify later than MCEM expects, treating a deal as qualified once a budget is confirmed rather than once a need is documented and mapped. Partners typically build a solution design without a separate business case, which fails the stage-2 exit criterion as written. And partners typically close the opportunity record at signature, which means nothing in their system tracks stages 4 and 5 where the annuity revenue lives. If you only fix one of these, fix the business case — it is the explicit half of the stage-2 gate that most partner processes have no field for.

## Where do partner activity and Microsoft seller activity actually intersect?

The intersections are specific rather than continuous, and knowing them prevents both over- and under-engaging Microsoft. At **stage 1** the intersection is the qualified opportunity being created in Partner Center and shared with Microsoft sellers — before that, Microsoft has no visibility. At **stage 2** it is agreement on the solution and the solution play, since the play determines which Microsoft specialists get routed to the deal, and Microsoft's stated stage-2 exit requires "both the partner and Microsoft accounts teams" to have clarity and agreement.

At **stage 3** the intersection is the most hands-on: this is where the published help types — technical architecture, proof of concept or demo, quotes and licensing — are most likely to be requested and granted, and where Microsoft describes agreement construction as involving "technology specialists, cloud solution architects, account executives, and industry solutions delivery and support personnel." At **stage 4** the Microsoft roles shift to Customer Success Managers and Solution Architects. At **stage 5** the intersection is the joint account review and the decision to open a new cycle — Microsoft's guidance is that "the partner and Microsoft account teams should engage to further explore with the customer."

Between those points, the deal is yours to run. MCEM does not put a Microsoft seller inside your sales process; it defines the handshakes.

## How do I decide whether a deal needs Microsoft at all?

Partner Center's deal types answer this directly, and not every deal should be a co-sell. A **private** deal is one where "a partner decides to work independently on a deal that is created in Partner Center" and the details are not shared with Microsoft sales. A **partner-led** deal is one where you want no active help but do want Microsoft visibility — created by answering *No* to needing help and *Yes* to "Would you like Microsoft sellers to view this deal?". A **co-sell** deal is one where you have selected a specific type of help, at which point "a Microsoft seller can potentially help closing the deal, although their participation isn't guaranteed."

For high-volume SMB business, partner-led is usually the right default: it gives Microsoft pipeline visibility without asking for seller attention that a small deal will not attract, and Microsoft notes that partner-led deals "are eligible for deal registration even though a Microsoft seller isn't actively involved." Reserve co-sell requests for deals where a named piece of Microsoft help would change the outcome. Deal types are not permanent — a private deal can be upgraded "into a partner-led or active co-sell deal before the deal reaches a terminal state."

## Currency warning

The sales stage table, percentages, and deal-type definitions are Partner Center program mechanics taken from a page dated 2026-07-23 and change with the platform. The MCEM exit criteria in the table are Microsoft's published wording as of that date. Percentages shown are the Partner Center referrals system's own weighting and should not be read as guidance for your own forecast weighting. Re-verify before building CRM integration against this mapping.
