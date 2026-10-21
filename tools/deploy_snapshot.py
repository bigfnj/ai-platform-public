"""Snapshot what a running deployment serves, and diff two snapshots.

    python tools/deploy_snapshot.py --env <live>/deploy/.env --out before.json
    python tools/deploy_snapshot.py --env <live>/deploy/.env --out after.json --diff before.json

WHY THIS EXISTS
---------------
rail_smoke.py asserts CONTENT facts from inside the containers. This records the surfaces a user
and the platform itself touch from OUTSIDE, which is what a redeploy (new checkout, new images,
reinstalled services) can change without any content check noticing:

- the status route of every app the deployment serves (PLATFORM_ENABLED_APPS, unset meaning
  every rail, minus PLATFORM_DISABLED_APPS), THROUGH the gateway with a real admin session, so a
  rail whose proxy or identity wiring broke shows up even if its container is healthy; the
  session is logged out at the end and its revocation checked
- the broker's /v1/roles view: pattern, resolved model, installed, upstream per role
- each running container's image name and image ID, via whichever of podman or docker runs the
  gateway container, so "rebuilt" is checked rather than assumed

--diff prints what changed and exits 1 if anything did, so it can gate a deployment phase. Some
change is often EXPECTED (new roles on a newer broker, a new image ID after a rebuild); the point
is that every change is listed and has to be accounted for, not that none may happen.

Stdlib only, and credentials come from the env file you point it at, never from arguments.
Status bodies are reduced to their keys and short scalar values, and any key that names a
credential (token, secret, password, api key, cookie, session) is written as <redacted>. What
remains is still deployment detail (the admin username, corpus paths), so keep snapshots on the box.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class DotEnvError(ValueError):
    """A file compose refuses to load: an unterminated quote, an unclosed or empty ${}, or
    ${NAME?msg} with NAME unset. The message names the line, never the value."""


_KEY = re.compile(r"[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_.-]*)[ \t]*=[ \t]*")
_DQ_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "a": "\a", "b": "\b", "f": "\f", "v": "\v",
               "\\": "\\", '"': '"', "$": "$"}
_TEMPLATE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-+?])([\s\S]*))?\Z")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def read_env(path: Path, environ: "dict[str, str] | None" = None) -> dict[str, str]:
    """KEY=value pairs, read the way compose's dotenv reads them: the one Python reader (rail_smoke
    uses it too), a port of deploy/installer/lib-runtime.ps1's ConvertFrom-DotEnvText. Both are
    held to tools/tests/dotenv_compose_cases.json AND dotenv_compose_cases_extra.json, measured
    with `docker compose config` (v5.5.1); this one used to be held to the first file only and
    failed 22 of the extra cases (`$$`, `${X:-d}`, a tab before `#`, quotes across lines, so an
    unquoted `pa$$word` read as `pa$`). The rules, as lib-runtime's Read-DotEnvFile states them:

    - UTF-8, a BOM dropped, LF or CRLF. A line that is not `KEY=...` is skipped; `export ` before
      the key and blanks around the `=` are allowed. The LAST assignment of a key wins.
    - unquoted: ends at " #" (a SPACE then a hash: `a#b` and `a<TAB># b` are kept whole), loses
      its trailing whitespace, a backslash is literal.
    - 'single': literal up to the next ', across lines; the rest of that line is ignored.
    - "double": \\n \\r \\t \\a \\b \\f \\v \\\\ \\" \\$ are escapes and any other backslash is
      kept; across lines; the rest of that line is ignored.
    - unquoted and "double" values expand $NAME and ${NAME}, also with :- - :+ + :? ?, and $$ is
      one $. A name is looked up in the process environment first, then among the keys ABOVE it.
    - an unterminated quote, an unclosed or empty ${}, and ${NAME?msg} with NAME unset raise
      DotEnvError naming the line. Compose refuses the same file.
    """
    environ = os.environ if environ is None else environ
    # Bytes, decoded: read_text() would turn a CRLF inside a quoted value into LF, and compose keeps it.
    text = path.read_bytes().decode("utf-8")
    if text.startswith("﻿"):
        text = text[1:]
    source = str(path)
    out: dict[str, str] = {}
    n, i, line = len(text), 0, 1
    while i < n:
        eol = text.find("\n", i)
        eol = n if eol < 0 else eol
        m = _KEY.match(text, i, eol)
        if not m:
            i, line = eol + 1, line + 1
            continue
        key, p = m.group(1), m.end()
        q = text[p] if p < eol else ""
        if q in ("'", '"'):
            close, j = -1, p + 1
            while j < n:
                if q == '"' and text[j] == "\\":
                    j += 2
                    continue
                if text[j] == q:
                    close = j
                    break
                j += 1
            if close < 0:
                raise DotEnvError(f"{source}: line {line}: unterminated quoted value for {key}")
            inner = text[p + 1:close]
            value = _expand(inner, out, environ, True, line, source) if q == '"' else inner
            line += inner.count("\n")
            eol = text.find("\n", close)
            eol = n if eol < 0 else eol
        else:
            raw = text[p:eol]
            h = raw.find(" #")
            value = _expand((raw[:h] if h >= 0 else raw).rstrip(), out, environ, False, line, source)
        out[key] = value
        i, line = eol + 1, line + 1
    return out


def _expand(raw: str, keys: dict, environ, double: bool, line: int, source: str) -> str:
    """The $-expansion and the double-quote escapes of one value (read_env has the rules)."""
    if "$" not in raw and not (double and "\\" in raw):
        return raw

    def lookup(name: str) -> tuple[bool, str]:
        if environ.get(name) is not None:
            return True, environ[name]
        if name in keys:
            return True, keys[name]
        return False, ""

    out: list[str] = []
    n, i = len(raw), 0
    while i < n:
        c = raw[i]
        if double and c == "\\" and i + 1 < n:
            e = raw[i + 1]
            out.append(_DQ_ESCAPES.get(e, c + e))
            i += 2
            continue
        if c == "$" and i + 1 < n:
            d = raw[i + 1]
            if d == "$":
                out.append("$")
                i += 2
                continue
            if d == "{":
                depth, j = 1, i + 2
                while j < n:
                    if raw[j] == "$" and j + 1 < n and raw[j + 1] == "{":
                        depth, j = depth + 1, j + 2
                        continue
                    if raw[j] == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                if depth:
                    raise DotEnvError(f'{source}: line {line}: invalid template (unclosed ${{)')
                bm = _TEMPLATE.match(raw[i + 2:j])
                if not bm:
                    raise DotEnvError(f"{source}: line {line}: invalid template")
                name, op, word = bm.group(1), bm.group(2) or "", bm.group(3) or ""
                is_set, val = lookup(name)
                empty = not is_set or val == ""
                if (op == ":?" and empty) or (op == "?" and not is_set):
                    raise DotEnvError(f"{source}: line {line}: required variable {name} is missing a value")
                use_word = {":-": empty, "-": not is_set, ":+": not empty, "+": is_set}.get(op, False)
                if use_word:
                    out.append(_expand(word, keys, environ, double, line, source))
                elif op not in (":+", "+"):
                    out.append(val)
                i = j + 1
                continue
            nm = _NAME.match(raw, i + 1)
            if nm:
                out.append(lookup(nm.group())[1])
                i = nm.end()
                continue
        out.append(c)
        i += 1
    return "".join(out)


# A status body is reduced to keys and scalars before it is written, and a key that names a
# credential is redacted whatever its value: "nothing secret is written" has to hold for a rail
# that one day reports a token, not only for the bodies that exist today.
_SECRET_KEY = re.compile(r"token|secret|passw|api[_-]?key|authorization|cookie|session", re.I)


def status_routes() -> dict[str, str]:
    """{app id: status route} for every catalog tile a manifest backs, `also_serves` included.

    An `also_serves` tile is the SAME image as its owning rail, run as a second instance, so it
    answers the owner's status route under its own prefix. It has no manifest of its own, and
    reading only manifest ids recorded it as "no status_route": that tile's status was never
    compared on any box that deploys it. A tile that does have its own manifest keeps its own.
    """
    routes: dict[str, str] = {}
    shared: dict[str, str] = {}
    for man in sorted(REPO.glob("rails/*/rail.json")):
        m = json.loads(man.read_text(encoding="utf-8"))
        if m.get("id") and m.get("status_route"):
            routes[m["id"]] = m["status_route"]
            for tile in m.get("also_serves") or []:
                shared.setdefault(tile, m["status_route"])
    return {**shared, **routes}


# --- which apps this deployment serves ---------------------------------------------------------
# This read PLATFORM_ENABLED_APPS alone. A box that leaves it unset, which means EVERY rail, was
# snapshotted with zero rails and the run still exited 0, so the rail half of the record checked
# nothing on exactly the box it was being used on. It now applies the gateway's own rule, from
# the env file it was pointed at: PLATFORM_ENABLED_APPS (unset or blank = every app) minus
# PLATFORM_DISABLED_APPS, the same rule tools/rail_smoke.py's box scope applies.

def parse_apps(raw: str) -> tuple[str, ...]:
    """An app list in either form the gateway accepts: a JSON array, or comma/newline-separated.

    A copy of GatewaySettings' parser, because this tool is stdlib-only; a parity test pins the
    two. Blank comes back as (); callers treat a blank ENABLED list as "not set".
    """
    s = (raw or "").strip()
    if not s:
        return ()
    if s.startswith("["):
        return tuple(json.loads(s))
    return tuple(p for p in (part.strip() for part in s.replace("\n", ",").split(",")) if p)


def every_app() -> list[str]:
    """Every app id this tree's manifests back, also_serves tiles included: the gateway's
    default enabled list (a test pins the two)."""
    apps: set[str] = set()
    for man in sorted(REPO.glob("rails/*/rail.json")):
        m = json.loads(man.read_text(encoding="utf-8"))
        if m.get("id"):
            apps.add(m["id"])
            apps.update(m.get("also_serves") or [])
    return sorted(apps)


def served_apps(env: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    """(apps the gateway serves, {app: why it is not snapshotted}) for this env file."""
    known = every_app()
    raw = env.get("PLATFORM_ENABLED_APPS", "").strip()
    enabled = list(dict.fromkeys(parse_apps(raw))) if raw else known
    disabled = set(parse_apps(env.get("PLATFORM_DISABLED_APPS", "")))
    skipped = {a: "disabled on this box" for a in enabled if a in disabled}
    skipped.update({a: "not enabled on this box" for a in known if a not in enabled})
    return [a for a in enabled if a not in disabled], dict(sorted(skipped.items()))


# --- which container CLI this deployment runs under --------------------------------------------
# This shelled out to `podman` unconditionally. On the Docker box that recorded an exception
# string on both sides of every diff, so `--diff` could never report a container change. Chosen
# the way deploy/installer/lib-runtime.ps1's Resolve-PlatformRuntime chooses: the CLI that lists
# the gateway container RUNNING, else the one that lists it at all, else the first one installed.
GATEWAY_CONTAINER = "platform-gateway-1"


def container_cli(container: str = GATEWAY_CONTAINER) -> tuple[str | None, str]:
    """(cli or None, why it was chosen)."""
    seen: list[tuple[str, bool, str | None]] = []   # (cli, installed, the container's state)
    for cli in ("podman", "docker"):
        if not shutil.which(cli):
            seen.append((cli, False, None))
            continue
        state = None
        try:
            ps = subprocess.run([cli, "ps", "-a", "--format", "{{.Names}} {{.State}}"],
                                capture_output=True, text=True, timeout=15)
            if ps.returncode == 0:
                for row in ps.stdout.splitlines():
                    f = row.split()
                    if f and f[0] == container:
                        state = f[1] if len(f) > 1 else "listed"
        except (OSError, subprocess.TimeoutExpired):
            pass                                      # installed but not answering: no state
        seen.append((cli, True, state))
    for wanted, why in ((lambda s: s == "running", "lists {c} running"),
                        (lambda s: bool(s), "lists {c}, not running")):
        hit = [cli for cli, _inst, state in seen if wanted(state)]
        if hit:
            return hit[0], f"'{hit[0]} ps' " + why.format(c=container)
    installed = [cli for cli, inst, _state in seen if inst]
    if installed:
        return installed[0], f"no CLI lists {container}, so the first one installed"
    return None, "neither podman nor docker is on PATH"


def containers(cli: str | None) -> dict | str:
    """{name: {image, id, state}} for every running container, via `ps` then `inspect` (podman
    and docker both answer `inspect` in JSON; their `ps` templates differ). A failure is a
    string, never an empty dict: a dead transport recorded as zero containers made a diff list
    every container as removed."""
    if cli is None:
        return "no container CLI: neither podman nor docker is on PATH"
    try:
        ps = subprocess.run([cli, "ps", "--format", "{{.Names}}"],
                            capture_output=True, text=True, timeout=60)
        if ps.returncode != 0:
            return f"{cli} ps exit {ps.returncode}: {ps.stderr.strip()[:200]}"
        names = sorted({n.strip() for n in ps.stdout.splitlines() if n.strip()})
        if not names:
            return {}
        ins = subprocess.run([cli, "inspect", *names], capture_output=True, text=True, timeout=60)
        try:
            rows = json.loads(ins.stdout or "[]")
        except ValueError:
            return f"{cli} inspect exit {ins.returncode}: {ins.stderr.strip()[:200]}"
        out = {}
        for d in rows if isinstance(rows, list) else []:
            cfg = d.get("Config") or {}
            out[str(d.get("Name") or "").lstrip("/")] = {
                "image": d.get("ImageName") or cfg.get("Image"),
                "id": d.get("Image"),
                "state": (d.get("State") or {}).get("Status")}
        return out
    except Exception as e:  # noqa: BLE001 - a snapshot records failure, it does not raise
        return type(e).__name__


def scalars(obj, depth: int = 0):
    """Keep the shape and the scalar facts of a status body; drop long strings and secrets."""
    if isinstance(obj, dict):
        if depth >= 3:
            return "{...}"
        return {k: ("<redacted>" if _SECRET_KEY.search(str(k)) else scalars(v, depth + 1))
                for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return f"[{len(obj)} items]"
    if isinstance(obj, str) and len(obj) > 120:
        return f"<str {len(obj)}>"
    return obj


def snapshot(env_file: Path, gateway: str, broker: str) -> dict:
    env = read_env(env_file)
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def get(url: str, timeout: float = 30, headers: dict | None = None) -> tuple[int, object]:
        try:
            with opener.open(urllib.request.Request(url, headers=headers or {}), timeout=timeout) as r:
                body = r.read()
                try:
                    return r.status, json.loads(body)
                except ValueError:
                    return r.status, f"<{len(body)} bytes>"
        except urllib.error.HTTPError as e:
            return e.code, None
        except Exception as e:  # noqa: BLE001 - a snapshot records failure, it does not raise
            return -1, type(e).__name__

    snap: dict = {}
    code, health = get(f"{gateway}/api/platform/healthz")
    snap["gateway_healthz"] = {"status": code, "apps": (health or {}).get("apps")
                               if isinstance(health, dict) else None}

    login = urllib.request.Request(
        f"{gateway}/api/platform/login", method="POST",
        data=json.dumps({"username": env.get("PLATFORM_ADMIN_USER", ""),
                         "password": env.get("PLATFORM_ADMIN_PASSWORD", "")}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with opener.open(login, timeout=30) as r:
            snap["login"] = r.status
    except urllib.error.HTTPError as e:
        snap["login"] = e.code
    except Exception as e:  # noqa: BLE001 - a gateway that is down is a recorded fact, not a crash
        snap["login"] = type(e).__name__

    served, skipped = served_apps(env)
    snap["rails_skipped"] = skipped
    routes = status_routes()
    rails = {}
    for app in served:
        route = routes.get(app)
        if route:
            code, body = get(f"{gateway}/{app}{route}")
            rails[app] = {"route": route, "status": code, "body": scalars(body)}
        else:
            rails[app] = {"status": None, "note": "no status_route in its manifest"}
        # The federation bundle itself. `/<app>/` answered 200 even with the bundle missing: the
        # gateway skips a rail whose dist has no remoteEntry.js, and the SPA catch-all then
        # serves the shell's index.html for that path.
        rails[app]["frontend"] = get(f"{gateway}/{app}/assets/remoteEntry.js")[0]
    snap["rails"] = rails

    # The broker's control plane needs the token on a box that sets one; without it /v1/roles was
    # recorded as 401 on both sides of a diff, and the roles surface was never compared at all.
    token = env.get("BROKER_AUTH_TOKEN", "")
    code, roles = get(f"{broker}/v1/roles", timeout=90,
                      headers={"Authorization": f"Bearer {token}"} if token else None)
    snap["roles"] = {r["role"]: {k: r.get(k) for k in ("pattern", "resolved", "installed", "upstream")}
                     for r in (roles or {}).get("roles", [])} if isinstance(roles, dict) else code

    cli, _why = container_cli()
    snap["container_cli"] = cli
    snap["containers"] = containers(cli)
    snap["logout"] = end_session(jar, gateway)
    return snap


def end_session(jar: http.cookiejar.CookieJar, gateway: str) -> dict:
    """Log the snapshot's admin session out, and prove it ended.

    Every run used to leave a live 7-day admin session behind. Logging out is not enough to say
    so: /api/platform/logout answers 200 whether or not it deleted anything. So the session
    cookie is replayed against /api/platform/me before (must be 200, the positive control: a
    malformed replay is also a 401) and after (must be 401). `revoked` is True only when both hold.
    """
    # The cookie is sent by hand, not through the jar: a jar withholds a Secure cookie over plain
    # http, and then logout would delete nothing while still answering 200.
    cookie = "; ".join(f"{c.name}={c.value}" for c in jar)
    if not cookie:
        return {"status": None, "revoked": None, "note": "no session was opened"}
    plain = urllib.request.build_opener()

    def call(path: str, method: str = "GET") -> int | str:
        req = urllib.request.Request(f"{gateway}{path}", headers={"Cookie": cookie},
                                     data=b"" if method == "POST" else None, method=method)
        try:
            with plain.open(req, timeout=30) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception as e:  # noqa: BLE001 - an unreachable gateway proves nothing either way
            return type(e).__name__

    before = call("/api/platform/me")
    status = call("/api/platform/logout", "POST")
    after = call("/api/platform/me")
    return {"status": status, "revoked": before == 200 and after == 401}


def diff(a, b, path: str = "") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b), key=str):
            p = f"{path}.{k}" if path else str(k)
            if k not in a:
                out.append(f"+ {p} = {json.dumps(b[k])[:200]}")
            elif k not in b:
                out.append(f"- {p} (was {json.dumps(a[k])[:200]})")
            else:
                out.extend(diff(a[k], b[k], p))
        return out
    return [] if a == b else [f"~ {path}: {json.dumps(a)[:200]} -> {json.dumps(b)[:200]}"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--env", required=True, type=Path, help="the deployment's deploy/.env")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--diff", type=Path, help="an earlier snapshot to compare against")
    ap.add_argument("--gateway", default="http://localhost:1111")
    ap.add_argument("--broker", default="http://127.0.0.1:11500")
    a = ap.parse_args()
    # Read the baseline BEFORE writing: with --out and --diff naming the same file, writing first
    # overwrote the baseline and the diff then compared the new snapshot with itself (0 changes).
    if a.diff and a.diff.resolve() == a.out.resolve():
        ap.error("--out and --diff name the same file; that would overwrite the baseline")
    before = json.loads(a.diff.read_text(encoding="utf-8")) if a.diff else None

    snap = snapshot(a.env, a.gateway.rstrip("/"), a.broker.rstrip("/"))
    a.out.write_text(json.dumps(snap, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rails = snap.get("rails", {})
    print(f"gateway {snap['gateway_healthz']['status']}, login {snap.get('login')}, "
          f"{sum(1 for r in rails.values() if r.get('status') == 200)}/{len(rails)} rails 200, "
          f"{len(snap['roles']) if isinstance(snap['roles'], dict) else 'no'} roles, "
          f"{len(snap['containers']) if isinstance(snap['containers'], dict) else 'no'} containers")
    print(f"container CLI: {snap.get('container_cli')}; "
          f"{len(rails)} app(s) snapshotted, {len(snap.get('rails_skipped') or {})} skipped")
    for app, r in rails.items():
        print(f"  {app:24} status {r.get('status')}  frontend {r.get('frontend')}")
    for app, why in (snap.get("rails_skipped") or {}).items():
        print(f"  {app:24} SKIP ({why})")
    out = snap.get("logout") or {}
    if out.get("revoked") is not True:
        print(f"  WARN  the snapshot's admin session was not shown revoked: {out}")
    print(f"written to {a.out}")
    if before is None:
        return 0
    changes = diff(before, snap)
    print(f"\n{len(changes)} change(s) vs {a.diff}:")
    for c in changes:
        print("  " + c)
    return 1 if changes else 0


if __name__ == "__main__":
    sys.exit(main())
