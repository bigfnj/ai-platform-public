"""deploy/.env.example must name every variable deploy/docker-compose.yml reads.

The example listed 10 keys while compose read 91. Nothing was undiscoverable, because every one
was documented SOMEWHERE in tracked files, but an operator following the example started with
every job-board key and the recipe-book token key unset and nothing telling them so. A list
maintained by hand next to a file it restates drifts the first time someone adds a knob to one
and not the other, so the restatement is checked here instead of trusted.

One direction only, deliberately: compose -> example. The example may document keys compose
never interpolates, and the PUBLIC snapshot ships a compose file with the withheld rails'
services stripped out, so "every example key is in compose" could be false in one tree while
this direction stays true in both.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "deploy" / "docker-compose.yml"
EXAMPLE = REPO / "deploy" / ".env.example"

# `$$` is compose's escape for a literal dollar and is NOT a variable. The alternation puts it
# first so `$${NOT_A_VAR}` is consumed as an escape rather than read as `$` + `${NOT_A_VAR}`.
_REF = re.compile(r"\$(?:(\$)|\{([A-Za-z_][A-Za-z0-9_]*)|([A-Za-z_][A-Za-z0-9_]*))")
# `KEY=` at the start of a line, optionally commented out (`# KEY=default` is how this file
# shows a compose default without pinning it). A key only MENTIONED in prose does not count:
# "documented" means an operator can see the line they would uncomment.
_ASSIGN = re.compile(r"^[ \t]*(#[ \t]*)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.M)
# Names that hold a credential. `_FILE` (a path to one) and `_ID` / `_RECIPIENT` (public halves)
# do not end in any of these, which is the point of matching the END of the name.
_SECRET = re.compile(r"(PASSWORD|SECRET|TOKEN|API_KEY|APP_KEY|TOKEN_KEY)$")


def _strip_comment(line: str) -> str:
    """The line with any YAML comment removed. A `#` opens a comment only outside quotes and only
    at the start of the line or after whitespace (`a#b` is a plain scalar). Compose never
    interpolates inside a comment, so a `${VAR}` written in one is prose, not a reference."""
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def compose_vars(text: str) -> set[str]:
    """Every variable name compose would interpolate, nested defaults included:
    `${A:-${B:-x}}` reads both A and B, and B is just as much a knob."""
    names: set[str] = set()
    for line in text.splitlines():
        for m in _REF.finditer(_strip_comment(line)):
            if m.group(1):          # `$$`, an escaped literal dollar
                continue
            names.add(m.group(2) or m.group(3))
    return names


def documented(text: str) -> set[str]:
    return {m.group(2) for m in _ASSIGN.finditer(text)}


# --- the parsers are what make this test mean anything, so they are tested first -------------

def test_compose_parser_reads_every_reference_form_and_nothing_else():
    sample = "\n".join([
        "services:",
        "  a:                       # ${IN_A_COMMENT} is prose",
        "    image: ${PLAIN}",
        "    environment:",
        "      X: ${WITH_DEFAULT:-1320}",
        "      Y: ${OUTER:-${INNER:-dev}}",
        "      Z: ${ONLY_IF_SET:+/data/x.db}",
        "      W: $BARE_FORM",
        "      V: \"$${ESCAPED} costs $$5\"",
        "      U: \"has # not-a-comment ${QUOTED_HASH}\"",
        "    # ${WHOLE_LINE_COMMENT}",
        "    volumes:",
        "      - \"${MOUNT:-vol}:/data\"   # trailing ${TRAILING_COMMENT}",
    ])
    assert compose_vars(sample) == {"PLAIN", "WITH_DEFAULT", "OUTER", "INNER", "ONLY_IF_SET",
                                    "BARE_FORM", "QUOTED_HASH", "MOUNT"}


def test_example_parser_counts_commented_keys_but_not_prose():
    sample = "\n".join([
        "# Prose that mentions MENTIONED_ONLY without assigning it.",
        "ACTIVE=",
        "  # COMMENTED_DEFAULT=1320",
        "#NO_SPACE=x",
        "# not a key: lower = spaced",
    ])
    assert documented(sample) == {"ACTIVE", "COMMENTED_DEFAULT", "NO_SPACE"}


def test_the_real_compose_parse_agrees_with_a_yaml_walk():
    """The line parser is an approximation of YAML. On the real file it must be exact, so a
    construct it mis-reads (a block scalar, a quoted `#`) shows up here rather than as a key
    that silently stops being required."""
    yaml = pytest.importorskip("yaml", reason="install pyyaml to cross-check the line parser")
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(k)
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            found.update(compose_vars(node))

    text = COMPOSE.read_text(encoding="utf-8")
    walk(yaml.safe_load(text))
    assert compose_vars(text) == found


# --- the real files ------------------------------------------------------------------------------

def test_compose_reads_variables_at_all():
    """A vacuity guard: a parser that silently found nothing would pass the test below."""
    assert len(compose_vars(COMPOSE.read_text(encoding="utf-8"))) >= 10


def test_every_compose_variable_is_documented_in_the_example():
    needed = compose_vars(COMPOSE.read_text(encoding="utf-8"))
    have = documented(EXAMPLE.read_text(encoding="utf-8"))
    missing = sorted(needed - have)
    assert not missing, (
        f"deploy/docker-compose.yml reads {len(missing)} variable(s) deploy/.env.example does not "
        f"name: {', '.join(missing)}. Add each as `KEY=` (operator sets it) or `# KEY=<compose "
        f"default>` (shown, not pinned), with a line on what it controls.")


def test_secrets_in_the_example_are_empty():
    """The example is tracked and published. A value on a credential line, commented out or not,
    is either a real key pasted in or a placeholder someone will deploy unchanged."""
    filled = [f"{m.group(2)}={m.group(3).strip()}"
              for m in _ASSIGN.finditer(EXAMPLE.read_text(encoding="utf-8"))
              if _SECRET.search(m.group(2)) and m.group(3).strip()]
    assert not filled, f"credential keys carry a value in deploy/.env.example: {filled}"
