// Mutation harness for shell-status.test.js. NOT a test file: it rewrites the sources.
//
//     node apps/platform/frontend/tests/mutate_shell_status.mjs
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { runMutations } from '../../../../web/tests/mutation_suite.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..', '..', '..', '..')
const SRC = join(HERE, '..', 'src')
const STATUS = join(SRC, 'shell_status.ts')
const CANON = join(SRC, 'canonical.ts')
const MAIN = join(SRC, 'main.tsx')
const APP = join(SRC, 'App.tsx')

runMutations({
  repo: REPO,
  suite: join(HERE, 'shell-status.test.js'),
  mutations: [
    {
      label: 'a 401 is read as one more miss (the old "Broker offline" for ever)',
      file: STATUS,
      old: '  if (!answer.ok && answer.httpStatus === 401) {',
      new: '  if (false) {',
      expect: 'a 401 is a lost session, not a broker outage',
    },
    {
      label: 'any failure is taken for a lost session',
      file: STATUS,
      old: '  if (!answer.ok && answer.httpStatus === 401) {',
      new: '  if (!answer.ok) {',
      expect: 'other failures are outages, and only the third in a row reads as offline',
    },
    {
      label: 'the first miss blanks the widget (no hysteresis)',
      file: STATUS,
      old: 'export const OFFLINE_AFTER = 3',
      new: 'export const OFFLINE_AFTER = 1',
      expect: 'other failures are outages, and only the third in a row reads as offline',
    },
    {
      label: 'a good answer does not reset the miss count',
      file: STATUS,
      old: '    return { status: answer.status, misses: 0, sessionLost: false }',
      new: '    return { status: answer.status, misses: prev.misses, sessionLost: false }',
      expect: 'a good answer resets the count',
    },
    {
      label: 'canonical.ts brings back a default domain',
      file: CANON,
      old: '  if (!configured || isSecureContext) return null',
      new: "  configured = configured || 'https://platform.example.com'\n  if (isSecureContext) return null",
      expect: 'no canonical URL configured means no redirect',
    },
    {
      label: 'main.tsx gives the variable a fallback again',
      file: MAIN,
      old: 'canonicalRedirect(import.meta.env.VITE_PLATFORM_CANONICAL_URL, location',
      new: "canonicalRedirect(import.meta.env.VITE_PLATFORM_CANONICAL_URL ?? 'https://platform.example.com', location",
      expect: 'main.tsx hands the build variable over as is, with no default of its own',
    },
    {
      label: 'a secure context is redirected too',
      file: CANON,
      old: '  if (!configured || isSecureContext) return null',
      new: '  if (!configured) return null',
      expect: 'never from a secure context, never to the same origin, never to http',
    },
    {
      label: 'an http canonical URL is accepted (a downgrade)',
      file: CANON,
      old: " || target.protocol !== 'https:') return null",
      new: ') return null',
      expect: 'never from a secure context, never to the same origin, never to http',
    },
    // --- the theme state (added 2026-10-14) -----------------------------------------------------
    {
      label: 'the server\'s palette list is stored in themeMeta again and never read',
      file: APP,
      old: '    hasOverride: boolean; defaultPalette: string; defaultMode: Theme\n'
        + "  }>({ hasOverride: false, defaultPalette: 'indigo', defaultMode: 'system' })\n",
      new: '    palettes: string[]; hasOverride: boolean; defaultPalette: string; defaultMode: Theme\n'
        + "  }>({ palettes: [], hasOverride: false, defaultPalette: 'indigo', defaultMode: 'system' })\n",
      expect: 'the theme state App.tsx keeps holds only what the shell reads',
    },
    {
      label: 'the menu offers the server\'s list instead of the shell\'s own',
      file: APP,
      old: '        palettes: FRONTEND_PALETTES,\n',
      new: '        palettes: me.theme.palettes,\n',
      expect: 'the theme state App.tsx keeps holds only what the shell reads',
    },
  ],
})
