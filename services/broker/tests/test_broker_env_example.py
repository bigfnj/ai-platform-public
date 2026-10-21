"""services/broker/.env.example tells the truth about BrokerSettings.

It named BROKER_HOST / BROKER_PORT ("internal only by default") when nothing read either, so it
promised a loopback bind the installed service does not have (0.0.0.0:11500), and it shipped
BROKER_DEFAULT_LOAD_KEEP_ALIVE=-1 against a code default of 30m, so copying the example did
the opposite of what the default is for. These pin both kinds of drift: every variable it names
is a setting the broker reads, and every value it states outright is the code's default.
"""
from __future__ import annotations

import re
from pathlib import Path

from pydantic import TypeAdapter

from app.config import BrokerSettings

EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"
# `NAME=value` at the start of a line, or `# NAME=` for a documented-but-unset one.
_LINE = re.compile(r"^(#\s*)?(BROKER_[A-Z0-9_]+)=(\S*)", re.MULTILINE)


def _entries() -> list[tuple[bool, str, str]]:
    text = EXAMPLE.read_text(encoding="utf-8")
    return [(bool(c), name, value) for c, name, value in _LINE.findall(text)]


def test_every_variable_the_example_names_is_a_setting():
    fields = {f"BROKER_{n.upper()}" for n in BrokerSettings.model_fields}
    named = {name for _c, name, _v in _entries()}
    assert named, "the parser found nothing; the check would pass on any file"
    unread = sorted(named - fields)
    assert not unread, f".env.example names settings the broker never reads: {unread}"


def test_every_value_stated_outright_is_the_code_default():
    """An uncommented line is a value someone copies. It must be what the code does anyway,
    so copying the example changes nothing until a line is edited on purpose."""
    stated = [(n, v) for commented, n, v in _entries() if not commented and v]
    assert stated, "no uncommented values found; the check would pass on any file"
    wrong = []
    for name, value in stated:
        field = BrokerSettings.model_fields[name[len("BROKER_"):].lower()]
        # Compared as the setting's own type, so "600" and 600.0 are the same value.
        parsed = TypeAdapter(field.annotation).validate_python(value)
        if parsed != field.default:
            wrong.append(f"{name}={value} but the code default is {field.default!r}")
    assert not wrong, "\n".join(wrong)
