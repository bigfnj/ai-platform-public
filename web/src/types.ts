// web-core shared types.

export type Theme = 'light' | 'dark' | 'system'
// `cli` is the Claude-backend colour (--cli in styles.css), not a health tone: it marks what a
// Claude call is, the way `accent` marks emphasis, and says nothing about whether it is going well.
export type Tone = 'good' | 'warning' | 'critical' | 'accent' | 'neutral' | 'cli'

// An app in the platform's left rail.
export interface AppEntry {
  id: string
  label: string
  icon: string // emoji or short glyph for the rail
  status: 'ready' | 'soon'
}

// Platform GPU/model status, surfaced by the gateway's /api/platform/*.
export interface Gpu {
  total_mib: number
  used_mib: number
  free_mib: number
  gpu_name: string
}
export interface LoadedModel {
  name: string
  class: 'heavy' | 'embed'
  size_vram: number | null
}
// A job in the broker's GPU queue: the active one plus any waiting behind it.
export interface ModelFallback {
  model: string      // what is actually running
  requested: string  // what the caller asked for, e.g. "@vision"
  original: string   // what the role resolved to before substitution
  reason: string     // "disabled" | "not installed"
  category: string
}
export interface QueueJob {
  seq: number
  model: string | null
  source: string | null // rail name (from its @role), or null for a generic/manual job
  state: 'active' | 'waiting'
  // Present only when this job is running a SUBSTITUTE. Without it the widget would show a
  // model nobody asked for, with no explanation.
  fallback?: ModelFallback | null
}
// A NON-ADMIN receives an allowlist of this (apps/platform/backend/platform_gateway_app/
// status_view.py): what ModelWidget draws and nothing else. A field the widget starts reading
// must be admitted there as well, or plain users never receive it while admins, who get the
// broker's whole /v1/status (worker interpreter paths, ollama_version...), see it work.
export interface PlatformStatus {
  broker_reachable: boolean
  ollama_reachable: boolean
  loaded: LoadedModel[]
  heavy_loaded: string[]
  gpu: Gpu | null
  queue: { active: number; waiting: number }
  jobs: QueueJob[]
  // Persistent, unlike a job: a job lives for seconds, so a marker that appears for four of
  // them is not a report. This is what lets the widget stay warning-toned with an empty queue.
  fallbacks?: { count: number; last: ModelFallback | null }
  // The broker's owner-only Claude CLI backend. ABSENT for anyone who is neither an admin nor
  // an owner, and absent from an older gateway, so every reader must treat a missing key as
  // "nothing to show" rather than as "not ready".
  cli?: CliStatus
}

// --- Claude CLI backend, as /api/platform/status reports it ---------------------------------
// A Claude call is NOT a GPU job: it holds no slot in the broker's queue, cannot be cancelled
// from here, and does not touch VRAM. That is why it has its own shape rather than riding on
// QueueJob, and why the widget keeps it out of the GPU-busy arithmetic.
export interface CliActiveCall {
  role: string            // the broker role, e.g. "@iep"
  rail: string | null     // friendly rail name when the role belongs to one rail
  since: number           // epoch SECONDS (a float), on the broker's clock
  requester?: string      // admins only: whose subscription call this is
}
// Only the LATEST outcome per role is kept, so `ok: false` means that role's most recent Claude
// call fell back to the local model, or (reason `interrupted`, its failure's slug in `cause`) its
// stream stopped after the first token with no local answer after it; the next success replaces
// it. An owner receives only the roles whose latest call was their own; `requester` (whose call
// it was) reaches admins only.
export type CliRoleOutcome =
  | { ok: true; at: number; requester?: string }
  | { ok: false; at: number; reason: string; cause?: string; requester?: string }
interface CliStatusCommon {
  backend: 'claude'
  ready: boolean
  roles: string[]
  active: CliActiveCall[]
  last_by_role: Record<string, CliRoleOutcome>
}
/** What a non-admin OWNER sees: enough to know their own calls are on Claude, nothing about
 *  the install or the other owners. */
export interface CliStatusOwner extends CliStatusCommon {
  for_you: true
}
/** What an ADMIN sees: the broker's whole cli_state() block. */
export interface CliStatusAdmin extends CliStatusCommon {
  enabled: boolean
  blockers: string[]
  exe: string
  owners: string[]
  workdir: string
  token_problem: string | null
  /** Where `roles` came from: the BROKER_CLI_ROLES env set, or the Rails tab's switch file. */
  roles_source?: 'env' | 'file'
  roles_file_set?: boolean
  roles_problem?: string
  /** The concurrency cap (BROKER_CLI_CONCURRENCY) and the calls waiting for a slot. */
  concurrency?: { limit: number; waiting: number }
  calls: number
  refusals: Record<string, number>
  fallbacks: { count: number; last: { requested: string; reason: string; detail?: string } | null }
}
export type CliStatus = CliStatusAdmin | CliStatusOwner

// --- Rail model chips ------------------------------------------------------------------------
// Here rather than in ModelChips.tsx so the pure view logic (model_chip_view.ts) can name the
// type without importing a .tsx file, which node's type stripping cannot load.

export type ModelState = 'missing' | 'cold' | 'warming' | 'loaded'

export interface ModelChip {
  /** Stable key for the slot (matches the rail's manifest slot id where there is one). */
  slot: string
  /** Human label for the slot, e.g. "LLM" or "Retrieval". */
  label: string
  /** The concrete model the slot resolves to, e.g. "mistral-small3.2:24b". Optional. */
  model?: string
  /** Residency of the LOCAL model. On a Claude slot this is the health of the fallback, not of
   *  what answers the viewer's own jobs. */
  state: ModelState
  /** The broker role, shown only in the hover title. Optional. */
  role?: string
  /** Which broker runs this slot: 'local' (or absent) for this box, otherwise a registered
   *  upstream's name. A delegated slot is worth marking for two reasons: a green chip then
   *  visibly depends on ANOTHER machine being up, and such a slot has no `warming` state —
   *  warming means "a job is queued on a model that is not resident", and both the queue and
   *  the model are on the other box. Unmarked, that absence reads as a bug. */
  upstream?: string
  /** 'claude' means THIS viewer's own jobs on this slot run on Claude through the broker's CLI
   *  backend; 'ollama' (or absent, from an older broker) means the local model answers;
   *  'external' means the rail calls an endpoint of its own configuration and the broker never
   *  sees the slot, so `state` is only the rail's guess (`upstream` is 'external' then too). A
   *  separate key rather than a fifth ModelState, because a rail bundle that is not rebuilt
   *  ignores an unknown key but would index MODEL_STATE_TEXT with an unknown state. */
  backend?: 'ollama' | 'claude' | 'external'
  /** A Claude call on this slot is in flight for this viewer. Ignored on a local or external
   *  slot: on a local one `warming` already says the same thing, and the broker sees no call on
   *  an external one. */
  busy?: boolean
}
export interface InstalledModel {
  name: string
  class: 'heavy' | 'embed'
}

// Admin -> Models: the workstation model pool. Every installed model, annotated with whether a
// rail role uses it (in_use + roles), whether it's resident in VRAM now (loaded), and the
// admin availability flag (enabled). Enable/Disable is reversible; Delete is not.
export interface ModelPoolEntry {
  name: string
  class: 'heavy' | 'embed' | string | null
  parameter_size?: string | null
  size?: number | null
  vision: boolean
  loaded: boolean
  in_use: boolean
  roles: string[]
  enabled: boolean
  /** Editorial category id (chat / reasoning / vision / code / embed / image / other). */
  category?: string
  /** One-line "good at / why", shown under the model in the Models tab. */
  blurb?: string
  /** True when category+blurb were auto-generated by the broker LLM (no curated entry), so the
   * Models tab can tag it 'auto'. Curated and fallback entries are false/undefined. */
  generated?: boolean
}

/** One rail as the admin Rail Manager sees it. `state` is DERIVED by the gateway from what is
 * installed plus the rail_state table, so it never goes stale:
 *   enabled     — installed here and switched on
 *   disabled    — installed, switched off by an admin (reversible)
 *   unavailable — in the catalog but not in this deployment's enabled_apps, which the UI cannot
 *                 change: the rail's static mount is registered when the gateway starts. */
export interface RailManagerEntry {
  id: string
  label: string
  icon: string
  description: string | null
  status: 'ready' | 'soon' | string
  state: 'enabled' | 'disabled' | 'unavailable'
  installed: boolean
  /** False when no built bundle resolves — the rail would render blank rather than error. */
  bundle_built: boolean
  has_backend: boolean
  entitled_users: number
  /** Why an `unavailable` rail is not mounted on this box, so the card names the setting to
   *  change: 'disabled_apps' (PLATFORM_DISABLED_APPS lists it), 'not_enabled' (it is not in
   *  PLATFORM_ENABLED_APPS). null for a rail this box runs. From main.py _rail_blocked_by, the
   *  rule the toggle's refusal uses too. */
  blocked_by?: 'disabled_apps' | 'not_enabled' | null
}

/** A model-pool category: id, display label, and sort order. From the gateway's model_catalog. */
export interface ModelCategory {
  id: string
  label: string
  order: number
}

// Admin -> Schedule: the central scheduler. Each rail owns maintenance tasks with an editable
// recurrence (Outlook-style, minus Duration). The gateway fires them and records next/last run.
export interface Recurrence {
  freq: 'daily' | 'weekly' | 'monthly'
  interval: number
  byweekday?: number[] // weekly, Mon=0 .. Sun=6
  bymonthday?: number // monthly, 1..31 or -1 (last day)
  at: string // 'HH:MM' wall-clock in tz
  tz: string // IANA zone
}
export interface ScheduleTask {
  task_id: string
  label: string
  description: string
  recurrence: Recurrence
  enabled: boolean
  next_run: string | null // ISO UTC
  last_run: string | null
  last_status: string | null
}
export interface RailSchedules {
  rail: string
  icon: string
  tasks: ScheduleTask[]
}

// Theming: a palette (color family) + mode, with a platform default and optional
// per-user override. Effective = override where set, else the platform default.
export interface ThemeState {
  palette: string
  mode: Theme
  default: { palette: string; mode: Theme }
  override: { palette: string | null; mode: string | null } | null
  palettes: string[]
  modes: string[]
}

// Auth / multi-tenant.
export interface Me {
  username: string
  is_admin: boolean
  theme: ThemeState
}
export interface AdminUser {
  id: number
  username: string
  is_admin: boolean
  is_superadmin: boolean
  apps: string[]
}
// A shared workspace (a "room"): entitlements say which APPS you may reach, a workspace says
// whose DATA you see inside one. Membership is symmetric and full-rights, so every member of a
// workspace sees and edits every other member's records in the rails that honour rooms.
export interface Workspace {
  id: number
  name: string
  members: string[] // platform usernames, sorted by the gateway
}

// Admin -> Rails: per-rail model slots. Each slot maps to a broker role that resolves to
// a concrete installed model; changing it repoints the ROLE, so every rail with a slot on that
// role moves with it (RailModelSlot.shared_with names the others).
export interface ModelOption {
  name: string
  class: 'heavy' | 'embed'
  parameter_size?: string | null
  vision: boolean // model accepts image input (a vision slot only offers these)
}
// A media (image) backend for an "image" slot — not an Ollama model.
export interface MediaOption {
  name: string
  label: string
  note?: string
}
export interface RailModelSlot {
  slot: string
  label: string
  role: string
  kind: 'chat' | 'vision' | 'image' // capability; filters which models are offered
  env: string
  default: string // the model/glob this slot shipped with (revert target)
  description: string
  model: string | null // concrete model the role resolves to (null if nothing installed matches)
  pattern: string // the role's stored name/glob
  installed: boolean
  /** Labels of the OTHER installed rails whose slots use this role ([] when it is this rail's
   *  alone). Computed by the gateway (rails_models.py): a repoint moves all of them. */
  shared_with: string[]
  /** Which broker runs this slot: 'local', or a registered upstream's name. Index
   *  `RailsSettings.models` with it — a delegated slot's choices come from THAT box's
   *  inventory, not this card's. Always present. */
  upstream: string
  /** The Claude switch. `cli_on`: the broker lets an owner's own call on this ROLE go to the
   *  Claude CLI. `cli_wired`: this rail passes who is asking for this slot (without that every
   *  call stays local whatever the switch says). `cli_ready`: the backend is ready apart from
   *  which roles are on. All three from the gateway (main.py _rails_payload). */
  cli_on: boolean
  cli_wired: boolean
  cli_ready: boolean
  /** The Claude model the broker's switch file gives this slot's ROLE, or null for the
   *  default (`RailsCli.default_model`). From the gateway, like the three above. */
  cli_model: string | null
  /** Whether the slot has the Claude switch and model select at all: its kind is one the
   *  switch is for (the gateway's CLI_SLOT_KINDS, a text or a vision slot, never an image
   *  slot). The tab draws the row from this alone (rails_view.ts hasCliSwitch). */
  cli_switchable: boolean
  /** Why the switch cannot be turned ON on this broker, or null: on a vision role, the
   *  broker does not say it can send images to Claude (`RailsCli.vision`); on a streaming role
   *  (a slot whose rail streams its answers on it), the broker does not say it streams to Claude
   *  (`RailsCli.streams`). The gateway's own sentence, the one its 409 gives; switching OFF
   *  stays allowed. */
  cli_unavailable: string | null
}
/** The broker's Claude switch state, from the Rails tab's one read (gateway _cli_block). */
export interface RailsCli {
  ready: boolean
  blockers: string[]
  /** The blockers that disable the switches: every one except those a switch itself fixes
   *  (no_roles_configured, cli_all_roles_off). */
  switch_blockers: string[]
  enabled_roles: string[]
  /** 'env' (BROKER_CLI_ROLES) or 'file' (the switches, BROKER_CLI_ROLES_FILE). */
  roles_source: 'env' | 'file'
  /** Whether the broker has a switch file configured at all; without one a switch is refused. */
  roles_file_set: boolean
  roles_problem: string
  /** role -> the Claude model the switch file gives it, for every role it names with one. */
  role_models: Record<string, string>
  /** The model for a role with none of its own (BROKER_CLI_MODEL); '' is the CLI's own default. */
  default_model: string
  /** Whether the broker says it sends images to the Claude CLI; false from one that does not
   *  say. A vision role can be switched ON only when it is true. */
  vision: boolean
  /** Whether the broker says it sends streamed chat to the Claude CLI; false from one that does
   *  not say. A streaming role can be switched ON only when it is true. */
  streams: boolean
}
export interface RailModels {
  id: string
  label: string
  icon: string
  slots: RailModelSlot[]
}
/** A broker a role can be pointed at. `local` is always present and always healthy (it is this
 *  box). `healthy` deliberately collapses "the box is off" and "the box refuses our token" into
 *  one boolean: both mean the same thing to a picker, which is do not offer it. */
export interface UpstreamInfo {
  name: string
  url: string
  healthy: boolean
}
export interface RailsSettings {
  rails: RailModels[]
  /** Installed generative models PER BROKER, keyed by upstream name. Keyed rather than flat
   *  because a slot's valid choices depend on where it runs. Every reported upstream has a
   *  key — empty when its inventory could not be read — so a slot can be indexed by its own
   *  `upstream` with no missing-key branch. */
  models: Record<string, ModelOption[]>
  media: MediaOption[] // image backends (image slots) — local only; images are never delegated
  upstreams: UpstreamInfo[]
  cli: RailsCli
  /** The Claude models a role may be given (the broker's BROKER_CLI_MODELS), in its order; []
   *  from a broker older than the model choice. */
  cli_models: string[]
  /** Whether THIS admin may flip a Claude switch or choose a Claude model: super-admin only. */
  cli_editable: boolean
}
/** The Claude switch's reply when the broker took the switch but the gateway could not re-read
 *  the Rails tab afterwards (gateway admin_set_rail_cli). The switch is LIVE, so the reply is
 *  its own `cli` block and no `rails`: the shell redraws the slots it already has from it
 *  (rails_view.ts applyCliReply) and shows `reload_error`. */
export interface RailsCliSwitched {
  cli: RailsCli
  cli_models: string[]
  cli_editable: boolean
  reload_error: string
}

/** A named broker access token. The plaintext is NEVER in this shape -- the broker stores a
 *  sha256 hash, so `prefix` is all there is to identify a row by after creation. */
export interface BrokerToken {
  id: string
  label: string
  scope: string
  prefix: string
  created: string
  /** Epoch seconds, or null. In-memory on the broker: it resets on a restart, so "never" can
   *  mean "not since the broker last started" rather than "never used". */
  last_seen: number | null
}
