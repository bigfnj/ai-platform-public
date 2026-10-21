"""Make an OpenMAIC checkout's base path env-driven, so it can be served under /openmaic/api/app/.

    python rails/openmaic/tools/platform-basepath.py --check   <path-to-OpenMAIC-checkout>
    python rails/openmaic/tools/platform-basepath.py           <path-to-OpenMAIC-checkout>
    python rails/openmaic/tools/platform-basepath.py --default-locale zh-CN <path-to-OpenMAIC-checkout>
    python rails/openmaic/tools/platform-basepath.py --revert  <path-to-OpenMAIC-checkout>

WHY THIS EXISTS. openmaic is the first rail here that WRAPS a third-party application. Upstream
THU-MAIC/OpenMAIC is a Next.js app that assumes it owns the origin; this platform serves every
rail under /<id>/. Next supports exactly that with `basePath`, but upstream does not set one, so
the app has to be told, at BUILD time, that it lives under a prefix.

WHY IT PATCHES RATHER THAN FORKS. The checkout stays pullable. A few small edits, each idempotent
and each reversible, beat a fork that has to be rebased against an app moving as fast as this one.

WHY IT IS ENV-DRIVEN RATHER THAN HARD-CODED. `NEXT_BASE_PATH` unset means no basePath at all, so
the same patched tree still builds and runs standalone. A hard-coded prefix would make the
checkout useless for anything but this platform, which is a rude thing to do to somebody's repo.

WHICH UPSTREAM. The anchors are upstream v1.1.3's (2026-10-05), which upstream main still had on
2026-10-09. v1.1.x added an `env` object to next.config.ts, so PLATFORM_BASE_PATH now goes into
that object. A CLEAN v1.0.x checkout has no `env` object and is refused (ANCHOR MISSING). One that
already carries the old patch is accepted: its three old edits are found by their markers, only the
locale edit is added, and --revert then refuses it (put it back with git, or with the version of
this script that patched v1.0.x, which the upgrade runbook in the rail's README keeps reachable).

THE EDITS, each found again by its own marker:
  next.config.ts             basePath + assetPrefix from NEXT_BASE_PATH        (platform-basepath)
  Dockerfile                 NEXT_BASE_PATH as a build arg in the builder      (platform-basepath)
  next.config.ts             PLATFORM_BASE_PATH in upstream's env object       (platform-api-prefix)
  instrumentation-client.ts  the request shim, tools/platform-api-prefix.ts    (platform-api-prefix)
  lib/i18n/types.ts          defaultLocale, en-US unless --default-locale      (platform-locale)
  Dockerfile                 /app/data owned by the runtime user, before USER  (platform-data-dir)
                             -- only where upstream does not already do it

WHY /app/data IS CHECKED. The app runs as an unprivileged user and writes classrooms, usage and
materials to /app/data, which compose mounts as a named volume. A fresh volume takes the owner of
the image's /app/data at its first mount (Docker's copy-up, which Docker repeats at every mount of
an EMPTY volume; podman's first-mount chown, which podman does once per volume), so that directory
must exist in the image and belong to the runtime user, or nothing the app saves is kept.
Upstream v1.1.3 does this itself (THU-MAIC/OpenMAIC#1438: `RUN mkdir -p /app/data && chown
-R nextjs:nodejs /app/data` before `USER nextjs`); the run reports that as done and writes nothing.
A Dockerfile without it gets the same line, marked, before the runtime stage's USER line. One whose
runtime stage has no single USER line is refused, and so is one whose marked line no longer sits in
the runtime stage before that USER line, or chowns to a user USER no longer names (--revert, then a
run, writes it where and for whom it belongs now). Neither fixes a
volume that already exists with another owner: on podman that takes a one-off chown (the rail's
README says how).

WHAT basePath DOES NOT REWRITE, AND WHAT COVERS IT. `basePath` only rewrites URLs Next itself
generates (next/link, next/image, the metadata API). Two kinds of hand-written root-relative
string are passed through untouched and resolve against the origin root:

  * asset literals in markup, `<img src="/logos/x.svg">`. OpenMAIC has ~124. They are handled by
    the rail declaring `root_assets` in its manifest (rails/openmaic/README.md and RC029).
  * API calls, `fetch('/api/server-providers')`. OpenMAIC has ~70, and the gateway owns /api/ at
    the root, so they cannot be root assets: every one of them 404ed at the gateway. The shim
    this writes as instrumentation-client.ts moves same-origin /api/ requests under the basePath
    before hydration. It covers fetch, XMLHttpRequest and EventSource. It does not cover an /api/
    URL the browser loads from markup (an <img src>, for example).

WHAT defaultLocale DRIVES. Upstream's is 'zh-CN'. lib/i18n/config.ts initialises
i18next with it as `lng` and as `fallbackLng`, so it is the language of the server render (the
prerendered HTML) and of the first client render. lib/hooks/use-i18n.tsx switches to the reader's
own locale (localStorage 'locale', else navigator.language) only in an effect after hydration, and
maps a browser language upstream does not ship to defaultLocale. So under 'zh-CN' every load
showed Chinese and a 'CN' toggle until the app hydrated. With 'en-US': English from the first
paint; a reader whose own locale is Chinese (or any other) sees English until hydration and then
their own; a key missing from a locale falls back to English rather than Chinese; and a browser
language upstream does not ship gets English. `--default-locale zh-CN` keeps upstream's language.

LINE ENDINGS ARE KEPT. Each file is read and written as bytes, and an anchor is matched in the
file's own line ending. The first version wrote through Path.write_text, which on Windows turned
every line of an LF file into CRLF, so the whole file showed as changed.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

MARK = "platform-basepath"
API_MARK = "platform-api-prefix"
LOCALE_MARK = "platform-locale"
SHIM_SOURCE = Path(__file__).resolve().with_name("platform-api-prefix.ts")
SHIM_TARGET = "instrumentation-client.ts"
# Every name Next would load as client instrumentation. If upstream ships one of these itself,
# writing ours beside it would leave two, so the run refuses instead.
CLIENT_INSTRUMENTATION = [f"{d}instrumentation-client.{ext}"
                          for d in ("", "src/") for ext in ("ts", "js", "tsx", "jsx", "mjs")]

# The image build this patch exists for. NEXT_PUBLIC_PI_CHAT_ENABLED=false keeps the classroom
# chat on upstream's legacy runtime, which v1.0.x used by default: v1.1.x turned its Pi runtime on
# by default, Pi needs tool calling, and this rail's shim (backend/openmaic_app/api/llm.py) does not
# forward `tools`. The README's build command must be this one, character for character.
# The tag is spelled in full: compose's `openmaic-app:latest` is docker.io/library/openmaic-app
# to Docker and to podman's Docker-compatible API (what compose talks to on a podman box), while
# `podman build -t openmaic-app:latest` would store it as localhost/openmaic-app:latest.
IMAGE = "docker.io/library/openmaic-app"
BUILD_COMMAND = (f"docker build -t {IMAGE}:latest"
                 " --build-arg NEXT_BASE_PATH=/openmaic/api/app"
                 " --build-arg NEXT_PUBLIC_PI_CHAT_ENABLED=false .")

NEXT_CONFIG_ANCHOR = "const nextConfig: NextConfig = {"
NEXT_CONFIG_PATCH = f"""const nextConfig: NextConfig = {{
  // {MARK}: served under a path prefix by the ai-platform gateway. Unset => no prefix, so
  // this tree still builds standalone. Only rewrites URLs Next generates; hand-written
  // absolute literals are handled by the rail's root_assets declaration.
  basePath: process.env.NEXT_BASE_PATH || undefined,
  assetPrefix: process.env.NEXT_BASE_PATH || undefined,"""

# Anchored on the LAST line of the edit above and on the line that opens upstream's own `env`
# object straight after it, so the edit above stays one contiguous block (and so revertable on its
# own), and the key lands in the object that is nextConfig's first property.
ENV_ANCHOR = """  assetPrefix: process.env.NEXT_BASE_PATH || undefined,
  env: {
"""
ENV_PATCH = f"""  assetPrefix: process.env.NEXT_BASE_PATH || undefined,
  env: {{
    // {API_MARK}: the same prefix, inlined into the client bundle for
    // {SHIM_TARGET}, which moves fetch('/api/...') calls under it. '' when unset.
    PLATFORM_BASE_PATH: process.env.NEXT_BASE_PATH || '',
"""

DOCKER_ANCHOR = "FROM base AS builder\n"
DOCKER_PATCH = f"""FROM base AS builder

# {MARK}: the prefix has to be present at BUILD time -- Next bakes basePath and assetPrefix into
# the emitted bundle, so setting it only at runtime produces an app that requests its own assets
# from the wrong origin and renders blank.
ARG NEXT_BASE_PATH
ENV NEXT_BASE_PATH=${{NEXT_BASE_PATH}}
"""

LOCALE_FILE = "lib/i18n/types.ts"
LOCALES_FILE = "lib/i18n/locales.ts"
UPSTREAM_LOCALE = "zh-CN"
DEFAULT_LOCALE = "en-US"
LOCALE_ANCHOR = f"export const defaultLocale: Locale = '{UPSTREAM_LOCALE}';\n"
# Stands in for the locale when the patched block is matched whatever locale it was applied with.
_SLOT = "@@LOCALE@@"


def locale_patch(locale: str) -> str:
    return f"""// {LOCALE_MARK}: the locale of the server render and of the first client render (i18next's
// lng, and its fallbackLng). The reader's own replaces it only after hydration
// (lib/hooks/use-i18n.tsx), so upstream's '{UPSTREAM_LOCALE}' showed Chinese until then on every load.
export const defaultLocale: Locale = '{locale}';
"""


DATA_MARK = "platform-data-dir"
DATA_DIR = "/app/data"


def data_dir_block(user: str) -> str:
    return f"""# {DATA_MARK}: the app writes classrooms, usage and materials to {DATA_DIR}, which compose
# mounts as a named volume. A fresh volume takes this directory's owner at its first mount, so it
# must belong to the runtime user (upstream v1.1.3 does this itself, THU-MAIC/OpenMAIC#1438).
RUN mkdir -p {DATA_DIR} && chown {user} {DATA_DIR}

"""


def _runtime_user(text: str) -> tuple[str, int, int]:
    """The runtime (last) stage's USER, the offset of that line, and the offset of the stage's
    FROM. Refused unless the stage has exactly one USER line."""
    stages = [m.start() for m in re.finditer(r"^FROM\s", text, re.M)]
    if not stages:
        raise Refused(f"ANCHOR MISSING in Dockerfile [{DATA_MARK}]: 'FROM'\n"
                      "  Upstream changed this file. Re-derive the patch rather than forcing it.")
    users = list(re.finditer(r"^USER[ \t]+(\S+)[ \t]*\r?$", text[stages[-1]:], re.M))
    if len(users) != 1:
        what = "MISSING" if not users else f"FOUND {len(users)} TIMES"
        raise Refused(f"ANCHOR {what} in Dockerfile [{DATA_MARK}]: a USER line in the runtime "
                      "stage\n  Upstream changed this file. Re-derive the patch rather than forcing it.")
    return users[0].group(1), stages[-1] + users[0].start(), stages[-1]


def _upstream_data_dir(stage: str, user: str) -> str | None:
    """The RUN instruction in `stage` (the runtime stage up to its USER line) that creates
    DATA_DIR and chowns it to `user`, continuation lines joined; None when there is none."""
    joined = re.sub(r"\\[ \t]*\r?\n", " ", stage)
    for line in joined.splitlines():
        if (re.match(r"\s*RUN\s", line)
                and re.search(rf"\bmkdir\s+(?:-p\s+)?{re.escape(DATA_DIR)}(?:\s|$|&|;)", line)
                and re.search(rf"\bchown\s+(?:-R\s+)?{re.escape(user)}(?::\S+)?\s+"
                              rf"{re.escape(DATA_DIR)}(?:\s|$|&|;)", line)):
            return " ".join(line.split())
    return None


def plan_data_dir(text: str, revert: bool) -> tuple[str, str]:
    """The Dockerfile with DATA_DIR owned by the runtime user before USER, and the report line.
    Upstream's own line counts as done: nothing is written for it.

    Our line counts as done only while it sits in the runtime stage before that stage's USER
    line, and only while the user it chowns to is still the one USER names. An upstream that
    reorders its stages (or moves USER up) would otherwise leave "already patched" over a line
    that runs in another stage or as the unprivileged user, and one that renames the user (or a
    hand edit of USER) over a directory owned by somebody the app no longer runs as; either way
    nothing the app saves is kept. The run refuses, and --revert followed by a run writes it
    afresh."""
    ours = re.compile(re.escape(_in(text, data_dir_block(_SLOT))).replace(re.escape(_SLOT), r"(\S+)"))
    m = ours.search(text)
    if revert:
        if DATA_MARK not in text:
            return text, f"  clean    Dockerfile [{DATA_MARK}] (nothing to revert)"
        if m is None:
            raise Refused(f"CANNOT REVERT Dockerfile [{DATA_MARK}]: the marker is there but the "
                          "patched text is not. Something edited it after it was applied; undo it "
                          "by hand.")
        return text[:m.start()] + text[m.end():], f"  revert   Dockerfile [{DATA_MARK}]"
    if DATA_MARK in text:
        if m is None:
            raise Refused(f"REFUSED  Dockerfile [{DATA_MARK}]: the marker is there but the patched "
                          "text is not. Something edited it after it was applied; undo it by hand.")
        user, at, stage_at = _runtime_user(text)
        if not stage_at <= m.start() < at:
            raise Refused(f"REFUSED  Dockerfile [{DATA_MARK}]: the patched line is not in the "
                          f"runtime stage before its USER {user} line. Upstream changed this file; "
                          "run --revert, then patch again.")
        if m.group(1) != user:
            raise Refused(f"REFUSED  Dockerfile [{DATA_MARK}]: the patched line chowns {DATA_DIR} "
                          f"to {m.group(1)!r}, but the runtime stage's USER is now {user!r}. "
                          "Upstream changed this file; run --revert, then patch again.")
        return text, f"  ok       Dockerfile [{DATA_MARK}] (already patched)"
    user, at, stage_at = _runtime_user(text)
    found = _upstream_data_dir(text[stage_at:at], user)
    if found:
        return text, f"  ok       Dockerfile [{DATA_MARK}] (upstream's own, before USER {user}: {found})"
    return text[:at] + _in(text, data_dir_block(user)) + text[at:], f"  patch    Dockerfile [{DATA_MARK}]"


def _one_env_key(text: str) -> str | None:
    # A second `env:` in the same object literal is not an error in JavaScript: the later key
    # silently wins. If upstream grows another one, ours could be the one discarded (the shim then
    # gets nothing and does nothing). A merge by hand is the honest answer.
    if len(re.findall(r"^\s*env\s*:", text, re.M)) > 1:
        return ("next.config.ts has more than one `env` key, and the later one silently wins; "
                "add PLATFORM_BASE_PATH by hand")
    return None


@dataclass(frozen=True)
class Edit:
    file: str
    marker: str
    anchor: str
    patched: str
    guard: Callable[[str], str | None] | None = None
    # For an edit that carries a value chosen at run time: `patched` with _SLOT where the value
    # goes, so the block is found whatever value it was applied with, and a re-run with another
    # value updates it in place.
    form: str | None = None
    value: str | None = None


def edits(locale: str = DEFAULT_LOCALE) -> list[Edit]:
    return [
        Edit("next.config.ts", MARK, NEXT_CONFIG_ANCHOR, NEXT_CONFIG_PATCH),
        Edit("Dockerfile", MARK, DOCKER_ANCHOR, DOCKER_PATCH),
        Edit("next.config.ts", API_MARK, ENV_ANCHOR, ENV_PATCH, _one_env_key),
        Edit(LOCALE_FILE, LOCALE_MARK, LOCALE_ANCHOR, locale_patch(locale),
             form=locale_patch(_SLOT), value=locale),
    ]


EDITS = edits()


class Refused(Exception):
    """The checkout is not what this script expects. Nothing has been written."""


def shim_text() -> str:
    """The shim as it is written into the checkout: LF, whatever this repo's checkout uses."""
    return SHIM_SOURCE.read_bytes().decode("utf-8").replace("\r\n", "\n")


def _eol(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _in(text: str, s: str) -> str:
    """`s` (written with LF) in the line ending `text` uses."""
    return s.replace("\n", _eol(text)) if _eol(text) == "\r\n" else s


def _form(text: str, form: str) -> re.Pattern[str]:
    """The patched block, in `text`'s line ending, with the value as group 1."""
    return re.compile(re.escape(_in(text, form)).replace(re.escape(_SLOT), "([A-Za-z]{2}-[A-Za-z]{2})"))


def supported_locales(root: Path) -> list[str]:
    p = root / LOCALES_FILE
    if not p.is_file():
        raise Refused(f"MISSING  {LOCALES_FILE} -- upstream layout has moved; this script needs a look")
    return re.findall(r"\bcode:\s*'([^']+)'", p.read_bytes().decode("utf-8"))


def plan(root: Path, revert: bool, locale: str = DEFAULT_LOCALE,
         ) -> tuple[dict[Path, bytes | None], list[str]]:
    """What a run would write, and one report line per edit. {path: new bytes, or None to
    delete}. Computed in full before anything is written, so a refusal leaves the tree as it was."""
    if not revert:
        known = supported_locales(root)
        if locale not in known:
            raise Refused(f"REFUSED  --default-locale {locale!r}: upstream ships "
                          f"{', '.join(known)} ({LOCALES_FILE})")
    texts: dict[Path, str] = {}
    original: dict[Path, str] = {}
    lines: list[str] = []
    todo = edits(locale)
    for e in todo:
        p = root / e.file
        if p not in texts:
            if not p.is_file():
                raise Refused(f"MISSING  {e.file} -- upstream layout has moved; this script needs a look")
            texts[p] = original[p] = p.read_bytes().decode("utf-8")

    def do(e: Edit) -> None:
        p = root / e.file
        text = texts[p]
        anchor, patched = _in(text, e.anchor), _in(text, e.patched)
        applied = e.marker in text
        # The block as it stands in the file: for a valued edit, whatever value it holds.
        block = patched
        held = e.value
        if applied and e.form is not None:
            m = _form(text, e.form).search(text)
            if m is not None:
                block, held = m.group(0), m.group(1)
        if revert:
            if not applied:
                lines.append(f"  clean    {e.file} [{e.marker}] (nothing to revert)")
            elif block not in text:
                raise Refused(f"CANNOT REVERT {e.file} [{e.marker}]: the marker is there but the "
                              "patched text is not. Something edited it after it was applied; "
                              "undo it by hand.")
            else:
                texts[p] = text.replace(block, anchor, 1)
                lines.append(f"  revert   {e.file} [{e.marker}]")
            return
        if applied:
            if e.form is not None and block not in text:
                raise Refused(f"REFUSED  {e.file} [{e.marker}]: the marker is there but the patched "
                              "text is not. Something edited it after it was applied; undo it by "
                              "hand.")
            if held != e.value:
                texts[p] = text.replace(block, patched, 1)
                lines.append(f"  update   {e.file} [{e.marker}] ({held} -> {e.value})")
                return
            # Idempotent by the MARKER, not by comparing against our own replacement text. A
            # marker survives upstream reformatting the surrounding lines; a text comparison
            # does not, and would report "already applied" as FAIL and re-apply the patch.
            lines.append(f"  ok       {e.file} [{e.marker}] (already patched)")
            return
        if text.count(anchor) != 1:
            what = "MISSING" if anchor not in text else f"FOUND {text.count(anchor)} TIMES"
            raise Refused(f"ANCHOR {what} in {e.file} [{e.marker}]: {e.anchor.strip()!r}\n"
                          "  Upstream changed this file. Re-derive the patch rather than forcing it.")
        problem = e.guard(text) if e.guard else None
        if problem:
            raise Refused(f"REFUSED  {e.file} [{e.marker}]: {problem}")
        texts[p] = text.replace(anchor, patched, 1)
        lines.append(f"  patch    {e.file} [{e.marker}]")

    def data_dir() -> None:
        p = root / "Dockerfile"
        texts[p], line = plan_data_dir(texts[p], revert)
        lines.append(line)

    # Reverted in the opposite order, so each edit is undone with the ones after it already gone.
    if revert:
        data_dir()
    for e in (reversed(todo) if revert else todo):
        do(e)
    if not revert:
        data_dir()

    writes: dict[Path, bytes | None] = {
        p: t.encode("utf-8") for p, t in texts.items() if t != original[p]}

    shim = root / SHIM_TARGET
    present = [n for n in CLIENT_INSTRUMENTATION if (root / n).is_file()]
    ours = shim.is_file() and API_MARK in shim.read_bytes().decode("utf-8")
    foreign = [n for n in present if not (n == SHIM_TARGET and ours)]
    if revert:
        if ours:
            writes[shim] = None
            lines.append(f"  delete   {SHIM_TARGET} [{API_MARK}]")
        else:
            lines.append(f"  clean    {SHIM_TARGET} [{API_MARK}] (not ours, or absent)")
    elif foreign:
        raise Refused(f"REFUSED  {', '.join(foreign)}: upstream now ships its own client "
                      f"instrumentation. Merge {SHIM_SOURCE.name} into it by hand.")
    else:
        want = shim_text()
        if ours and shim.read_bytes().decode("utf-8") == want:
            lines.append(f"  ok       {SHIM_TARGET} [{API_MARK}] (current)")
        else:
            # Ours but older: the whole file is ours, so it is replaced rather than merged.
            writes[shim] = want.encode("utf-8")
            lines.append(f"  {'update' if ours else 'write'}    {SHIM_TARGET} [{API_MARK}]")
    return writes, lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("checkout", type=Path, help="path to an OpenMAIC checkout")
    ap.add_argument("--check", action="store_true", help="report what would change, write nothing")
    ap.add_argument("--revert", action="store_true", help="undo the patch")
    ap.add_argument("--default-locale", default=DEFAULT_LOCALE, metavar="LOCALE",
                    help=f"the app's defaultLocale (default {DEFAULT_LOCALE}; {UPSTREAM_LOCALE} "
                         "keeps upstream's)")
    args = ap.parse_args(argv)

    root: Path = args.checkout.resolve()
    if not (root / "next.config.ts").is_file():
        print(f"not an OpenMAIC checkout (no next.config.ts): {root}")
        return 2
    try:
        writes, lines = plan(root, args.revert, args.default_locale)
    except Refused as exc:
        print(f"  {exc}")
        print("\nnothing written")
        return 2

    for line in lines:
        print(("  WOULD" + line[1:]) if args.check and not line.lstrip().startswith(("ok", "clean")) else line)
    if not args.check:
        for path, data in writes.items():
            if data is None:
                path.unlink()
            else:
                path.write_bytes(data)
    verb = "would change" if args.check else "changed"
    print(f"\n{len(writes)} file(s) {verb}")
    if writes and not args.check and not args.revert:
        print("\nNow build with the prefix baked in:")
        print(f"  {BUILD_COMMAND}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
