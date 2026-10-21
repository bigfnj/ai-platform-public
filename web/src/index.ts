// web-core barrel: the shared design system + unified shell chrome + platform
// status client. Apps import from here; the shell also imports './styles.css'.

export { AppShell } from './AppShell'
export type { ThemeMenuProps } from './AppShell'
export { ModelWidget } from './ModelWidget'
export { platformApi, HttpError } from './platformApi'
// Polling: hidden-tab skip, no overlap, stop on a verdict or unmount (poll.ts says why). The
// hook is exported, not startPoll, the loop under it: nothing outside web-core imported it, so
// it went with the list below (2026-10-14). Re-export it the day a poller outside a component
// needs it.
export { stallDetector } from './poll'
export type { PollTick, PollVerdict } from './poll'
export { usePoll } from './usePoll'
// The one rail header + the shared model-chip row every rail uses (RAIL_CONTRACT.md).
export { RailHeader } from './RailHeader'
export type { RailHeaderProps } from './RailHeader'
export { ModelChips, MODEL_STATE_TEXT } from './ModelChips'
export type { ModelChip, ModelChipsProps, ModelState } from './ModelChips'
// Voice. Two per-rail chips a rail drops beside the thing they act on — SpeakButton takes the
// text to read, DictateButton hands back the transcript — plus VoiceControls, the top-bar voice
// SETTINGS (read-aloud voice and speed, the microphone and speaker); the global top-bar mic it
// used to host was retired (VoiceControls.tsx says why).
//
// Not exported, because nothing outside web-core ever imported them (checked over every rail
// and the shell): the raw voice functions (playWav, record, stopSpeaking, the device setters,
// canRecord / canSpeak / speakable), DEFAULT_VOICE, ThemeMenu, and the Card / CardHeader /
// FavButton / HeartIcon / Spinner / Stars / StatTile / TagChip primitives (each rail ships its
// own). Re-export one here the day a rail needs it. MODEL_STATE_TEXT above stays exported on
// purpose: it is the shared four-state text gemini-cx still duplicates as its own STATE_TEXT.
export { VoiceControls } from './VoiceControls'
export { SpeakButton } from './SpeakButton'
export type { SpeakButtonProps } from './SpeakButton'
export { DictateButton } from './DictateButton'
export type { DictateButtonProps } from './DictateButton'
export { Badge, Button } from './ui'
export type {
  AdminUser,
  AppEntry,
  BrokerToken,
  CliActiveCall,
  CliRoleOutcome,
  CliStatus,
  CliStatusAdmin,
  CliStatusOwner,
  Gpu,
  InstalledModel,
  LoadedModel,
  Me,
  MediaOption,
  ModelCategory,
  ModelOption,
  ModelPoolEntry,
  PlatformStatus,
  RailManagerEntry,
  RailModels,
  RailModelSlot,
  RailSchedules,
  RailsCli,
  RailsCliSwitched,
  RailsSettings,
  Recurrence,
  ScheduleTask,
  Theme,
  ThemeState,
  Tone,
  UpstreamInfo,
  Workspace,
} from './types'
