import type { AppEntry } from '@web-core'

// The platform's app rail. 'ready' apps load at runtime as federated remotes;
// 'soon' apps are roadmap placeholders. Order here is the order in the left rail.
// EDU-Suite is the recommitted first citizen; it flips to 'ready' once it mounts
// as a remote. recipe-book/console were removed in the edu-suite-first recommit.
export const APPS: AppEntry[] = [
  { id: 'edu-suite', label: 'EDU-Suite', icon: '🎓', status: 'ready' },
  { id: 'smb-partner-enablement', label: 'SMB Partner', icon: '🤝', status: 'ready' },
]
