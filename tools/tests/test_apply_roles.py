"""The apply engine's role map keeps the box's generic roles, reads roles.json as the broker does,
and builds the gateway image instead of pulling it (records of 2026-09-11, never closed).

- `apply.ps1 -Execute` wrote services/broker/roles.json from the plan ALONE, which holds only the
  per-rail slots it sized, so the nine generic roles the lean map pins to gemma3:4b fell back to
  their 24 GB defaults on the card the plan had just downshifted.
- The roles step's Test compared with -eq, which ignores case; the broker's re.fullmatch does not,
  so a hand-edited "Recipe" key (dropped by the broker) read as "already done".
- Get-RoleDefaults took every key (annotations became roles) and unwrapped a nested map the broker
  cannot read.
Driven for real under both engines against a scratch copy of the lean role map.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "deploy" / "installer"
ROLE = re.compile(r"[a-z0-9][a-z0-9-]*")


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


@pytest.fixture(scope="module")
def roles_step(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("roles")
    root = base / "repo"
    (root / "services" / "broker").mkdir(parents=True)
    (root / "deploy").mkdir()
    # The tracked file holds the lean map, as an install from before deploy\local left it; the step
    # writes the box's own deploy\local\roles.json over it (test_lean_broker_files.py has the rest).
    rp = root / "services" / "broker" / "roles.json"
    shutil.copy2(INSTALLER / "roles.lean.json", rp)
    bp = root / "deploy" / "local" / "roles.json"
    lean = json.loads(rp.read_text(encoding="utf-8"))
    odd = base / "odd.json"
    odd.write_text(json.dumps({"_comment": "x", "Recipe": "a:1b", "chat": "gemma3:4b", "n": 3}), encoding="utf-8")
    nested = base / "nested.json"
    nested.write_text(json.dumps({"roles": {"chat": "gemma3:4b"}}), encoding="utf-8")
    body = f"""
$ErrorActionPreference = 'Stop'
& {{
  . {q(INSTALLER / 'lib-modelplan.ps1')}
  . {q(INSTALLER / 'lib-apply.ps1')}
  $plan = Get-ModelPlan -Rails @('terminal-fun', 'recipe-book') -VramMib 8192 -Installed @{{}}
  $planRoles = Get-PlanRoles -Plan $plan
  $acts = @(Get-ApplyActions -Plan $plan -Rails @('terminal-fun', 'recipe-book') -AdminUser 'u' -AdminPassword 'p' -Mode podman -Root {q(root)})
  $step = $acts | Where-Object Id -eq 'roles'
  & $step.Do
  $after = [bool](& $step.Test)
  $text = [IO.File]::ReadAllText({q(bp)})
  [IO.File]::WriteAllText({q(bp)}, ($text -creplace '"recipe"', '"Recipe"'))
  $retitled = [bool](& $step.Test)
  $RolesJsonPath = {q(odd)}
  $odd = Get-RoleDefaults
  $RolesJsonPath = {q(nested)}
  $nested = Get-RoleDefaults
  @{{ plan = $planRoles; written = ($text | ConvertFrom-Json); after = $after; retitled = $retitled
     odd = $odd; nested = $nested }} | ConvertTo-Json -Depth 4 -Compress
}}
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    got["lean"] = lean
    return got


def test_the_written_map_keeps_every_role_the_box_had(roles_step):
    lean_roles = {k: v for k, v in roles_step["lean"].items() if ROLE.fullmatch(k) and isinstance(v, str)}
    written = roles_step["written"]
    missing = sorted(set(lean_roles) - set(written))
    assert missing == [], missing                          # chat, chat-fast, vision... all still there
    assert len(lean_roles) > len(roles_step["plan"]), "the lean map must hold roles the plan does not"


def test_the_plans_choices_win_where_it_made_one(roles_step):
    for role, model in roles_step["plan"].items():
        assert roles_step["written"][role] == model, role


def test_the_roles_check_is_case_sensitive_like_the_broker(roles_step):
    assert roles_step["after"] is True
    assert roles_step["retitled"] is False                  # "Recipe" is dropped by the broker


def test_role_defaults_are_read_as_the_broker_reads_them(roles_step):
    assert roles_step["odd"] == {"chat": "gemma3:4b"}       # no _comment, no Recipe, no number
    assert roles_step["nested"] == {}                       # no unwrapping, and no phantom "roles"
