"""Make an OpenMAIC checkout's base path env-driven, so it can be served under /openmaic/api/app/.

    python rails/openmaic/tools/platform-basepath.py --check   D:\\.ai-work\\projects\\OpenMAIC
    python rails/openmaic/tools/platform-basepath.py           D:\\.ai-work\\projects\\OpenMAIC
    python rails/openmaic/tools/platform-basepath.py --revert  D:\\.ai-work\\projects\\OpenMAIC

WHY THIS EXISTS. openmaic is the first rail here that WRAPS a third-party application. Upstream
THU-MAIC/OpenMAIC is a Next.js app that assumes it owns the origin; this platform serves every
rail under /<id>/. Next supports exactly that with `basePath`, but upstream does not set one --
so the app has to be told, at BUILD time, that it lives under a prefix.

WHY IT PATCHES RATHER THAN FORKS. The checkout stays pullable. Two small edits, both idempotent
and both reversible, beat a fork that has to be rebased against an app moving as fast as this one.

WHY IT IS ENV-DRIVEN RATHER THAN HARD-CODED. `NEXT_BASE_PATH` unset means no basePath at all, so
the same patched tree still builds and runs standalone. A hard-coded prefix would make the
checkout useless for anything but this platform, which is a rude thing to do to somebody's repo.

THE TRAP THIS DOES NOT CLOSE. `basePath` only rewrites URLs Next itself generates (next/link,
next/image, the metadata API). A hand-written `<img src="/logos/x.svg">` string literal in JSX is
passed through untouched and still resolves against the origin root. OpenMAIC has ~124 of those.
They are handled the other way, by the rail declaring `root_assets` in its manifest -- see
rails/openmaic/README.md and RC029.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

MARK = "platform-basepath"

NEXT_CONFIG_ANCHOR = "const nextConfig: NextConfig = {"
NEXT_CONFIG_PATCH = f"""const nextConfig: NextConfig = {{
  // {MARK}: served under a path prefix by the ai-platform gateway. Unset => no prefix, so
  // this tree still builds standalone. Only rewrites URLs Next generates; hand-written
  // absolute literals are handled by the rail's root_assets declaration.
  basePath: process.env.NEXT_BASE_PATH || undefined,
  assetPrefix: process.env.NEXT_BASE_PATH || undefined,"""

DOCKER_ANCHOR = "FROM base AS builder\n"
DOCKER_PATCH = f"""FROM base AS builder

# {MARK}: the prefix has to be present at BUILD time -- Next bakes basePath and assetPrefix into
# the emitted bundle, so setting it only at runtime produces an app that requests its own assets
# from the wrong origin and renders blank.
ARG NEXT_BASE_PATH
ENV NEXT_BASE_PATH=${{NEXT_BASE_PATH}}
"""


def _targets(root: Path) -> list[tuple[Path, str, str]]:
    """(file, anchor, patched) for each edit. Anchor absent => the file is not what we expect."""
    return [
        (root / "next.config.ts", NEXT_CONFIG_ANCHOR, NEXT_CONFIG_PATCH),
        (root / "Dockerfile", DOCKER_ANCHOR, DOCKER_PATCH),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("checkout", type=Path, help="path to an OpenMAIC checkout")
    ap.add_argument("--check", action="store_true", help="report what would change, write nothing")
    ap.add_argument("--revert", action="store_true", help="undo the patch")
    args = ap.parse_args()

    root: Path = args.checkout.resolve()
    if not (root / "next.config.ts").is_file():
        print(f"not an OpenMAIC checkout (no next.config.ts): {root}")
        return 2

    changed = 0
    for path, anchor, patched in _targets(root):
        if not path.is_file():
            print(f"  MISSING  {path.name} -- upstream layout has moved; this script needs a look")
            return 2
        text = path.read_text(encoding="utf-8")
        applied = MARK in text
        if args.revert:
            if not applied:
                print(f"  clean    {path.name} (nothing to revert)")
                continue
            if args.check:
                print(f"  WOULD REVERT  {path.name}")
            else:
                path.write_text(text.replace(patched, anchor), encoding="utf-8")
                print(f"  reverted {path.name}")
            changed += 1
            continue
        if applied:
            # Idempotent by the MARKER, not by comparing against our own replacement text. A
            # marker survives upstream reformatting the surrounding lines; a text comparison
            # does not, and would report "already applied" as FAIL and re-apply the patch.
            print(f"  ok       {path.name} (already patched)")
            continue
        if anchor not in text:
            print(f"  ANCHOR MISSING in {path.name}: {anchor.strip()!r}")
            print("  Upstream changed this file. Re-derive the patch rather than forcing it.")
            return 2
        if args.check:
            print(f"  WOULD PATCH   {path.name}")
        else:
            path.write_text(text.replace(anchor, patched, 1), encoding="utf-8")
            print(f"  patched  {path.name}")
        changed += 1

    verb = "would change" if args.check else "changed"
    print(f"\n{changed} file(s) {verb}")
    if changed and not args.check and not args.revert:
        print("\nNow build with the prefix baked in:")
        print("  docker build -t openmaic-app:latest "
              "--build-arg NEXT_BASE_PATH=/openmaic/api/app .")
    return 0


if __name__ == "__main__":
    sys.exit(main())
