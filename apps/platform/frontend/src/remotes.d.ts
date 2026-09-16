// Runtime-federated app remotes served by the gateway. Each exposes a default
// React component mounted by the shell for its rail entry.
declare module 'recipe_book/module' {
  import type { ComponentType } from 'react'
  const RecipeBookModule: ComponentType
  export default RecipeBookModule
}
declare module 'workstation/module' {
  import type { ComponentType } from 'react'
  const WorkstationModule: ComponentType
  export default WorkstationModule
}
declare module 'terminal_fun/module' {
  import type { ComponentType } from 'react'
  const TerminalFunModule: ComponentType
  export default TerminalFunModule
}
declare module 'ai_playground/module' {
  import type { ComponentType } from 'react'
  const AiPlaygroundModule: ComponentType
  export default AiPlaygroundModule
}
declare module 'co_worker/module' {
  import type { ComponentType } from 'react'
  const CoWorkerModule: ComponentType
  export default CoWorkerModule
}
declare module 'smb_partner/module' {
  import type { ComponentType } from 'react'
  const SmbPartnerModule: ComponentType
  export default SmbPartnerModule
}
declare module 'meeting_atlas/module' {
  import type { ComponentType } from 'react'
  const MeetingAtlasModule: ComponentType
  export default MeetingAtlasModule
}

declare module 'gemini_cx/module' {
  import type { ComponentType } from 'react'
  const GeminiCxModule: ComponentType
  export default GeminiCxModule
}

declare module 'openmaic/module' {
  import type { ComponentType } from 'react'
  const OpenMaicModule: ComponentType
  export default OpenMaicModule
}
