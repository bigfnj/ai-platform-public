// Model markdown to HTML that is safe to hand to dangerouslySetInnerHTML.
//
// Every caller renders text a model wrote, and a model restates its input: a rail asked to quote
// untrusted text, such as the descriptions on an uploaded statement, can echo a memo carrying
// `<img onerror=...>` back in the reply, where it runs in the viewer's session. marked 12 passes raw
// HTML and `javascript:` links through untouched (its `sanitize` option was removed in v5), so the
// output is filtered here against an allowlist of what markdown itself produces.
//
// Fails CLOSED. DOMPurify returns its input unchanged when it finds no usable DOM
// (`if (!DOMPurify.isSupported) return dirty`), so without one this escapes the text instead.
//
// Byte-identical in recipe-book, finance and bouquet, and web/tests/markdown-sinks.test.js holds
// them to that: each rail builds on its own, so web-core cannot carry the dependency for them.
import { marked } from "marked";
import DOMPurify from "dompurify";

// The elements marked emits for prose, lists, code, quotes and GFM tables, and nothing else. No
// img (a remote image is a request that reports the page was opened), no form or input, no
// style element or attribute, and no class or id, so a reply cannot borrow the platform's own
// overlay classes or clobber a named global.
const ALLOWED_TAGS = [
  "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "em", "i", "del", "s",
  "code", "pre", "blockquote", "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td", "a",
];
const ALLOWED_ATTR = ["href", "title", "align", "start"];

export function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

export function markdownToSafeHtml(md: string | null | undefined): string {
  const text = md ?? "";
  if (!DOMPurify.isSupported) return `<p>${escapeHtml(text)}</p>`;
  const raw = marked.parse(text, { async: false }) as string;
  return DOMPurify.sanitize(raw, { ALLOWED_TAGS, ALLOWED_ATTR, ALLOW_DATA_ATTR: false });
}
