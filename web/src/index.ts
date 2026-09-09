// web-core barrel: the shared design system + unified shell chrome + platform
// status client. Apps import from here; the shell also imports './styles.css'.

export { AppShell, ThemeToggle, ThemeMenu } from './AppShell'
export type { ThemeMenuProps } from './AppShell'
export { ModelWidget } from './ModelWidget'
export { platformApi } from './platformApi'
// The one rail header + the shared model-chip row every rail uses (RAIL_CONTRACT.md).
export { RailHeader } from './RailHeader'
export type { RailHeaderProps } from './RailHeader'
export { ModelChips, MODEL_STATE_TEXT } from './ModelChips'
export type { ModelChip, ModelChipsProps, ModelState } from './ModelChips'
// Voice. Two per-rail chips a rail drops beside the thing they act on — SpeakButton takes the
// text to read, DictateButton hands back the transcript — plus VoiceControls, the shell-chrome
// mic that works on any rail with no rail code (and is the fallback where no chip exists yet).
export { VoiceControls, DEFAULT_VOICE } from './VoiceControls'
export { SpeakButton } from './SpeakButton'
export type { SpeakButtonProps } from './SpeakButton'
export { DictateButton } from './DictateButton'
export type { DictateButtonProps } from './DictateButton'
export {
  canRecord, canSpeak, playWav, record, speakable, stopSpeaking,
  getAudioInputDevices, getAudioOutputDevices, setAudioInput, setAudioSink,
} from './voice'
export type { Recorder } from './voice'
export {
  Badge,
  Button,
  Card,
  CardHeader,
  FavButton,
  HeartIcon,
  Spinner,
  Stars,
  StatTile,
  TagChip,
} from './ui'
export type {
  AdminUser,
  AppEntry,
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
  RailsSettings,
  Recurrence,
  ScheduleTask,
  Theme,
  ThemeState,
  Tone,
} from './types'
