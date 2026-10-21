// platform-api-prefix: written into an OpenMAIC checkout as instrumentation-client.ts by
// rails/openmaic/tools/platform-basepath.py. The source of truth is that file's sibling in the
// ai-platform repo; an edit made in the checkout is overwritten by the next patch run.
//
// WHY THIS EXISTS. The platform serves OpenMAIC under a basePath (/openmaic/api/app). Next applies
// basePath to the URLs it generates itself, but not to a hand-written fetch('/api/...'). OpenMAIC
// has about seventy of those, so every one of its API calls left the prefix, reached the
// gateway's own /api/ at the origin root and 404ed there. Three of them run on every page load
// (/api/server-providers, /api/comfyui-workflows, /api/access-code/status). Losing the first meant
// the app never received its server-side model configuration and showed "Set up model".
//
// WHAT IT DOES. Next runs instrumentation-client before React hydration, so wrapping the request
// APIs here wraps them before any component effect can call them. fetch, XMLHttpRequest.open and
// EventSource are wrapped. A URL is rewritten only when it resolves to THIS origin and its path
// starts with /api/: that one prefix is moved under the basePath, and the original form is kept, so
// a root-relative string stays root-relative. Every other URL is passed through as it was:
// cross-origin calls, Next's own basePath-aware requests, and paths already under the prefix.
//
// WHAT IT DOES NOT COVER. A URL the BROWSER loads from markup (<img src>, <audio src>, <a href>)
// never goes through these APIs, and neither does window.open. See rails/openmaic/README.md.
//
// The page must itself be under the basePath before anything is wrapped. In production that is
// always true, because Next serves this bundle only under its basePath. The check exists so that
// a test which injects this code into every frame of a page cannot rewrite the requests of a
// document that is not OpenMAIC's.
//
// No imports, so plain node can load this file for tests (rails/openmaic/frontend/tests/).

const API = '/api/'
const INSTALLED = '__platformApiPrefix'

/** The basePath with no trailing slash, or '' when it is unset or not a path. */
export function normaliseBase(base: string | undefined | null): string {
  const b = (base || '').trim().replace(/\/+$/, '')
  return b.startsWith('/') ? b : ''
}

/** `url` moved under `base` when it is a same-origin /api/ URL as seen from `pageHref`, and
 *  `url` unchanged otherwise. Never throws: a URL that does not parse is returned as given, and
 *  the request API it was meant for reports it in its own words. */
export function rewriteApiUrl(url: string, base: string, pageHref: string): string {
  if (!base) return url
  let target: URL
  let page: URL
  try {
    page = new URL(pageHref)
    target = new URL(url, page)
  } catch {
    return url
  }
  if (target.origin !== page.origin || !target.pathname.startsWith(API)) return url
  const tail = base + target.pathname + target.search + target.hash
  // Keep the caller's form. A root-relative string stays root-relative, and anything else
  // (absolute, protocol-relative) comes back absolute.
  return url.startsWith('/') && !url.startsWith('//') ? tail : target.origin + tail
}

/** The parts of a window this touches. A real Window satisfies it; tests pass a fake. */
export interface PrefixTarget {
  location: { href: string; pathname: string }
  fetch: typeof fetch
  Request: typeof Request
  EventSource?: typeof EventSource
  XMLHttpRequest?: typeof XMLHttpRequest
  [INSTALLED]?: string
}

/** Wrap the target's request APIs so same-origin /api/ calls go under `rawBase`. Returns whether
 *  the wrappers are in place. Idempotent: a second call with the same base changes nothing. */
export function installApiPrefix(win: PrefixTarget, rawBase: string | undefined | null): boolean {
  const base = normaliseBase(rawBase)
  if (!base) return false
  const path = win.location.pathname
  if (path !== base && !path.startsWith(base + '/')) return false
  if (win[INSTALLED] === base) return true
  const rewrite = (url: string): string => rewriteApiUrl(url, base, win.location.href)

  const originalFetch = win.fetch
  const RequestCtor = win.Request
  win.fetch = function prefixedFetch(input: RequestInfo | URL, init?: RequestInit) {
    let next: RequestInfo | URL = input
    if (typeof input === 'string') {
      next = rewrite(input)
    } else if (input instanceof RequestCtor) {
      const moved = rewrite(input.url)
      // new Request(url, request) is the standard way to change only the URL: method, headers,
      // body and signal are all read from the request it is given.
      if (moved !== input.url) next = new RequestCtor(moved, input)
    } else if (input && typeof (input as URL).href === 'string') {
      const moved = rewrite((input as URL).href)
      if (moved !== (input as URL).href) next = moved
    }
    return originalFetch.call(win, next, init)
  } as typeof fetch

  const XHR = win.XMLHttpRequest
  if (XHR) {
    const open = XHR.prototype.open as (this: XMLHttpRequest, ...args: unknown[]) => void
    XHR.prototype.open = function prefixedOpen(this: XMLHttpRequest, ...args: unknown[]) {
      if (args.length >= 2 && args[1] != null) args[1] = rewrite(String(args[1]))
      return open.apply(this, args)
    } as typeof XHR.prototype.open
  }

  const ES = win.EventSource
  if (ES) {
    // A subclass, so instanceof EventSource and the CONNECTING/OPEN/CLOSED statics keep working.
    win.EventSource = class PrefixedEventSource extends ES {
      constructor(url: string | URL, init?: EventSourceInit) {
        super(rewrite(String(url)), init)
      }
    }
  }

  win[INSTALLED] = base
  return true
}

// Next replaces process.env.PLATFORM_BASE_PATH with the build's NEXT_BASE_PATH (the `env` entry
// platform-basepath.py adds to next.config.ts). Unset, it is '' and nothing is wrapped, so a tree
// built standalone behaves exactly as upstream ships it.
if (typeof window !== 'undefined') {
  installApiPrefix(window as unknown as PrefixTarget, process.env.PLATFORM_BASE_PATH)
}
