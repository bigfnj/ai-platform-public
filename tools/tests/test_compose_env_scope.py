"""What compose hands a container's environment: no service gets deploy/.env whole, and job-aid
gets exactly the keys its own source reads.

job-aid used to load the whole of deploy/.env through `env_file`, so the platform admin
password, the broker token and every other rail's secrets sat in its environment, readable by
anything that could read /proc/<pid>/environ there. env_file had been chosen for a real reason:
job-board keys kept vanishing when the explicit `environment:` block was hand-edited. Both
failures are checked here, so neither fix has to be remembered:

  * a key the rail READS that compose does not pass  -> the vanishing-key failure
  * a key compose passes that the rail does not read -> the over-sharing failure
  * any service loading .env whole                   -> the original leak, anywhere

"What the rail reads" is derived from its source, not restated by hand, so a new read fails
here until someone either wires it or adds it to EXCLUDED with a reason.

The public snapshot drops job-aid (a withheld rail) from compose and from the tree, so the
job-aid tests SKIP there with that reason; the any-service rule still runs.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "deploy" / "docker-compose.yml"
JOB_AID_SRC = REPO / "rails" / "job-aid" / "src"
SERVICE = "job-aid"

# Shared with the .env.example test, loaded by path so neither depends on sys.path order.
_spec = importlib.util.spec_from_file_location(
    "_env_example_parsers", Path(__file__).with_name("test_env_example.py"))
_parsers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_parsers)
compose_vars = _parsers.compose_vars
_SECRET = _parsers._SECRET

# Read by job-aid's source and deliberately NOT passed by compose. Every entry needs a reason a
# reviewer can check; an entry the source no longer reads fails test_every_exclusion_is_still_read.
EXCLUDED = {
    "PLATFORM_STANDALONE":
        "turns the gateway identity gate off (local dev without a gateway); the deployed "
        "container must never be able to pick it up from deploy/.env",
    "OLLAMA_BASE_URL":
        "read only when the LLM provider is `ollama`; compose pins JOB_AID_LLM_PROVIDER=broker",
    "ANTHROPIC_API_KEY":
        "read only when the LLM provider is `anthropic`; pinned to broker, so passing it would "
        "hand the container a secret it can never use",
    "JOB_AID_SECRET":
        "read only by the Flask dashboard (`job-aid web`); the container runs the FastAPI app",
    "BROKER_AUTH_TOKEN_FILE":
        "compose passes the token-file form to no service yet; a host path in deploy/.env would "
        "be unreadable inside the container and silently drop the token",
}

# os.environ.get("X"...), os.getenv('X'...), os.environ["X"], environ.setdefault/pop("X"...).
# A variable as the key (`os.environ[key] = value` in a .env loader) is not a named read.
_READ = re.compile(
    r"""(?:getenv|environ\.get|environ\.setdefault|environ\.pop)\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']"""
    r"""|environ\[\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*\]""")


def reads_in(text: str) -> set[str]:
    return {a or b for a, b in _READ.findall(text)}


def source_reads(root: Path) -> set[str]:
    names: set[str] = set()
    for p in sorted(root.rglob("*.py")):
        names |= reads_in(p.read_text(encoding="utf-8"))
    return names


def _compose() -> dict:
    yaml = pytest.importorskip("yaml", reason="install pyyaml; compose structure needs a YAML parser")
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _environment(svc: dict) -> dict[str, str]:
    """A service's environment as {KEY: raw value}, from either compose form."""
    env = svc.get("environment") or {}
    if isinstance(env, list):
        out = {}
        for item in env:
            key, _, value = str(item).partition("=")
            out[key.strip()] = value
        return out
    return {str(k): "" if v is None else str(v) for k, v in env.items()}


def _env_files(svc: dict) -> list[str]:
    ef = svc.get("env_file")
    if ef is None:
        return []
    items = ef if isinstance(ef, list) else [ef]
    return [str(i.get("path", "")) if isinstance(i, dict) else str(i) for i in items]


def _job_aid() -> dict:
    svc = (_compose().get("services") or {}).get(SERVICE)
    if svc is None or not JOB_AID_SRC.is_dir():
        pytest.skip("job-aid is not in this tree (withheld from the public snapshot)")
    return svc


# --- the scanner is what makes the job-aid tests mean anything ----------------------------------

def test_the_read_scanner_finds_every_form_and_nothing_else():
    sample = "\n".join([
        'a = os.environ.get("GET_FORM", "")',
        "b = os.getenv('GETENV_FORM')",
        'c = os.environ["INDEX_FORM"]',
        'd = os.environ.setdefault("SETDEFAULT_FORM", "x")',
        'e = environ.get( "SPACED_FORM" )',
        '"""Reads DOCSTRING_ONLY from the environment."""',
        "os.environ[key] = value",
    ])
    assert reads_in(sample) == {"GET_FORM", "GETENV_FORM", "INDEX_FORM", "SETDEFAULT_FORM",
                                "SPACED_FORM"}


# --- every service -------------------------------------------------------------------------------

def test_no_service_loads_the_shared_env_file():
    loaders = {name: files for name, svc in (_compose().get("services") or {}).items()
               if (files := [f for f in _env_files(svc or {}) if Path(f).name == ".env"])}
    assert not loaders, (
        f"service(s) load deploy/.env whole, handing every secret in it to the container: "
        f"{loaders}. List the keys the service reads under `environment:` instead.")


# --- job-aid -------------------------------------------------------------------------------------

def test_job_aid_receives_exactly_the_keys_it_reads():
    received = set(_environment(_job_aid()))
    wanted = source_reads(JOB_AID_SRC) - set(EXCLUDED)
    missing, extra = sorted(wanted - received), sorted(received - wanted)
    assert not missing and not extra, (
        f"job-aid's compose environment does not match what its source reads. "
        f"Read but not passed: {missing or 'none'}. Passed but not read: {extra or 'none'}. "
        f"Pass a key with its in-code default, or add it to EXCLUDED with a reason.")


def test_job_aid_receives_no_secret_it_does_not_read():
    """Sharper than the exact match, and checks the VALUE too: a key the rail reads must not
    carry another component's secret, e.g. `JOOBLE_API_KEY: ${PLATFORM_ADMIN_PASSWORD}`."""
    reads = source_reads(JOB_AID_SRC) - set(EXCLUDED)
    leaked = []
    for key, value in _environment(_job_aid()).items():
        for name in {key} | compose_vars(value):
            if _SECRET.search(name) and name not in reads:
                leaked.append(f"{key} <- {name}" if name != key else key)
    assert not leaked, f"job-aid receives secret(s) its source does not read: {sorted(leaked)}"


def test_job_aid_cannot_be_given_an_excluded_key():
    passed = sorted(set(_environment(_job_aid())) & set(EXCLUDED))
    assert not passed, f"job-aid is passed key(s) excluded on purpose: {passed}"


def test_every_exclusion_is_still_read():
    _job_aid()
    stale = sorted(set(EXCLUDED) - source_reads(JOB_AID_SRC))
    assert not stale, f"EXCLUDED names key(s) job-aid's source no longer reads: {stale}"
