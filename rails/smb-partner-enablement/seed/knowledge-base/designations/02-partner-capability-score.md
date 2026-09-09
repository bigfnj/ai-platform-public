# The partner capability score — how Solutions Partner designations are actually scored

> Source: https://learn.microsoft.com/en-us/partner-center/membership/partner-capability-score · https://learn.microsoft.com/en-us/partner-center/membership/solutions-partner-faq
> As of: 2026-08 · Retrieved: 2026-08-14

## What is the partner capability score?

The partner capability score is a composite score that quantifies a partner organisation's performance in three categories — Performance, Skilling, and Customer success. It is not something you submit or apply for. Microsoft states that "partner capability scores are calculated from measurements of information that is already captured in Partner Center," meaning your consumption telemetry, your customer associations, and the certifications your linked learners hold. You get one score per solution area, so a partner holds up to six separate capability scores at any moment.

## How many points do I need for a Solutions Partner designation?

You need **at least 70 points out of a possible 100** in that solution area, **and** every metric in that solution area must be greater than zero points, **and** you must meet both conditions on at least one day during your qualification eligibility window. All three conditions are mandatory — a partner sitting at 85 points with a zero in one metric does not qualify. Microsoft's FAQ phrases it as "a minimum of 70 points (with points in each category and subcategory) out of a possible 100 points is needed to attain a Solutions Partner designation."

## What are the three metric categories in the partner capability score?

**Performance** measures your ability to expand both your customer base and Microsoft's; its single metric is Net customer adds. **Skilling** measures capability as the number of people in your organisation holding required certifications, split into an Intermediate certifications metric and an Advanced certifications metric. **Customer success** measures your ability to drive growth in the use of Microsoft products, split into a Usage growth metric and a Deployments metric. Most solution areas therefore have five scored metrics; Security has four (no Advanced certifications metric) and Data & AI (Azure) states that advanced certifications are not applicable to it.

## How are points earned within a metric — is it all-or-nothing?

No, partial credit is the norm. Each metric carries a **weight** (its maximum point contribution) and a **threshold** (the level of attainment that earns the full weight). Microsoft's rule is that "when you reach a metric's threshold, you receive the maximum weighted points for that metric. You get partial points for any progress on that metric." The FAQ gives a worked illustration: "if a metric requires three customer adds with a total of 30 total points and you obtain one customer add, you earn 10 of the 30 total points." Because thresholds and weights differ per solution area, the same activity is worth different points in Modern Work than in Infrastructure.

## What does the Net customer adds metric actually measure?

Net customer adds awards points for the number of eligible customers added over the **trailing 12 months**, with lost customers subtracted from new customers — it is a net figure, so churn actively costs you points. Each of the six solution areas defines its own eligibility criteria for what counts as a new customer and its own customer-growth threshold. Microsoft is specific that eligible customers are "customers new to the partner," which can be a customer new to Microsoft products or an existing Microsoft customer who moves to you, and that "the customer must be associated with the partner to be included in the metric" — so CSP, CPOR, DPOR or PAL association is a precondition, not an optional extra.

## What does the Skilling category measure and how do certifications count?

Skilling counts people, not exams. Credits are based on certifications earned by learners who are **linked to your organisation** in Partner Center — an uncorrelated certification earns you nothing. The category splits into Intermediate certifications (progress toward having an adequate number of people with intermediate-level certifications in that solution area) and Advanced certifications (the same for advanced-level certifications). Microsoft warns that "as solution areas evolve and change, required certifications are expected to change as well," and that expiring certifications will drop your score — Partner Center surfaces a recommendation when a certification's valid date is approaching.

## What do the Usage growth and Deployments metrics measure?

Both sit in the Customer success category and they measure different things. **Usage growth** measures the growth in your customers' consumption of Microsoft products over the past year — expressed as Azure consumed revenue growth in the Azure areas, active-user growth in Modern Work, protected-user or ACR growth in Security, and monthly consumption value growth in Business Applications. **Deployments** measures your ability to expand the breadth of Microsoft workloads deployed across your customer portfolio — counting distinct Azure services in the Azure areas, and net new customer tenants crossing a usage threshold in the Microsoft 365 areas. Each solution area sets its own thresholds and eligibility rules for both.

## What is the qualification eligibility window and why does it matter?

The qualification eligibility window is the rolling period in which you need to have hit the bar on at least one single day. It is what makes the score forgiving of month-to-month noise. **If you are not enrolled** in that solution area, the window is the current month plus the previous five full calendar months, rolling forward each month — Microsoft's example is that on 15 August 2024 you must have qualified for at least one day between 1 March 2024 and 15 August 2024. **If you are enrolled**, the window is the month of your anniversary date plus the previous five months, and it also includes your 30-day renewal window. If you qualify at any point in the renewal window you are eligible to renew.

## How often is the partner capability score refreshed?

Two different cadences apply, and the difference explains most "why hasn't my score moved" support tickets. Performance and Customer success subcategories are "typically refreshed by the 20th of every month," though minor refreshes may occur through the month. Skilling subcategories are "typically refreshed within 10 days after certification is completed." Microsoft separately notes that after you associate a new customer it can take up to two update cycles — roughly three to four weeks — for that customer to appear in Solutions Partner designation data if the association lands close to an update cutoff.

## What are the Enterprise and SMB qualification tracks?

Several solution areas run two parallel attainment paths with different thresholds, so that a partner serving many small customers is not measured against a partner serving a handful of large ones. Microsoft states that multiple tracks exist in Data & AI, Digital & App Innovation, Infrastructure, Modern Work and Business Applications. How you land on a track differs by area: in **Modern Work and Security** Microsoft evaluates you on both paths and takes the higher of the two capability scores at the solution-area level; in **Data & AI, Digital & App Innovation, Infrastructure and Business Applications** your organisation is automatically categorised into one track based on the revenue and customer base you serve. Note that the capability-score page's list of multi-track areas omits Security, while the Security solution-area page itself documents an Enterprise and an SMB path — treat the Security page as authoritative for Security.

## How is my enrollment track locked in once I am enrolled?

For the Azure solution areas, the enrolment track is displayed only once you are enrolled and then stays fixed for a full year until the next renewal. The computation is deliberately generous toward SMB: "the enrollment track is computed as SMB if you are qualified as SMB at least once during the qualification eligibility window" — that is, at least one SMB row in the six rows of the scores-history panel. Only if your organisation qualifies as Enterprise on every single row throughout the window is the enrolment track computed as Enterprise.

## Which customers and subscriptions are excluded from the capability score?

Two exclusions are worth knowing before you build a plan around a particular customer. First, on government: "government customers operating in public/commercial clouds are included in the partner capability score for all solution areas. Government customers operating in any cloud other than public/commercial clouds are not included in partner capability scores." Second, on sovereign clouds: Azure subscriptions associated with isolated or sovereign cloud environments, such as Azure China (Mooncake) and Fairfax / US Gov-like clouds, "are not currently supported in eligibility and scoring calculations in Solutions Partner designations and Specializations."

## Where do I see my score and what recommendations does Partner Center give me?

Sign in to Partner Center, select **Membership**, then **Solutions Partner > Overview** to see the consolidated status and score for all six solution areas as calculated that day; select **View details** on any card to drill into metric-level detail. The Membership workspace requires the Microsoft AI Cloud Partner Program partner admin role. Partner Center also exposes a **Recommendations** section that flags actions to improve or protect your score — for example customers whose Azure consumed revenue is too low and are therefore contributing negatively, certified employees who are not counting toward the skilling score, or deployments not being picked up in customer success. You can download the underlying contributing data to analyse it.

## Does working toward a specialization raise my partner capability score?

No. This is a common and expensive misconception. Microsoft answers it directly: asked whether working toward a specialization boosts a score currently under 50 points, the FAQ says "No. Specializations and expert programs continue to be a way to further differentiate your organization's deep technical expertise." The dependency runs the other way — the designation is the prerequisite for the specialization. A partner under 70 points should be working the five capability-score metrics, not specialization requirements.

## Currency warning

The 70-point threshold, the 100-point maximum, the three categories and the qualification-window mechanics in this file were verified against the Microsoft Learn partner capability score page (page date January 2025, last updated May 2026) and the Solutions Partner FAQ (last updated March 2026). Per-metric weights and thresholds live on the individual solution-area pages and change more often than the framework does — always re-check the solution-area page before quoting a specific weight. Microsoft's own scoring-framework summary table on the capability-score page shows placeholder values ("xx") rather than real numbers, so do not treat that table as a source of point values.
