# Partner Center roles and permissions for program administration

> Source: https://learn.microsoft.com/en-us/partner-center/account-settings/permissions-overview · https://learn.microsoft.com/en-us/partner-center/membership/mpn-overview · https://learn.microsoft.com/en-us/partner-center/enroll/understand-the-verification-process · https://learn.microsoft.com/en-us/partner-center/account-settings/partner-center-account-setup
> As of: 2026-08 (roles article dated 2025-12, updated 2026-06) · Retrieved: 2026-08-14

## How does Partner Center access actually work?

Access is two-step and people frequently only complete the first step. Microsoft states that "users login to Partner Center using a work account associated with an Entra ID. Any Entra aka tenant member can access Partner Center. However workspace access is governed by roles." Provisioning therefore means, first, adding the person to the Entra tenant as a member, and second, granting them one or more Partner Center roles. Being in the tenant gets someone through the door but shows them almost nothing — Microsoft's **Default User role**, which "any user without explicit role-assignment inherits," permits only viewing the Account Settings Overview page and a learning profile.

## Which role do I need to enrol my company in the partner program?

Global admin, and only Global admin. Microsoft describes Global Admin as an Entra-enabled role and the "most privileged role for tenant and account management," and states it is the "only role eligible to enroll an organization tenant into Microsoft AI Cloud partner program (MAICPP), Cloud solution program (CSP), Surface and accept agreements." Beyond enrolment, Global Admin can add users to the tenant, assign the privileged roles (user management admin, billing admin, admin agent, security admin, account admin), add and remove peer Global Admins, delete partner locations, add tenants, reset user passwords and manage certificates. Because it is the only role that can accept agreements, an organisation with no reachable Global Admin cannot enrol or re-enrol at all.

## What does the Microsoft AI Cloud Partner Program partner admin do?

This is the role that runs the program day to day, and it is the one most partner staff are missing when something is invisible to them. It is a Partner Center–specific role applicable to organisations enrolled in MAICPP. It unlocks the **Membership** workspace, to "view and manage offers like Solutions partner designation, specializations, Azure managed service provider" plus the Training Services Partner Program and Solutions Partner with certified software, and to work in the voucher validation tool; the **Benefits** workspace, to view and manage benefits; and **Insights**, to view Solutions Partner insights, customer eligibility, the score simulator, qualification criteria and learn analytics. Microsoft is explicit that "to manage your membership in Partner Center, you must be granted the Microsoft AI Cloud Partner Program partner admin role."

## What is the Account admin role and how is it different from Global admin?

Account admin is the organisational-administration role for the partner program specifically, and unlike Global admin it is Partner Center–specific rather than inherited from Entra. Its scope is the Account settings workspace: managing the organisation's legal profile, creating and managing partner location accounts, initiating account merges when mergers or acquisitions happen, and user role management — but with a deliberate ceiling. Microsoft notes Account admin "can do role management for all except privileged roles like Global admin, admin agent, billing admin, User management admin." The practical division is that Global admin owns the tenant and agreements, while Account admin owns the partner account's legal identity, locations and non-privileged role assignment.

## Which roles come from Microsoft Entra ID and which are Partner Center only?

The distinction determines where you go to assign them. Microsoft lists the roles that "automatically extend to Partner Center" and can be assigned in Entra as: **Global Admin, Billing Admin, Admin Agent, User Administrator (User Management Admin), Security Admin, Compliance Admin, Helpdesk agent and Sales agent.** Everything else — Account admin, MAICPP partner admin, Business Profile admin, Referrals admin and user, Incentives admin and user, Report viewer, Executive report viewer, Support request admin, and the Marketplace roles Owner, Manager, Developer, Business Contributor, Finance Contributor and Marketer — is Partner Center–specific and assigned inside Partner Center. Microsoft warns of a lag between the two systems: "Role assignments or changes made in the Azure portal can take up to one hour to reflect in Partner Center."

## Who can assign roles to other users?

Assignment authority differs by program, which is why "ask an admin" is not specific enough advice. Microsoft lists it as: **Global Admin** for MAICPP, CSP and Surface programs; **User Management Admin** for MAICPP, CSP and Surface programs; **Account Admin** for the MAICPP program; and **Owner or Manager** for Azure Marketplace and developer programs. The assignment path is Partner Center → Settings → Account Settings → User management, then select the user, select or clear roles, and Update. To find who holds a given role, the same User management page can be filtered by role name — filtering on "Global admin" returns the list of Global admins at the company.

## Can roles be limited to a single business location?

Yes, for a specific subset, which matters for partners operating several subsidiaries under one global account. Microsoft distinguishes **location-scoped** roles — giving examples of Incentives Admin/User and Referrals Admin/User — from **organisation-scoped** roles, giving examples of Account Admin, User Management Admin and Compliance Admin. Location scoping restricts a user to the partner location accounts they are assigned to, which is how a company keeps a regional incentives administrator from seeing or acting on another region's payouts. Report viewer roles can also be scoped to location.

## Can external consultants or guests hold partner program roles?

Only a narrow set, deliberately. Microsoft states that guest users invited into Entra "can only be assigned MPN Admin, Business Profile Admin, Referral Admin for MAICPP, Manager or Developer role for Marketplace or developer programs." That is enough for an outsourced marketing agency to manage the business profile and referrals, or for a contracted developer to work on Marketplace offers, without exposing legal profile, billing, user management or customer data. Guests cannot be Global admins, Account admins or Billing admins.

## Why can't I see the Membership or Benefits workspace?

Because workspace visibility is role-driven, and the specific role needed is the MAICPP partner admin. Microsoft's own FAQ frames the symptom exactly as partners experience it: "I can't see the details of membership and am getting an 'access denied' error. Sometimes it's asking me to contact myself to get permission." The explanation is that full access to the Membership and Benefits workspaces requires the Microsoft AI Cloud Partner Program partner admin role, which Account admins grant — so an Account admin who lacks it is told to contact an Account admin, meaning themselves, and can simply self-assign it by following the same steps. To check current access, go to **Account Settings → Overview → View permissions**.

## Which roles does verification require?

Verification splits between who can start it and who can finish it. Microsoft states that "Global Admins can start enrollment," and that once started, verification can be completed by **Global admin, Compliance admin, or Account admin (MAICPP-specific)**; for developer programs such as Microsoft Marketplace, Windows and Xbox, and Microsoft 365 and Copilot, a **Manager or Owner** can both start and complete it. Compliance Admin is the purpose-built role here — its documented capability is to "manage legal profile and monitor account verification" and add or update the organisation's security contact. Note that regardless of who performs the steps, only the **primary contact** receives Microsoft's verification emails.

## How should we govern partner program roles over time?

Microsoft publishes an explicit cadence rather than leaving it to the partner. Its stated best practice is least-privileged access scoped to the tenant boundary, plus a review "every 3–6 months to ensure role assignments are most relevant," and — importantly — to "ensure backup users are available for Global Admin, User management admin, Account admin, owner." Microsoft also names the trigger events that should prompt an immediate review: when the organisation first enrols in MAICPP and becomes active, immediately after a location is added or deleted, on program enrolment changes, and on account merge events. The single-point-of-failure risk is real: without a second Global Admin, a company can lose the ability to accept agreements or enrol in new programs.

## Currency warning

Role names, capabilities and workspace mappings change as Partner Center evolves, and Microsoft's own documentation currently mixes generations — the roles article still calls the partner admin role "MPN Admin" in the guest-user section while naming it "Microsoft AI Cloud Partner Program partner admin" elsewhere. Editing user permissions is not supported in national clouds, including Microsoft Cloud for US Government and Microsoft Azure and Microsoft 365 operated by 21Vianet in China. This file reflects the Microsoft Learn roles article dated December 2025 and last updated June 2026, retrieved 14 August 2026; re-verify role names against the live User management page before instructing a partner.
