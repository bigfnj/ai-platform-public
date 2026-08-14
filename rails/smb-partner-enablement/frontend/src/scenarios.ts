/**
 * The four SMB scenarios from the original prototype (see ../../reference/README.md).
 *
 * Kept as data rather than markup so the Scenario Builder is a loop over this list, and so a
 * fifth scenario is a data edit. `collections` scopes retrieval for that scenario's generation
 * pass — a restaurant question should not be answered out of the auto-dealership material.
 *
 * The questions are the diagnostic: four, no more. Each carries `why`, shown to the partner as
 * "Why this matters" — it is what makes the tool feel like coaching rather than a form.
 */

export type Question = {
  id: string
  prompt: string
  why: string
  options: string[]
}

export type Scenario = {
  id: string
  icon: string
  title: string
  fit: string
  situation: string
  collections: string[]
  questions: Question[]
}

export const SCENARIOS: Scenario[] = [
  {
    id: 'auto-dealership',
    icon: '🚗',
    title: 'Auto Dealership',
    fit: 'Azure Migration + Copilot for Sales',
    situation:
      'SMB dealership group moving off on-prem infrastructure, wants sellers using AI-generated cold call scripts.',
    collections: ['solution-plays', 'discovery', 'objection-handling'],
    questions: [],
  },
  {
    id: 'restaurant-group',
    icon: '🍽️',
    title: 'Restaurant Group',
    fit: 'Azure Consolidation + Copilot for Frontline Managers',
    situation:
      'Multi-location SMB restaurant group with disconnected POS systems, paper scheduling, and no centralized data visibility.',
    collections: ['solution-plays', 'discovery', 'objection-handling'],
    questions: [],
  },
  {
    id: 'retail-chain',
    icon: '🛍️',
    title: 'Retail Chain',
    fit: 'Teams Frontline + Copilot for Store Ops',
    situation:
      'Multi-location SMB retailer with frontline staff, paper schedules, and no shared communication layer across stores.',
    collections: ['solution-plays', 'discovery', 'objection-handling'],
    // The one question recovered verbatim from the demo capture. The remaining three, and the
    // other scenarios' sets, are authored against the real SME material — see
    // seed/knowledge-base/discovery/.
    questions: [
      {
        id: 'locations',
        prompt: 'How many store locations does this retailer operate?',
        why: 'Determines whether this customer is worth flagging for co-sell, and which frontline SKUs make sense at their scale.',
        options: ['2–5 locations', '6–15 locations', '16–50 locations', '50+ locations'],
      },
    ],
  },
  {
    id: 'professional-services',
    icon: '💼',
    title: 'Professional Services',
    fit: 'M365 Security + Copilot for Knowledge Work',
    situation:
      'Accounting, legal, or consulting firm handling sensitive client data with security gaps and partners asking about AI.',
    collections: ['solution-plays', 'discovery', 'objection-handling'],
    questions: [],
  },
]

/** The generation steps shown while the package builds. Each names a real stage of the pass. */
export const BUILD_STEPS = [
  'Analyzing diagnostic answers…',
  'Grounding in Microsoft product catalog…',
  'Applying MCEM sales methodology…',
  'Generating discovery playbook…',
  'Building customer Q&A pack…',
  'Drafting ROI summary…',
  'Finalizing directional close…',
]

/** The output tabs on a completed diagnostic. */
export const OUTPUT_TABS = [
  { id: 'scenario-card', icon: '📋', label: 'Scenario Card' },
  { id: 'discovery', icon: '🔍', label: 'Discovery Playbook' },
  { id: 'qa', icon: '💬', label: 'Customer Q&A' },
  { id: 'roi', icon: '📊', label: 'ROI Summary' },
] as const
