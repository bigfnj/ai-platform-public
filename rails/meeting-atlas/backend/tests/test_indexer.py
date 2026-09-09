"""Indexing, without FastAPI, the broker or a container.

``indexer.py`` is 792 lines of pure functions over a directory tree and had no tests at
all. Its own docstring promises it is the part most likely to need a fix at an awkward
moment. Three of the things it does are the reason this rail exists, and every one of
them fails *silently*:

* **The citation verifier.** Every generated summary is a claim, and each action item's
  quote is checked against the real transcript. An unflagged fabrication is
  indistinguishable from a verified citation, so a broken verifier leaves the rail
  vouching for model output while checking nothing.
* **UTC to local.** Recording timestamps are UTC; everything a user reads is local.
  Reading either naively does not error, it just moves meetings between days and weeks,
  reshuffling the roll-ups the whole UI is built from.
* **Source precedence.** A co-work sidecar has to beat the Meetily database, or a local
  4B model's summary outranks the good one and nobody is told.

Every tree here is built under ``tmp_path`` from invented content (Acme, Project Falcon).
Nothing reads the real recordings mount.

Five tests are ``xfail(strict=True)``: they state the contract INGEST.md advertises,
against defects that are live today. Each names its defect and a line number in its
``reason``. They report as xfail now and turn into loud failures the moment the defect
is fixed, so nobody has to remember to come back and delete them.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from meeting_atlas_app import indexer

# The rail's own default (MEETING_ATLAS_DISPLAY_TZ). Named explicitly rather than taken
# from settings: these assertions are wrong under any other zone, so the zone has to be
# visible in the test, not inherited from the box that happens to run it.
LA = ZoneInfo("America/Los_Angeles")

# A Thursday-morning meeting, in local time, used as the summary's anchor date.
MEETING_DT = datetime(2026, 8, 20, 6, 59, 29, tzinfo=LA)

# Synthetic transcript. Timings are seconds from the start of the audio, per INGEST.md.
SEGMENTS = [
    {"start": 0.0, "duration": 4.0, "speaker": "Ada",
     "text": "Morning everyone, this is the Project Falcon sync."},
    {"start": 12.0, "duration": 6.0, "speaker": "Ada",
     "text": "We should freeze the schema before the cutover."},
    {"start": 30.0, "duration": 5.0, "speaker": "Bo",
     "text": "I will send the Acme account transfer list on Friday."},
    {"start": 90.0, "duration": 5.0, "speaker": "Cy",
     "text": "Someone from the team needs to own the runbook."},
    {"start": 200.0, "duration": 7.0, "speaker": "Bo",
     "text": "The post migration checklist still needs an owner."},
]

# The five verdicts the whole rail turns on. Asserted as a sorted list, never as
# "is truthy": a test that only checks the flag it expects cannot see a second flag
# firing by accident, and a spurious flag is as damaging as a missing one.
FLAGS = ("due_suspect", "due_uniform", "quote_missing", "quote_reused", "ts_mismatch")

ACTION_HEADER = [
    "| Owner | Task | Due | Reference Transcript Segment | Segment Time stamp |",
    "|---|---|---|---|---|",
]


def flags_on(item):
    return sorted(k for k in FLAGS if item.get(k))


def actions(rows, segments=SEGMENTS, meeting_dt=MEETING_DT):
    """Parse an Action Items table through the public entry point."""
    md = "\n".join(["**Action Items**", ""] + ACTION_HEADER + list(rows))
    return indexer.parse_summary(md, meeting_dt, segments)["actions"]


def row(owner="Ada", task="Freeze the schema.", due="TBD", ref="", ts=""):
    return "| %s | %s | %s | %s | %s |" % (owner, task, due, ref, ts)


def cite(seconds_text, quote):
    return '[%s] "%s"' % (seconds_text, quote)


# ---------------------------------------------------------------- tree fixtures

def write_meeting(root, folder, *, meta=None, plain=None, enriched=None, summary=None):
    """One meeting directory, holding exactly the sidecars INGEST.md defines."""
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    for name, payload in (("metadata.json", meta),
                          ("transcripts.json", plain),
                          ("transcript.enriched.json", enriched),
                          ("summary.json", summary)):
        if payload is not None:
            (d / name).write_text(json.dumps(payload), encoding="utf-8")
    return d


def meetily_transcript(segments=SEGMENTS):
    """Meetily's own shape: audio_start_time / audio_end_time, and never a speaker."""
    return {"segments": [{"audio_start_time": s["start"],
                          "audio_end_time": s["start"] + s["duration"],
                          "text": s["text"]} for s in segments]}


def write_meetily_db(path, *, meeting_id, title, folder_path, created_at,
                     markdown=None, model="gemma3:4b", provider="ollama", secs=57.61,
                     with_summary_table=True):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE meetings "
                "(id TEXT PRIMARY KEY, title TEXT, created_at TEXT, folder_path TEXT)")
    con.execute("INSERT INTO meetings VALUES (?, ?, ?, ?)",
                (meeting_id, title, created_at, folder_path))
    if with_summary_table:
        con.execute("CREATE TABLE summary_processes "
                    "(meeting_id TEXT, status TEXT, result TEXT, processing_time REAL)")
        if markdown is not None:
            result = json.dumps({"english_cache": {
                "markdown": markdown,
                "source": {"model_name": model, "model_provider": provider}}})
            con.execute("INSERT INTO summary_processes VALUES (?, ?, ?, ?)",
                        (meeting_id, "completed", result, secs))
    con.commit()
    con.close()
    return str(path)


@pytest.fixture(autouse=True)
def _clear_db_snapshot():
    """read_db snapshots to ONE fixed name in the system temp dir, and never clears it.

    Without this, the order tests happen to run in decides what read_db returns (see
    test_a_second_read_is_not_served_from_the_previous_snapshot for why). Reset around
    every test so each one starts from the same place.
    """
    snapshot = os.path.join(tempfile.gettempdir(), "ma_meetily_snapshot.sqlite")
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(snapshot + suffix)
        except OSError:
            pass
    yield


# ================================================================ citation verifier
#
# The reason the rail exists. Each flag gets a clean case that produces NO flag and a
# case that produces exactly that flag.

def test_a_sound_citation_produces_no_flag():
    item, = actions([row(due="Aug 28",
                         ref=cite("00:12", "freeze the schema before the cutover"),
                         ts="00:12")])
    assert flags_on(item) == []
    # The quote is anchored to the segment it was actually found in, which is what the
    # "verified 0:12" badge links to. 12.0 is the segment start, not the claimed time.
    assert item["quote_at"] == 12.0
    assert item["claimed_at"] == 12
    assert item["owner"] == "Ada"


def test_quote_missing_when_the_cited_line_is_nowhere_in_the_transcript():
    item, = actions([row(ref=cite("00:12", "sign off on the vendor contract"),
                         ts="00:12")])
    # A fabricated citation. Nothing else may fire: quote_at is absent, so the UI has
    # no position to link to and cannot present this as verified.
    assert flags_on(item) == ["quote_missing"]
    assert "quote_at" not in item


def test_ts_mismatch_fires_past_the_twenty_second_tolerance():
    quote = "send the Acme account transfer list"   # really at 30.0
    near, = actions([row(ref=cite("00:45", quote), ts="00:45")])
    far, = actions([row(ref=cite("00:55", quote), ts="00:55")])
    # 15s out is inside tolerance; the quote may sit anywhere inside a long segment.
    assert flags_on(near) == []
    # 25s out is a timestamp the model did not derive from the text.
    assert flags_on(far) == ["ts_mismatch"]
    # Both still resolve to the same real position, so the badge can say "cited 0:55,
    # found 0:30" rather than dropping the citation entirely.
    assert near["quote_at"] == far["quote_at"] == 30.0


def test_quote_reused_when_one_line_backs_two_unrelated_tasks():
    quote = "freeze the schema before the cutover"
    both = actions([
        row(owner="Ada", task="Freeze the schema.", ref=cite("00:12", quote), ts="00:12"),
        row(owner="Bo", task="Write the runbook.", ref=cite("00:12", quote), ts="00:12"),
    ])
    assert len(both) == 2
    # Both ends of the pair are flagged, not just the second. Flagging only the repeat
    # would let a reader trust whichever item they read first.
    assert [flags_on(i) for i in both] == [["quote_reused"], ["quote_reused"]]


def test_two_distinct_quotes_are_not_reuse():
    both = actions([
        row(owner="Ada", ref=cite("00:12", "freeze the schema before the cutover"), ts="00:12"),
        row(owner="Bo", ref=cite("00:30", "send the Acme account transfer list"), ts="00:30"),
    ])
    assert [flags_on(i) for i in both] == [[], []]


def test_due_uniform_needs_three_items_carrying_the_same_date():
    dated = [row(owner=o, task=t, due="2026-09-15", ref=cite(c, q), ts=c) for o, t, c, q in [
        ("Ada", "Freeze the schema.", "00:12", "freeze the schema before the cutover"),
        ("Bo", "Send the transfer list.", "00:30", "send the Acme account transfer list"),
        ("Cy", "Own the runbook.", "01:30", "needs to own the runbook"),
    ]]
    two = actions(dated[:2])
    three = actions(dated)
    # Two items sharing a date is a plan; three is the fingerprint of a model filling in
    # a column. The threshold is load-bearing, so it is pinned from both sides.
    assert [flags_on(i) for i in two] == [[], []]
    assert [flags_on(i) for i in three] == [["due_uniform"]] * 3
    # 2026-09-15 is 26 days out, so due_suspect must NOT ride along and double-count.
    assert all("due_suspect" not in i for i in three)


@pytest.mark.parametrize("due, expected", [
    ("Aug 28", []),          # 8 days out
    ("Dec 15", []),          # 117 days out, inside the 120-day horizon
    ("Dec 20", ["due_suspect"]),      # 122 days out
    ("2026-08-19", []),               # yesterday: allowed, clocks and time zones drift
    ("2026-08-18", ["due_suspect"]),  # two days before the meeting that created it
    ("2026-05-01", ["due_suspect"]),  # months in the past
    ("Jan 5", ["due_suspect"]),       # bare month/day, rolled into 2027, then 138 days out
    ("TBD", []),
    ("ASAP", []),
    ("next Friday", []),     # unparseable, so it is left alone rather than guessed at
])
def test_due_suspect_horizon(due, expected):
    item, = actions([row(due=due,
                         ref=cite("00:12", "freeze the schema before the cutover"),
                         ts="00:12")])
    assert flags_on(item) == expected


def test_a_bare_timestamp_column_anchors_the_claim_when_the_ref_has_none():
    # The reference cell carries only a quote; the claimed time comes from the separate
    # timestamp column, which is the shape INGEST.md documents.
    item, = actions([row(ref='"send the Acme account transfer list"', ts="00:55")])
    assert item["claimed_at"] == 55
    assert flags_on(item) == ["ts_mismatch"]


def test_an_hours_long_recording_reads_hh_mm_ss():
    assert indexer._claimed_seconds("[1:02:33]") == 3753
    assert indexer._claimed_seconds("[02:33]") == 153
    # Falls back to a bare clock outside brackets rather than losing the anchor.
    assert indexer._claimed_seconds("around 12:05 in") == 725
    assert indexer._claimed_seconds("no time here") is None


def test_column_headers_are_matched_by_keyword_not_by_position():
    md = "\n".join([
        "**Next Steps**", "",
        "| Assigned to | Action item | Deadline | Transcript quote | Timestamp |",
        "|---|---|---|---|---|",
        '| Bo | Send the list. | TBD | [00:30] "send the Acme account transfer list" | 00:30 |',
    ])
    item, = indexer.parse_summary(md, MEETING_DT, SEGMENTS)["actions"]
    # "Next Steps" is an alias for Actions, and no header word here is the literal one
    # from the example table. Nothing may land in the wrong column.
    assert item["owner"] == "Bo"
    assert item["task"] == "Send the list."
    assert item["due"] == "TBD"
    assert item["quote_at"] == 30.0
    assert flags_on(item) == []


def test_the_four_word_probe_rescues_a_lightly_reworded_quote():
    # A model that quotes the opening of a line and then paraphrases the rest is still
    # pointing at real evidence, so the first four words are accepted as the anchor.
    item, = actions([row(ref=cite("00:12", "freeze the schema before we ship anything"),
                         ts="00:12")])
    assert item["quote_at"] == 12.0
    assert flags_on(item) == []


def test_find_quote_ignores_punctuation_and_case():
    assert indexer.find_quote("FREEZE THE SCHEMA, before the cutover!", SEGMENTS) == 12.0
    assert indexer.find_quote("the post migration checklist", SEGMENTS) == 200.0
    assert indexer.find_quote("we never said this at all", SEGMENTS) is None


def test_the_meeting_row_counts_every_flagged_item_once(tmp_path):
    # Distinct due dates deliberately: three TBDs would add due_uniform to all three
    # (see test_three_placeholder_dues_are_not_a_uniform_due_date).
    md = "\n".join(["**Action Items**", ""] + ACTION_HEADER + [
        row(owner="Ada", task="Freeze the schema.", due="Aug 28",
            ref=cite("00:12", "sign off on the vendor contract"), ts="00:12"),
        row(owner="Bo", task="Send the list.", due="Sep 4",
            ref=cite("00:55", "send the Acme account transfer list"), ts="00:55"),
        row(owner="Cy", task="Own the runbook.", due="TBD",
            ref=cite("01:30", "needs to own the runbook"), ts="01:30"),
    ])
    write_meeting(tmp_path, "Meeting 2026-08-20_06-59-29",
                  meta={"created_at": "2026-08-20T13:59:29+00:00", "duration_seconds": 240.0},
                  plain=meetily_transcript(),
                  summary={"markdown": md, "model": "claude-opus-5", "provider": "anthropic"})
    idx = indexer.build_index(str(tmp_path), tz=LA)
    meeting, = idx["meetings"]
    assert meeting["n_actions"] == 3
    # One fabricated quote and one bad timestamp. The third item is clean, so the
    # badge count on the meeting card is 2, not 3 and not 1.
    assert meeting["flags"] == 2
    assert idx["corpus"]["n_flagged"] == 2
    assert meeting["owners"] == ["Ada", "Bo", "Cy"]


def test_three_placeholder_dues_are_not_a_uniform_due_date():
    """Regression: a summary used to be flagged for following the instructions.

    _mark_shared counted placeholder dues like real dates while _due_implausible deliberately
    whitelisted them, so three items correctly marked TBD — which INGEST.md tells writers to
    use when no date was spoken aloud — all came back with due_uniform. Both sites now share
    one _PLACEHOLDER_DUE pattern, so they cannot drift apart on what counts as a date.
    """
    items = actions([
        row(owner="Ada", task="Freeze the schema.", due="TBD",
            ref=cite("00:12", "freeze the schema before the cutover"), ts="00:12"),
        row(owner="Bo", task="Send the transfer list.", due="TBD",
            ref=cite("00:30", "send the Acme account transfer list"), ts="00:30"),
        row(owner="Cy", task="Own the runbook.", due="TBD",
            ref=cite("01:30", "needs to own the runbook"), ts="01:30"),
    ])
    assert [flags_on(i) for i in items] == [[], [], []]


def test_a_quote_containing_an_apostrophe_is_checked_in_full():
    """Regression: the quote used to be truncated at its first apostrophe.

    _extract_quote's character class treated the straight apostrophe as a closing delimiter
    as well as excluding it from the body, so this fabricated line collapsed to "the team",
    matched an unrelated segment, and came back with a quote_at — a made-up citation reported
    as verified, which is the single failure this rail exists to prevent. Most spoken English
    contains contractions, so it was the common case, not an edge one.
    """
    item, = actions([row(task="Chase the runbook.",
                         ref=cite("03:20", "the team's runbook is not written"),
                         ts="03:20")])
    assert item["quote"] == "the team's runbook is not written"
    # That sentence is nowhere in SEGMENTS, so the only correct verdict is a fabrication.
    assert flags_on(item) == ["quote_missing"]


def test_a_short_quote_that_is_really_in_the_transcript_is_not_called_missing():
    """Regression: extraction accepted 6 raw characters while find_quote demanded 8
    normalised, so a genuine short quote was extracted, failed to match, and was reported as
    fabricated. Both now use the same normalised threshold; a quote too short to verify is
    simply not checked, which is a false negative rather than a false accusation.
    """
    item, = actions([row(ref=cite("00:12", "cutover"), ts="00:12")])
    assert flags_on(item) == []


# ================================================================ UTC vs local
#
# rail.json note 5: recording timestamps are UTC, everything a user reads is local, and
# reading either naively puts every meeting hours off. Nothing raises when it is wrong.

def test_utc_created_at_lands_on_the_local_day_week_and_weekday(tmp_path):
    # 02:30 UTC on Monday 2026-01-05 is 18:30 PST on SUNDAY 2026-01-04. Three separate
    # roll-ups move together: the day, the weekday column, and the ISO week - Sunday
    # closes 2026-W01 while the Monday it looks like in UTC opens 2026-W02.
    write_meeting(tmp_path, "Meeting 2026-01-04_18-30-00",
                  meta={"created_at": "2026-01-05T02:30:00+00:00", "duration_seconds": 60.0},
                  plain=meetily_transcript(SEGMENTS[:1]))
    meeting, = indexer.build_index(str(tmp_path), tz=LA)["meetings"]
    assert meeting["date"] == "2026-01-04"
    assert meeting["week"] == "2026-W01"
    assert meeting["month"] == "2026-01"
    assert meeting["dow"] == 6                      # Sunday, not Monday
    assert meeting["start"] == "2026-01-04T18:30:00"
    assert meeting["start_min"] == 18 * 60 + 30     # an evening meeting, not a 2am one


def test_the_utc_offset_is_not_a_constant_eight_hours(tmp_path):
    # Same wall-clock instant in August: PDT is UTC-7, so the same 02:30Z is 19:30 the
    # previous evening, not 18:30. A hardcoded offset gets one of these two wrong.
    write_meeting(tmp_path, "Meeting 2026-08-19_19-30-00",
                  meta={"created_at": "2026-08-20T02:30:00+00:00", "duration_seconds": 60.0},
                  plain=meetily_transcript(SEGMENTS[:1]))
    meeting, = indexer.build_index(str(tmp_path), tz=LA)["meetings"]
    assert meeting["start"] == "2026-08-19T19:30:00"
    assert meeting["date"] == "2026-08-19"


def test_a_naive_created_at_is_read_as_local_and_never_shifted(tmp_path):
    # A hand-written sidecar timestamp with no offset is taken at face value. Treating
    # it as UTC would drag every one of them 7 or 8 hours backwards.
    write_meeting(tmp_path, "Meeting 2026-08-20_06-59-29",
                  meta={"created_at": "2026-08-20T06:59:29", "duration_seconds": 60.0},
                  plain=meetily_transcript(SEGMENTS[:1]))
    meeting, = indexer.build_index(str(tmp_path), tz=LA)["meetings"]
    assert meeting["start"] == "2026-08-20T06:59:29"
    assert meeting["date"] == "2026-08-20"


def test_the_folder_name_fallback_reads_the_local_stamp_not_the_utc_one(tmp_path):
    # Meetily's folder name carries two timestamps: a leading LOCAL one and a trailing
    # UTC one. With no metadata.json the leading one is the only correct source; the
    # trailing 01-00 would put this Wednesday-evening meeting on Thursday.
    write_meeting(tmp_path, "Meeting 2026-08-19_18-00-00_2026-08-20_01-00",
                  plain=meetily_transcript(SEGMENTS[:1]))
    meeting, = indexer.build_index(str(tmp_path), tz=LA)["meetings"]
    assert meeting["date"] == "2026-08-19"
    assert meeting["start"] == "2026-08-19T18:00:00"


def test_the_last_resort_mtime_is_also_read_in_the_display_zone(tmp_path):
    # No metadata.json, no database row, and a folder name with no timestamp in it. The
    # folder's mtime is a POSIX instant, so it needs the same conversion as created_at.
    folder = write_meeting(tmp_path, "Falcon war room", plain=meetily_transcript(SEGMENTS[:1]))
    stamp = datetime(2026, 8, 20, 2, 30, tzinfo=ZoneInfo("UTC")).timestamp()
    os.utime(folder, (stamp, stamp))
    meeting, = indexer.build_index(str(tmp_path), tz=LA)["meetings"]
    assert meeting["start"] == "2026-08-19T19:30:00"
    assert meeting["date"] == "2026-08-19"


def test_display_tz_env_var_selects_the_zone(monkeypatch):
    from meeting_atlas_app import config

    monkeypatch.setenv("MEETING_ATLAS_DISPLAY_TZ", "America/New_York")
    assert config.MeetingAtlasSettings().tz() == ZoneInfo("America/New_York")
    monkeypatch.setenv("MEETING_ATLAS_DISPLAY_TZ", "America/Los_Angeles")
    assert config.MeetingAtlasSettings().tz() == LA


def test_the_same_instant_reads_differently_in_two_zones(tmp_path):
    # The same recording under an east-coast display zone is a Sunday NIGHT meeting on
    # the 4th; under a west-coast one it is a Sunday evening. Proof the zone actually
    # reaches the roll-up rather than being decorative.
    write_meeting(tmp_path, "Meeting 2026-01-04_18-30-00",
                  meta={"created_at": "2026-01-05T02:30:00+00:00", "duration_seconds": 60.0},
                  plain=meetily_transcript(SEGMENTS[:1]))
    west, = indexer.build_index(str(tmp_path), tz=LA)["meetings"]
    east, = indexer.build_index(str(tmp_path), tz=ZoneInfo("America/New_York"))["meetings"]
    assert (west["start"], west["date"]) == ("2026-01-04T18:30:00", "2026-01-04")
    assert (east["start"], east["date"]) == ("2026-01-04T21:30:00", "2026-01-04")


def test_parse_iso_survives_meetilys_nine_digit_fraction():
    # Meetily writes nanoseconds; datetime.fromisoformat accepts at most microseconds,
    # so an unpatched parse returns None and the meeting silently falls back to mtime.
    parsed = indexer.parse_iso("2026-08-20T13:59:29.123456789+00:00")
    assert parsed == datetime(2026, 8, 20, 13, 59, 29, 123456, tzinfo=ZoneInfo("UTC"))
    assert indexer.parse_iso("2026-08-20T13:59:29Z") is not None
    assert indexer.parse_iso("not a date") is None
    assert indexer.parse_iso("") is None


# ================================================================ ISO week bucketing

@pytest.mark.parametrize("day, bucket", [
    ("2025-12-29", "2026-W01"),   # Monday of ISO week 1 of 2026, in calendar year 2025
    ("2026-01-04", "2026-W01"),   # the Sunday that closes it
    ("2026-01-05", "2026-W02"),
    ("2026-12-31", "2026-W53"),   # a 53-week ISO year
    ("2027-01-01", "2026-W53"),   # January, still filed under 2026
    ("2027-01-04", "2027-W01"),
])
def test_iso_week_uses_the_iso_year_not_the_calendar_year(day, bucket):
    # The off-by-one that matters: taking .year instead of isocalendar()[0] files the
    # last days of December under week 1 of the wrong year, and the week roll-up shows
    # two "W01" buckets a year apart merged into one.
    assert indexer.iso_week(datetime.fromisoformat(day)) == bucket


def test_week_bucketing_is_done_after_the_local_conversion(tmp_path):
    # Belt and braces on the two features that interact: this instant is 2026-W02 read
    # as UTC and 2026-W01 read locally, so a UTC bucket would move it a whole week.
    write_meeting(tmp_path, "Meeting 2026-01-04_18-30-00",
                  meta={"created_at": "2026-01-05T02:30:00+00:00", "duration_seconds": 60.0},
                  plain=meetily_transcript(SEGMENTS[:1]))
    meeting, = indexer.build_index(str(tmp_path), tz=LA)["meetings"]
    assert indexer.iso_week(indexer.parse_iso("2026-01-05T02:30:00+00:00")) == "2026-W02"
    assert meeting["week"] == "2026-W01"


# ================================================================ source precedence
#
# indexer.py:12-20. enriched transcript > Meetily transcript; sidecar summary > the
# Meetily database; sidecar title > database title > markdown H1 > folder name.

FALCON_FOLDER = "Meeting 2026-08-20_06-59-29"
FALCON_META = {"created_at": "2026-08-20T13:59:29+00:00", "duration_seconds": 240.0,
               "meeting_name": FALCON_FOLDER}
# The database stores an absolute WINDOWS path while the backend runs in a Linux
# container. folder_key exists because os.path.basename does not split on backslashes
# there, and every title silently stopped matching its folder.
FALCON_DB_PATH = r"C:\Users\you\Music\meetily-recordings\Meeting 2026-08-20_06-59-29"


def _falcon_tree(tmp_path, **kw):
    write_meeting(tmp_path, FALCON_FOLDER, meta=FALCON_META,
                  plain=meetily_transcript(), **kw)
    db = write_meetily_db(tmp_path / "meetily.sqlite", meeting_id="mtg-falcon",
                          title="Project Falcon Weekly", folder_path=FALCON_DB_PATH,
                          created_at="2026-08-20T13:59:29.123456789",
                          markdown="**Summary**\n\nMeetily's own local summary.\n")
    return db


def test_folder_key_folds_a_windows_path_to_its_final_component():
    assert indexer.folder_key(FALCON_DB_PATH) == "meeting 2026-08-20_06-59-29"
    assert indexer.folder_key("/data/recordings/Meeting 2026-08-20_06-59-29/") == \
        "meeting 2026-08-20_06-59-29"
    assert indexer.folder_key(FALCON_DB_PATH + "\\") == "meeting 2026-08-20_06-59-29"


def test_the_database_supplies_the_title_and_summary_when_nothing_else_does(tmp_path):
    db = _falcon_tree(tmp_path)
    meeting, = indexer.build_index(str(tmp_path), db_path=db, tz=LA)["meetings"]
    # The id comes from the database row, so a browser bookmark survives a re-index.
    assert meeting["id"] == "mtg-falcon"
    assert meeting["title"] == "Project Falcon Weekly"
    assert meeting["titled"] is True
    assert meeting["summary_source"] == "Meetily"
    assert meeting["summary_model"] == "gemma3:4b (ollama)"
    assert meeting["overview"] == "Meetily's own local summary."


def test_a_sidecar_summary_and_title_beat_the_database(tmp_path):
    db = _falcon_tree(tmp_path, summary={
        "title": "Falcon Cutover Review", "model": "claude-opus-5",
        "provider": "anthropic", "elapsed_s": 41.2,
        "markdown": "**Summary**\n\nThe co-work summary.\n"})
    meeting, = indexer.build_index(str(tmp_path), db_path=db, tz=LA)["meetings"]
    assert meeting["title"] == "Falcon Cutover Review"
    assert meeting["summary_source"] == "sidecar"
    assert meeting["summary_model"] == "claude-opus-5 (anthropic)"
    assert meeting["overview"] == "The co-work summary."


def test_the_database_title_survives_a_sidecar_that_carries_none(tmp_path):
    db = _falcon_tree(tmp_path, summary={
        "model": "claude-opus-5", "provider": "anthropic",
        "markdown": "**Summary**\n\nThe co-work summary.\n"})
    meeting, = indexer.build_index(str(tmp_path), db_path=db, tz=LA)["meetings"]
    # Only the fields the sidecar actually supplies override the database.
    assert meeting["title"] == "Project Falcon Weekly"
    assert meeting["overview"] == "The co-work summary."


def test_the_title_chain_falls_through_to_the_markdown_h1_then_the_folder(tmp_path):
    write_meeting(tmp_path, FALCON_FOLDER, meta={"created_at": FALCON_META["created_at"]},
                  plain=meetily_transcript(),
                  summary={"markdown": "# Project Falcon Cutover\n\n**Summary**\n\nText.\n"})
    write_meeting(tmp_path, "Meeting 2026-08-18_09-00-00", plain=meetily_transcript(SEGMENTS[:1]))
    by_folder = {m["folder"]: m for m in indexer.build_index(str(tmp_path), tz=LA)["meetings"]}

    h1 = by_folder[FALCON_FOLDER]
    assert h1["title"] == "Project Falcon Cutover"
    # titled tracks the DATABASE/sidecar title field only, so a meeting named solely by
    # its markdown heading still reports as untitled. The UI reads this to decide
    # whether to show the auto-name, so the two disagree here.
    assert h1["titled"] is False

    bare = by_folder["Meeting 2026-08-18_09-00-00"]
    assert bare["title"] == "Meeting 2026-08-18_09-00-00"
    assert bare["auto_title"] == "Meeting 2026-08-18_09-00-00"
    assert bare["id"] == "folder-" + indexer.slug("Meeting 2026-08-18_09-00-00")


def test_an_enriched_transcript_wins_and_brings_the_speakers(tmp_path):
    write_meeting(tmp_path, FALCON_FOLDER, meta=FALCON_META,
                  plain=meetily_transcript([{"start": 0.0, "duration": 3.0,
                                             "text": "Meetily's own words."}]),
                  enriched={"model": "claude-opus-5", "source": "co-work re-transcription",
                            "segments": SEGMENTS})
    idx = indexer.build_index(str(tmp_path), tz=LA)
    meeting, = idx["meetings"]
    assert meeting["transcript_source"] == "co-work re-transcription"
    assert meeting["transcript_model"] == "claude-opus-5"
    assert meeting["n_segments"] == 5
    # Meetily never writes a speaker, so the "Who talked" panel exists only because the
    # sidecar won. Three named speakers, no invented ones.
    assert {s["speaker"] for s in meeting["speakers"]} == {"Ada", "Bo", "Cy"}
    assert idx["corpus"]["n_enriched"] == 1


def test_an_unusable_enriched_transcript_falls_back_instead_of_emptying_the_meeting(tmp_path):
    # Segments whose text is blank are dropped, which can empty the list entirely. A
    # half-written sidecar must not delete a meeting that Meetily transcribed fine.
    write_meeting(tmp_path, FALCON_FOLDER, meta=FALCON_META,
                  plain=meetily_transcript(),
                  enriched={"segments": [{"start": 1.0, "text": "   "}]})
    meeting, = indexer.build_index(str(tmp_path), tz=LA)["meetings"]
    assert meeting["transcript_source"] == "Meetily"
    assert meeting["n_segments"] == 5


def test_a_folder_with_no_transcript_at_all_is_skipped(tmp_path):
    write_meeting(tmp_path, "Meeting 2026-08-21_10-00-00",
                  meta={"created_at": "2026-08-21T17:00:00+00:00"})
    (tmp_path / "loose-file.txt").write_text("not a meeting", encoding="utf-8")
    idx = indexer.build_index(str(tmp_path), tz=LA)
    assert idx["meetings"] == []
    assert idx["corpus"]["n_meetings"] == 0
    assert idx["corpus"]["available"] is True


def test_a_missing_recordings_root_reports_unavailable_rather_than_raising(tmp_path):
    idx = indexer.build_index(str(tmp_path / "nope"), tz=LA)
    assert idx["corpus"]["available"] is False
    assert idx["corpus"]["n_meetings"] == 0
    assert idx["meetings"] == [] and idx["details"] == {}


def test_a_sidecar_summary_is_never_labelled_with_the_database_model(tmp_path):
    """Regression: a sidecar summary inherited the database's model attribution.

    The merge dropped None values, so a summary.json with no "model" key kept the Meetily
    database's summary_model — the row then read summary_source: "sidecar" next to the local
    model's name. INGEST.md promises a summary is never silently attributed to the wrong
    model. The sidecar now owns its attribution, including the right to say nothing.
    """
    db = _falcon_tree(tmp_path,
                      summary={"markdown": "**Summary**\n\nThe co-work summary.\n"})
    meeting, = indexer.build_index(str(tmp_path), db_path=db, tz=LA)["meetings"]
    assert meeting["summary_source"] == "sidecar"
    assert meeting["overview"] == "The co-work summary."
    assert meeting["summary_model"] != "gemma3:4b (ollama)"


# ================================================================ the meetily database

def test_read_db_degrades_to_no_titles_rather_than_raising(tmp_path):
    # Every failure mode here has to return {}: a missing or unreadable database costs
    # the rail its titles, never the rail itself.
    assert indexer.read_db(None) == {}
    assert indexer.read_db(str(tmp_path / "absent.sqlite")) == {}
    junk = tmp_path / "junk.sqlite"
    junk.write_text("this is not a database", encoding="utf-8")
    assert indexer.read_db(str(junk)) == {}


def test_read_db_reads_a_database_the_meetily_app_still_holds_open(tmp_path):
    """The whole reason read_db copies first: Meetily keeps the DB open in WAL mode."""
    path = tmp_path / "hot.sqlite"
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE meetings "
                "(id TEXT PRIMARY KEY, title TEXT, created_at TEXT, folder_path TEXT)")
    con.execute("CREATE TABLE summary_processes "
                "(meeting_id TEXT, status TEXT, result TEXT, processing_time REAL)")
    con.execute("INSERT INTO meetings VALUES (?, ?, ?, ?)",
                ("mtg-falcon", "Project Falcon Weekly", "2026-08-20T13:59:29",
                 FALCON_DB_PATH))
    con.commit()
    try:
        # The writer is still connected, so the row lives in the -wal, not the main file.
        assert os.path.isfile(str(path) + "-wal")
        out = indexer.read_db(str(path))
        assert list(out) == ["meeting 2026-08-20_06-59-29"]
        assert out["meeting 2026-08-20_06-59-29"]["title"] == "Project Falcon Weekly"
    finally:
        con.close()


def test_the_summary_markdown_is_dug_out_of_whichever_shape_meetily_wrote():
    # summary_processes.result is a JSON blob whose shape has moved between Meetily
    # versions. All three readings have to work, and anything else has to yield None
    # rather than putting a JSON dump on the page as if it were a summary.
    assert indexer._extract_markdown(
        json.dumps({"english_cache": {"markdown": "# Falcon\n"}})) == "# Falcon\n"
    assert indexer._extract_markdown(
        json.dumps({"markdown": "# Falcon\n"})) == "# Falcon\n"
    assert indexer._extract_markdown("# Falcon\n") == "# Falcon\n"   # not JSON at all
    assert indexer._extract_markdown(json.dumps({"status": "processing"})) is None
    assert indexer._extract_markdown(None) is None
    # The model label degrades one field at a time instead of vanishing entirely.
    assert indexer._summary_model(json.dumps({"english_cache": {"source": {
        "model_name": "gemma3:4b", "model_provider": "ollama"}}})) == "gemma3:4b (ollama)"
    assert indexer._summary_model(json.dumps({"english_cache": {"source": {
        "model_name": "gemma3:4b"}}})) == "gemma3:4b"
    assert indexer._summary_model("not json") is None


def test_a_database_without_summary_processes_loses_its_titles_too(tmp_path):
    # Pinning current behaviour, not endorsing it: the summaries query runs first and
    # its OperationalError aborts the whole read, so a Meetily schema change costs the
    # titles as well as the summaries. See the report.
    db = write_meetily_db(tmp_path / "old.sqlite", meeting_id="mtg-falcon",
                          title="Project Falcon Weekly", folder_path=FALCON_DB_PATH,
                          created_at="2026-08-20T13:59:29", with_summary_table=False)
    assert indexer.read_db(db) == {}


def test_a_second_read_is_not_served_from_the_previous_snapshot(tmp_path):
    """Regression: a read could return the PREVIOUS database's rows.

    The snapshot used one fixed filename in the shared temp dir and copied -wal/-shm only
    when the source had them, never clearing stale ones, so an earlier read's write-ahead log
    was replayed over the freshly copied file. The fixed name was also a collision between
    concurrent builds. Each call now gets a private directory that is removed afterwards.
    """
    hot = tmp_path / "hot.sqlite"
    con = sqlite3.connect(str(hot))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE meetings "
                "(id TEXT PRIMARY KEY, title TEXT, created_at TEXT, folder_path TEXT)")
    con.execute("CREATE TABLE summary_processes "
                "(meeting_id TEXT, status TEXT, result TEXT, processing_time REAL)")
    con.execute("INSERT INTO meetings VALUES (?, ?, ?, ?)",
                ("mtg-old", "Stale Falcon Retro", "2026-08-13T13:59:29", FALCON_DB_PATH))
    con.commit()
    try:
        assert indexer.read_db(str(hot))["meeting 2026-08-20_06-59-29"]["db_id"] == "mtg-old"
        cold = write_meetily_db(tmp_path / "cold.sqlite", meeting_id="mtg-new",
                                title="Project Falcon Weekly", folder_path=FALCON_DB_PATH,
                                created_at="2026-08-20T13:59:29")
        assert indexer.read_db(cold)["meeting 2026-08-20_06-59-29"]["db_id"] == "mtg-new"
    finally:
        con.close()


# ================================================================ summary parsing

def test_the_recognised_sections_are_structured_and_the_rest_survives_raw():
    md = "\n".join([
        "# Project Falcon Sync", "",
        "**Summary**", "",
        "The team agreed to freeze the schema before the Acme cutover.", "",
        "**Key Decisions**", "",
        "- Freeze the schema on Thursday.",
        "- Acme keeps its own account ids.", "",
        "**Discussion Highlights**", "",
        "- **Cutover window:** Saturday night was preferred.",
        "- No owner for the runbook yet.", "",
        "**Risks Nobody Parses**", "",
        "- The 9p mount rejects renames.",
    ])
    out = indexer.parse_summary(md, MEETING_DT, SEGMENTS)
    assert out["title"] == "Project Falcon Sync"
    assert out["overview"] == "The team agreed to freeze the schema before the Acme cutover."
    assert out["decisions"] == ["Freeze the schema on Thursday.",
                                "Acme keeps its own account ids."]
    assert out["highlights"][0]["title"] == "Cutover window"
    assert out["highlights"][0]["body"] == "Saturday night was preferred."
    # Three, not two. Pinning current behaviour: an unrecognised heading does NOT end
    # the section it follows, so the bullet under "Risks Nobody Parses" is absorbed
    # into Discussion Highlights. INGEST.md promises unrecognised content is "never
    # lost, just not structured"; it is in fact structured, under the wrong heading.
    assert [h["title"] for h in out["highlights"]] == ["Cutover window", None, None]
    assert out["highlights"][2]["body"] == "The 9p mount rejects renames."
    # It does at least survive verbatim for the raw view.
    assert out["raw"] == md


def test_action_items_written_as_plain_bullets_still_become_action_items():
    # Plenty of models never emit the table. The items are kept, and every citation
    # field is honestly empty rather than filled with a guess.
    md = "\n".join(["**Action Items**", "",
                    "- Ada to freeze the schema before the cutover.",
                    "- Bo to send the Acme account transfer list."])
    items = indexer.parse_summary(md, MEETING_DT, SEGMENTS)["actions"]
    assert [i["task"] for i in items] == ["Ada to freeze the schema before the cutover.",
                                          "Bo to send the Acme account transfer list."]
    assert all(i["owner"] is None and i["due"] is None for i in items)
    # No quote was cited, so there is nothing to verify and nothing to flag.
    assert [flags_on(i) for i in items] == [[], []]


def test_a_missing_summary_is_none_not_an_empty_shell():
    assert indexer.parse_summary(None, MEETING_DT, SEGMENTS) is None
    assert indexer.parse_summary("", MEETING_DT, SEGMENTS) is None


# ================================================================ metrics

def test_derive_counts_pauses_and_buckets_the_sparkline():
    segments = [
        {"start": 0.0, "duration": 4.0, "text": "Morning everyone, this is the sync."},
        {"start": 12.0, "duration": 6.0, "text": "We should freeze the schema first."},
        {"start": 30.0, "duration": 5.0, "text": "Do we have a rollback plan?"},
        {"start": 90.0, "duration": 5.0, "text": "Someone needs to own the runbook."},
    ]
    met = indexer.derive(segments, duration_s=120.0)
    assert met["n_segments"] == 4
    assert met["words"] == 6 + 6 + 6 + 6
    assert met["spoken_s"] == 20.0
    # Three gaps over the 2s threshold: 8s, 12s and 55s. The 55s one is the longest.
    assert met["pauses"] == 3
    assert met["pause_s"] == 75.0
    assert met["longest_gap_s"] == 55.0
    assert met["questions"] == 1
    assert met["density"] == round(20.0 / 120.0, 3)
    # 120s of audio at 30s per bucket: four buckets, words filed by segment START.
    assert met["activity"] == [12, 6, 0, 6]
    assert sum(met["activity"]) == met["words"]
    # Under 30s of speech wpm is reported as 0 rather than extrapolated from a sample
    # too small to mean anything.
    assert met["wpm"] == 0
    # Past the threshold it is words per minute of SPOKEN time, not of elapsed time:
    # 90 words over 45s of speech is 120 wpm even inside a 10 minute recording.
    talky = [{"start": float(i * 15), "duration": 15.0,
              "text": " ".join(["falcon"] * 30)} for i in range(3)]
    assert indexer.derive(talky, duration_s=600.0)["wpm"] == 120.0


def test_speakers_appear_only_when_a_sidecar_supplied_them():
    met = indexer.derive(SEGMENTS, duration_s=240.0)
    speakers = {s["speaker"]: s for s in met["speakers"]}
    assert set(speakers) == {"Ada", "Bo", "Cy"}
    # Sorted by talk time, so the panel leads with whoever held the floor.
    assert [s["speaker"] for s in met["speakers"]] == ["Bo", "Ada", "Cy"]
    assert speakers["Ada"]["seconds"] == 10.0
    assert speakers["Ada"]["turns"] == 1        # two consecutive segments, one turn
    assert speakers["Bo"]["turns"] == 2         # two segments, split by Cy
    # Shares are of SPOKEN time (27s here), not of the recording's wall-clock length,
    # so they add to one across the panel rather than leaving silence unaccounted for.
    assert speakers["Bo"]["share"] == round(12.0 / 27.0, 3)
    assert speakers["Ada"]["share"] == round(10.0 / 27.0, 3)
    assert speakers["Cy"]["share"] == round(5.0 / 27.0, 3)

    # Strip the labels and the panel disappears rather than being guessed at from gaps.
    anonymous = [{k: v for k, v in s.items() if k != "speaker"} for s in SEGMENTS]
    assert indexer.derive(anonymous, duration_s=240.0)["speakers"] == []


def test_segments_are_normalised_from_either_shape_and_sorted(tmp_path):
    write_meeting(tmp_path, FALCON_FOLDER, meta=FALCON_META, enriched={"segments": [
        {"start": 30.0, "end": 35.0, "text": "Spoken second, listed first."},
        {"start": 5.0, "duration": 2.0, "text": "Spoken first, listed second."},
        {"start": 8.0, "text": ""},                     # dropped: no text
        {"start": "bad", "duration": None, "text": "Unparseable start."},
        "not a segment at all",
    ]})
    loaded = indexer.load_folder(str(tmp_path / FALCON_FOLDER))
    texts = [s["text"] for s in loaded["segments"]]
    assert texts == ["Unparseable start.", "Spoken first, listed second.",
                     "Spoken second, listed first."]
    # duration derived from `end`, and a junk start floors to 0.0 instead of raising:
    # one bad row must not cost the whole meeting.
    assert loaded["segments"][2]["duration"] == 5.0
    assert loaded["segments"][0]["start"] == 0.0
    assert loaded["segments"][0]["duration"] == 0.0


def test_details_are_kept_out_of_the_list_payload(tmp_path):
    write_meeting(tmp_path, FALCON_FOLDER, meta=FALCON_META,
                  enriched={"segments": SEGMENTS})
    idx = indexer.build_index(str(tmp_path), tz=LA)
    meeting, = idx["meetings"]
    assert "segments" not in meeting
    detail = idx["details"][meeting["id"]]
    # Positional rows, not objects: 5 segments x [start, duration, text, speaker].
    assert len(detail["segments"]) == 5
    assert detail["segments"][0] == [0.0, 4.0, SEGMENTS[0]["text"], "Ada"]


# ================================================================ keywords and IDF

def test_idf_prefers_the_term_that_is_not_in_every_meeting():
    counts = Counter({"falcon": 5, "meeting": 5})
    doc_freq = {"falcon": 1, "meeting": 10}
    # Identical raw frequency, so only IDF can separate them: "meeting" appears in all
    # ten transcripts and says nothing about this one.
    assert [k["t"] for k in indexer.keywords(counts, doc_freq, 10)] == ["falcon", "meeting"]
    # The reported n stays the raw count, not the score.
    assert indexer.keywords(counts, doc_freq, 10)[0]["n"] == 5


def test_a_term_said_once_never_reaches_the_keyword_list():
    assert indexer.keywords(Counter({"cutover": 1}), {"cutover": 1}, 1) == []
    assert indexer.keywords(Counter({"cutover": 2}), {"cutover": 1}, 1) == [
        {"t": "cutover", "n": 2}]


def test_keywords_degrade_to_plain_frequency_for_a_single_meeting():
    counts = Counter({"falcon": 9, "cutover": 4, "runbook": 2})
    top = indexer.keywords(counts, {t: 1 for t in counts}, 1, k=2)
    assert [k["t"] for k in top] == ["falcon", "cutover"]
    assert len(top) == 2


def test_fillers_and_stopwords_are_dropped_before_counting():
    counts = indexer.term_counts([
        {"start": 0.0, "duration": 1.0, "text": "Um."},
        {"start": 2.0, "duration": 1.0, "text": "Yeah, ok."},
        {"start": 4.0, "duration": 3.0, "text": "The Falcon cutover is the thing."},
    ])
    # Parakeet transcribes every "um" faithfully, so filler-only segments would
    # otherwise dominate the corpus themes.
    assert "the" not in counts and "yeah" not in counts and "thing" not in counts
    assert counts["falcon"] == 1
    assert counts["cutover"] == 1


def test_bigrams_are_counted_with_double_weight():
    counts = indexer.term_counts([
        {"start": 0.0, "duration": 3.0, "text": "The post migration checklist."},
    ])
    # A phrase said ONCE is stored as 2, which is how bigrams outrank the generic
    # unigrams they decompose into. Two consequences worth knowing: it clears the
    # "said at least twice" gate in keywords(), and the UI shows n=2 for a phrase
    # uttered once. See the report.
    assert counts["post migration"] == 2
    assert counts["migration checklist"] == 2
    assert counts["migration"] == 1


def test_a_two_letter_word_between_two_terms_is_glued_over():
    counts = indexer.term_counts([
        {"start": 0.0, "duration": 3.0, "text": "Is the schema at risk?"},
    ])
    # Pinning current behaviour. term_counts' comment claims adjacency is taken from
    # the ORIGINAL text so a dropped word never glues two terms together, but words
    # under three letters are removed by the tokenizer before the pairing, so "schema
    # at risk" is indexed as the phrase "schema risk", which nobody said.
    assert counts["schema risk"] == 2
    assert counts["schema"] == 1 and counts["risk"] == 1
