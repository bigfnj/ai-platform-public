// web-core barrel: the shared design system + unified shell chrome + platform
// status client. Apps import from here; the shell also imports './styles.css'.

export { AppShell, ThemeToggle, ThemeMenu } from './AppShell'
export type { ThemeMenuProps } from './AppShell'
export { ModelWidget } from './ModelWidget'
// Voice is a PLATFORM capability: dictation from the shell top bar (VoiceControls) and
// read-aloud from a rail (SpeakButton). Both reach the broker through the gateway, so a rail
// never needs a broker URL or the broker token. See voice.ts for why dictation has no
// browser-speech fallback.
// Per-rail chips are the PRIMARY surface: both take explicit text/callbacks, so neither writes
// into a field it does not own. VoiceControls (the top-bar mic) is an optional fallback for
// rails that have not adopted a chip — it has to fake typing into a React-controlled input,
// which is four workarounds a callback does not need. Prefer DictateButton.
export { DictateButton } from './DictateButton'
export type { DictateButtonProps } from './DictateButton'
export { VoiceControls, VOICES, loadVoicePrefs } from './VoiceControls'
export type { VoiceControlsProps, VoicePrefs } from './VoiceControls'
export { SpeakButton } from './SpeakButton'
export type { SpeakButtonProps } from './SpeakButton'
export {
  audioDevices,
  canRecord,
  isSpeaking,
  playWav,
  record,
  setAudioInput,
  setAudioOutput,
  speakLocal,
  speakable,
  stopSpeaking,
  synthesize,
} from './voice'
export type { Recorder, SpeakOptions, SttResult, TtsResult } from './voice'
export { platformApi } from './platformApi'
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
  ModelOption,
  ModelPoolEntry,
  PlatformStatus,
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
