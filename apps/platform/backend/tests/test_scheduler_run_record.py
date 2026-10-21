"""What a scheduled run leaves in its record (Schedule.last_status, the Schedule tab's badge)
when the rail ANSWERS with a failure.

scheduler.fire used to let httpx's raise_for_status() text stand in for the rail's answer:
"Server error '500 Internal Server Error' for url 'http://...'". The rail's own body, its counts
("removed=3 failed=1") or its reason ("admin only"), was thrown away, so the one place an
operator looks said that something broke and nothing about what. These tests drive a REAL
httpx client over httpx.MockTransport, so raise_for_status, .json() and .text behave exactly as
they do against a live rail.

Run tests/mutate_scheduler_fire.py after changing scheduler.fire or its summarisers.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import select

from platform_gateway_app import scheduler
from platform_gateway_app.models import Schedule

BACKENDS = {"bouquet": "http://bq:8000", "recipe-book": "http://rb:8000"}


def _fire(handler, rail="recipe-book", task="purge") -> str:
    async def go() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await scheduler.fire(http, BACKENDS, rail, task)
    return asyncio.run(go())


def _answer(code, **kw):
    return lambda request: httpx.Response(code, **kw)


def test_a_failing_sync_task_keeps_the_counts_the_rail_returned():
    """The case that prompted this: a sweep that deleted some clips and could not delete one
    answers 500 with its counts. Those counts are the whole story, and they must survive."""
    status = _fire(_answer(500, json={"detail": {"retention_days": 30, "removed": 3,
                                                 "failed": 1}}))
    assert status.startswith("error (500)")
    assert "removed=3" in status and "failed=1" in status and "retention_days=30" in status


def test_a_string_detail_is_kept_with_its_status_code():
    assert _fire(_answer(403, json={"detail": "admin only"})) == "error (403) admin only"


def test_a_validation_error_keeps_its_messages():
    body = {"detail": [{"loc": ["body", "days"], "msg": "Input should be a valid integer",
                        "type": "int_parsing"}]}
    assert _fire(_answer(422, json=body)) == "error (422) Input should be a valid integer"


def test_a_body_that_is_not_json_is_kept_as_one_bounded_line():
    html = "<html>\n<body>\n  <h1>502 Bad Gateway</h1>\n" + ("x" * 2000) + "\n</body></html>"
    status = _fire(_answer(502, text=html))
    assert status.startswith("error (502) <html> <body> <h1>502 Bad Gateway</h1>")
    assert "\n" not in status
    assert len(status) == scheduler.STATUS_MAX


def test_the_record_never_outgrows_its_column():
    """last_status is String(255). The bound is read from the column, so widening one widens
    the other and they cannot disagree."""
    assert scheduler.STATUS_MAX == Schedule.__table__.c.last_status.type.length == 255
    many = {f"count_{i}": i for i in range(200)}
    assert len(_fire(_answer(500, json={"detail": many}))) == 255
    assert len(_fire(_answer(200, json=many))) == 255


def test_nothing_credential_shaped_reaches_the_record():
    """A rail's error body is recorded, so a secret in it must not be. Secret-NAMED keys are
    dropped; an Authorization or NAME=value credential in free text is masked."""
    detail = {"failed": 1, "token": "tok-marker", "api_key": "key-marker",
              "Password": "pw-marker", "session_cookie": "ck-marker",
              "note": "Authorization: Bearer brr-marker and BROKER_AUTH_TOKEN=env-marker"}
    status = _fire(_answer(500, json={"detail": detail}))
    for marker in ("tok-marker", "key-marker", "pw-marker", "ck-marker", "brr-marker",
                   "env-marker"):
        assert marker not in status, status
    assert "failed=1" in status and "Bearer [redacted]" in status
    text = _fire(_answer(500, text="upstream said: Basic dXNlcjpwYXNz-marker refused"))
    assert "dXNlcjpwYXNz-marker" not in text and "Basic [redacted]" in text
    # Ordinary prose that merely MENTIONS a credential word is not mangled.
    assert _fire(_answer(500, json={"detail": "token file unreadable"})) == \
        "error (500) token file unreadable"


def test_a_successful_summary_drops_secret_named_keys_too():
    status = _fire(_answer(200, json={"removed": 2, "token": "tok-marker"}))
    assert status == "ok (200) removed=2"


def test_an_empty_error_body_still_records_the_code():
    assert _fire(_answer(500, content=b"")) == "error (500)"


def test_no_answer_at_all_still_records_httpx_text():
    def refuse(request):
        raise httpx.ConnectError("All connection attempts failed", request=request)
    assert _fire(refuse) == "error: All connection attempts failed"


def test_the_detail_reaches_the_schedule_row_through_run_now(session, db):
    """The Schedule tab reads the row, not fire()'s return value. Run now is the manual path;
    tick() stores the same string the same way."""
    scheduler.seed(session, {"recipe-book"})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                _answer(500, json={"detail": {"purged": 0, "failed": 4}}))) as http:
            return await scheduler.run_now(session, http, BACKENDS, "recipe-book", "purge")

    out = asyncio.run(go())
    assert out["status"] == "error (500) purged=0 failed=4"
    with db.session_ctx() as s2:
        row = s2.execute(select(Schedule).where(Schedule.rail == "recipe-book",
                                                Schedule.task_id == "purge")).scalar_one()
        assert row.last_status == "error (500) purged=0 failed=4"


@pytest.mark.parametrize("code", [400, 404, 500, 503])
def test_every_failure_reads_as_an_error_on_the_tab(code):
    """AdminPage.tsx colours a badge good only when it starts with ok/triggered."""
    status = _fire(_answer(code, json={"detail": "nope"}))
    assert status.startswith("error") and not status.startswith(("ok", "triggered"))
