"""keep_alive normalization — the fix for Ollama 400s on string "-1".

Ollama accepts an int (seconds; -1 = forever, 0 = unload) or a Go duration
string ("5m"). A plain-integer string like "-1" is neither and 400s, so the
broker coerces those to ints while leaving real durations untouched.
"""

from app.ollama import _normalize_keep_alive


def test_integer_strings_become_ints():
    assert _normalize_keep_alive("-1") == -1
    assert _normalize_keep_alive("0") == 0
    assert _normalize_keep_alive("300") == 300
    assert _normalize_keep_alive("  -1 ") == -1


def test_duration_strings_pass_through():
    assert _normalize_keep_alive("5m") == "5m"
    assert _normalize_keep_alive("1h30m") == "1h30m"


def test_ints_and_none_pass_through():
    assert _normalize_keep_alive(-1) == -1
    assert _normalize_keep_alive(0) == 0
    assert _normalize_keep_alive(None) is None
