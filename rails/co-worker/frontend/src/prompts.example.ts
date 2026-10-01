// Example co-work harvest job definitions — safe to commit, no owner identity.
//
// To use your own prompts: copy this file to prompts.ts (gitignored) and fill in
// your real name, role, and context. Then change the import in BriefView.tsx to
// point at ./prompts instead of ./prompts.example.
//
// Each job maps to one Claude scheduled run that writes to the co-worker inbox.

export interface CoWorkJob {
  id: string
  icon: string
  title: string
  schedule: string
  description: string
  prompt: string
}

export const COWORK_JOBS: CoWorkJob[] = [
  {
    id: 'email-triage',
    icon: '📧',
    title: 'Email triage',
    schedule: 'Daily, 07:00',
    description: 'Scans unread email for action items, deadlines, and threads needing a reply.',
    prompt: `You are a chief of staff for [YOUR NAME], a [YOUR ROLE] at [YOUR COMPANY].

Review the emails below and identify items that need attention. For each:
- Flag anything with a hard deadline or explicit ask
- Note threads that have gone unanswered for more than 48 hours
- Suppress newsletters, automated notifications, and FYI-only mail

Produce a JSON array matching the inbox item schema.`,
  },
  {
    id: 'calendar-prep',
    icon: '📅',
    title: 'Calendar prep',
    schedule: 'Daily, 06:45',
    description: 'Reviews upcoming meetings and flags ones that lack an agenda or pre-read.',
    prompt: `You are a chief of staff for [YOUR NAME].

Review the calendar events for the next 5 business days. For each meeting:
- Flag meetings with no agenda, no pre-read, or unclear objectives
- Identify back-to-back blocks with no buffer
- Note any prep actions that should happen before the meeting

Produce a JSON array matching the inbox item schema.`,
  },
  {
    id: 'project-pulse',
    icon: '🔭',
    title: 'Project pulse',
    schedule: 'Weekly, Monday 08:00',
    description: 'Summarises open project threads and surfaces items that have stalled.',
    prompt: `You are a chief of staff for [YOUR NAME].

Review the project notes and recent activity below. Identify:
- Work items that have not moved in more than 7 days
- Dependencies waiting on other people
- Promises made that have no visible follow-up

Produce a JSON array matching the inbox item schema.`,
  },
  {
    id: 'client-health',
    icon: '🤝',
    title: 'Client health',
    schedule: 'Weekly, Friday 16:00',
    description: 'Checks in on client relationships and flags any deteriorating signals.',
    prompt: `You are a chief of staff for [YOUR NAME].

Review recent client communications and meeting notes. Flag:
- Clients who have not been contacted in more than 2 weeks
- Open questions or commitments that haven't been resolved
- Any tone shifts in recent communications that suggest dissatisfaction

Produce a JSON array matching the inbox item schema.`,
  },
]
