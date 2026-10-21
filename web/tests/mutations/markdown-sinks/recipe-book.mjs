// mutate_markdown_sinks.mjs's cases for recipe-book's frontend. NOT a test file.
//
// A case lives with the rail whose source it quotes, so this module goes wherever recipe-book
// goes (tools/publish.json). Each `file` is relative to the repo; mutation_suite.mjs's
// railCases() loads it, and refuses a case that rewrites another rail's file. recipe-book's
// sanitiser is the copy the structure tests read, so its cases hold the sanitiser's own shape.
const SANITISER = 'rails/recipe-book/frontend/src/lib/markdown.ts'
const ORDER = 'the sanitiser fails closed, then returns marked output only through DOMPurify'
const ALLOWLISTS = 'the allowlists hold no element or attribute that can run, load or restyle'

export const MUTATIONS = [
  {
    label: "recipe-book's error path interpolates the message into markup again",
    file: 'rails/recipe-book/frontend/src/components/RecipeModal.tsx',
    old: 'setAssist({ loading: false, html: "", error: `Assistant unavailable: ${e?.message || "error"}`, mode });',
    new: 'setAssist({ loading: false, html: `<em>Assistant unavailable: ${e?.message}</em>`, error: "", mode });',
    expect: 'every dangerouslySetInnerHTML is fed by the sanitiser',
  },
  {
    label: "recipe-book's sanitiser returns marked output unsanitised (the copy the structure test reads)",
    file: SANITISER,
    old: '  return DOMPurify.sanitize(raw, { ALLOWED_TAGS, ALLOWED_ATTR, ALLOW_DATA_ATTR: false });',
    new: '  return raw;',
    expect: ORDER,
  },
  {
    label: 'the no-DOM guard is removed (DOMPurify then fails open)',
    file: SANITISER,
    old: '  if (!DOMPurify.isSupported) return `<p>${escapeHtml(text)}</p>`;\n',
    new: '',
    expect: ORDER,
  },
  {
    label: 'the no-DOM guard moves after the parse',
    file: SANITISER,
    old: '  if (!DOMPurify.isSupported) return `<p>${escapeHtml(text)}</p>`;\n  const raw = marked.parse(text, { async: false }) as string;\n',
    new: '  const raw = marked.parse(text, { async: false }) as string;\n  if (!DOMPurify.isSupported) return `<p>${escapeHtml(text)}</p>`;\n',
    expect: ORDER,
  },
  {
    label: 'the parsed HTML also leaks out unsanitised beside the sanitised copy',
    file: SANITISER,
    old: '  return DOMPurify.sanitize(raw, { ALLOWED_TAGS, ALLOWED_ATTR, ALLOW_DATA_ATTR: false });',
    new: '  (globalThis as any).__lastReply = raw;\n  return DOMPurify.sanitize(raw, { ALLOWED_TAGS, ALLOWED_ATTR, ALLOW_DATA_ATTR: false });',
    expect: ORDER,
  },
  {
    label: 'the tag allowlist lets <img> through',
    file: SANITISER,
    old: '"td", "a",\n];',
    new: '"td", "a", "img",\n];',
    expect: ALLOWLISTS,
  },
  {
    label: 'the attribute allowlist lets style through',
    file: SANITISER,
    old: 'const ALLOWED_ATTR = ["href", "title", "align", "start"];',
    new: 'const ALLOWED_ATTR = ["href", "title", "align", "start", "style"];',
    expect: ALLOWLISTS,
  },
]
