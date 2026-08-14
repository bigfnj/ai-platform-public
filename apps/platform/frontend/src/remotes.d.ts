// Runtime-federated app remotes served by the gateway. Each exposes a default
// React component mounted by the shell for its rail entry.
declare module 'edu_suite/module' {
  import type { ComponentType } from 'react'
  const EduSuiteModule: ComponentType
  export default EduSuiteModule
}
declare module 'iep_app/module' {
  import type { ComponentType } from 'react'
  const IepModule: ComponentType
  export default IepModule
}
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
