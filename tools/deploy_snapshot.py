"""Snapshot what a running deployment serves, and diff two snapshots.

    python tools/deploy_snapshot.py --env <live>/deploy/.env --out before.json
    python tools/deploy_snapshot.py --env <live>/deploy/.env --out after.json --diff before.json

WHY THIS EXISTS
---------------
rail_smoke.py asserts CONTENT facts from inside the containers. This records the surfaces a user
and the platform itself touch from OUTSIDE, which is what a redeploy (new checkout, new images,
reinstalled services) can change without any content check noticing:

- every enabled rail's status route, THROUGH the gateway with a real admin session, so a rail
  whose proxy or identity wiring broke shows up even if its container is healthy
- the broker's /v1/roles view: pattern, resolved model, installed, upstream per role
- each running container's image name and image ID, so "rebuilt" is checked rather than assumed

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
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def read_env(path: Path) -> dict[str, str]:
    """KEY=value pairs, read the way compose's dotenv reads them. An `export ` prefix is dropped;
    a value in matching quotes is taken whole; an unquoted value loses a trailing ` # comment`.
    A naive split kept `pw  # note` as the password, and every rail then logged in as 401."""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if k.startswith("export "):
            k = k[len("export "):].strip()
        v = v.strip()
        m = re.match(r"""^(["'])(.*?)\1""", v)
        out[k] = m.group(2) if m else re.sub(r"\s+#.*$", "", v).strip()
    return out


# A status body is reduced to keys and scalars before it is written, and a key that names a
# credential is redacted whatever its value: "nothing secret is written" has to hold for a rail
# that one day reports a token, not only for the bodies that exist today.
_SECRET_KEY = re.compile(r"token|secret|passw|api[_-]?key|authorization|cookie|session", re.I)


def status_routes() -> dict[str, str]:
    routes = {}
    for man in sorted(REPO.glob("rails/*/rail.json")):
        m = json.loads(man.read_text(encoding="utf-8"))
        if m.get("id") and m.get("status_route"):
            routes[m["id"]] = m["status_route"]
    return routes


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

    enabled = [a.strip() for a in env.get("PLATFORM_ENABLED_APPS", "").split(",") if a.strip()]
    routes = status_routes()
    rails = {}
    for app in enabled:
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

    try:
        ps = subprocess.run(["podman", "ps", "--format", "{{.Names}}|{{.Image}}|{{.ImageID}}|{{.State}}"],
                            capture_output=True, text=True, timeout=60)
        if ps.returncode != 0:
            # A dead podman transport is not "no containers": recorded as zero, a diff listed
            # every container as removed.
            snap["containers"] = f"podman ps exit {ps.returncode}: {ps.stderr.strip()[:200]}"
        else:
            snap["containers"] = {n: {"image": i, "id": d, "state": s} for n, i, d, s in
                                  (ln.split("|") for ln in ps.stdout.splitlines() if ln.count("|") == 3)}
    except Exception as e:  # noqa: BLE001
        snap["containers"] = type(e).__name__
    return snap


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
    for app, r in rails.items():
        print(f"  {app:24} status {r.get('status')}  frontend {r.get('frontend')}")
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
