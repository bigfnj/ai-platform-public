// web-core barrel: the shared design system + unified shell chrome + platform
// status client. Apps import from here; the shell also imports './styles.css'.

export { AppShell, ThemeToggle, ThemeMenu } from './AppShell'
export type { ThemeMenuProps } from './AppShell'
export { ModelWidget } from './ModelWidget'
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
