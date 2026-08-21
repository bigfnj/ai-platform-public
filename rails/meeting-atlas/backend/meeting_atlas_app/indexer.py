"""Meeting indexing — turns Meetily recording folders into the rail's payload.

Pure stdlib and pure functions over a directory tree, deliberately: this module is
the part most likely to need a fix at an awkward moment, and it should be testable
without FastAPI, without the broker and without a container.

READ-ONLY. Nothing here writes to the recordings mount. That is not an accident:
the mount is a Windows directory bind-mounted through Podman/Hyper-V, so it is 9p,
and 9p rejects rename-over-an-existing-file. The index lives in memory and is
rebuilt on demand instead.

Three data sources, in precedence order:

  1. ``transcript.enriched.json``  — an external co-work task's re-transcription.
     Wins over Meetily's own, and may carry speaker labels Meetily never produces.
  2. ``summary.json``             — an external task's (or enrich.py's) summary.
     Wins over the Meetily database, and may also supply the meeting title.
  3. the Meetily SQLite           — optional, read-only, mounted. The only place
     Meetily itself keeps the real title and its generated summary.

See INGEST.md for the sidecar contract.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone

# A >=2s hole in the audio is a real pause. Anything shorter is just breathing.
PAUSE_GAP = 2.0
BUCKET_S = 30.0          # activity sparkline resolution
TOP_KEYWORDS = 12

# Filler and function words. The filler list matters more than usual here:
# Parakeet transcribes "uh"/"um" faithfully, so they dominate raw frequency.
STOP = set("""
a about above after again against all also am an and any are arent as at be because been
before being below between both but by can cant cannot could couldnt did didnt do does
doesnt doing dont down during each few for from further had hadnt has hasnt have havent
having he hed hell hes her here heres hers herself him himself his how hows i id ill
im ive if in into is isnt it its itself lets me more most mustnt my myself no nor
not of off on once only or other ought our ours ourselves out over own same shant she shed
shell shes should shouldnt so some such than that thats the their theirs them themselves
then there theres these they theyd theyll theyre theyve this those through to too under
until up very was wasnt we wed well were weve werent what whats when whens where
wheres which while who whos whom why whys with wont would wouldnt you youd youll
youre youve your yours yourself yourselves
uh um uhh umm ah eh oh hmm mmm mm yeah yep yup okay ok alright right sure gonna wanna kinda
sorta like just really actually basically literally maybe probably think know say said says
going get got go went come came make made take took give gave want need see look thing
things stuff lot lots bit way ways time times day days one two three four five good great
thank thanks please now new use used using able let sort kind mean means
will yes part much many every around back first last next also even still something
anything everything someone everyone anyone nothing little big small long short guess
exactly correct totally absolutely definitely put point side end start done another
number set item pretty quite bunch couple thats theyre weve youve isnt arent whether
""".split())

FILLER_ONLY = re.compile(
    r"^(?:uh|um|ah|eh|oh|mm+|hmm+|yeah|yep|yup|ok|okay|right|so|and|but)[\s.,!?]*$", re.I)
WORD = re.compile(r"[a-z][a-z'\-]{2,}")
TS_BRACKET = re.compile(r"\[(\d{1,3}):(\d{2})(?::(\d{2}))?\]")

MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()

SECTION_ALIASES = {
    "summary": "overview", "overview": "overview", "meeting summary": "overview",
    "key decisions": "decisions", "decisions": "decisions",
    "action items": "actions", "actions": "actions", "next steps": "actions",
    "discussion highlights": "highlights", "highlights": "highlights",
    "key points": "highlights", "discussion points": "highlights",
}


# ---------------------------------------------------------------- utilities

def parse_iso(s):
    """Meetily writes 9-digit fractional seconds; fromisoformat wants <=6."""
    if not s:
        return None
    s = re.sub(r"(\.\d{6})\d+", r"\1", str(s).strip())
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def to_local(dt, tz):
    """Render an instant in the display timezone.

    A naive datetime is assumed to already be local — that is the only reading that
    does not silently shift a hand-written sidecar timestamp.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(tz)


def iso_local(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S") if dt else None


def iso_week(d):
    y, w, _ = d.isocalendar()
    return "%d-W%02d" % (y, w)


def slug(s):
    return hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()[:12]


def clock(sec):
    return "%d:%02d" % (int(sec) // 60, int(sec) % 60)


def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", str(s).lower()).strip()


# ---------------------------------------------------------------- meetily sqlite

def read_db(db_path, log=None):
    """Titles and summaries from the Meetily database, keyed by normalised folder path.

    Copies first (including -wal/-shm): the Meetily app holds the DB open in WAL mode,
    so reading it in place is a race. Returns {} rather than raising for every failure
    mode — a missing or locked database degrades this rail to "no titles", never to
    "no rail".
    """
    out = {}
    if not db_path or not os.path.isfile(db_path):
        return out
    tmp = os.path.join(tempfile.gettempdir(), "ma_meetily_snapshot.sqlite")
    try:
        for suffix in ("", "-wal", "-shm"):
            src = db_path + suffix
            if os.path.isfile(src):
                shutil.copy(src, tmp + suffix)
    except OSError as exc:
        if log:
            log.warning("meetily db copy failed (%s); continuing without titles", exc)
        return out
    con = None
    try:
        con = sqlite3.connect("file:%s?mode=ro" % tmp.replace("\\", "/"), uri=True)
        con.row_factory = sqlite3.Row
        summaries = {}
        for row in con.execute(
                "SELECT meeting_id, status, result, processing_time FROM summary_processes"):
            summaries[row["meeting_id"]] = row
        for row in con.execute("SELECT id, title, created_at, folder_path FROM meetings"):
            fp = (row["folder_path"] or "").rstrip("\\/")
            if not fp:
                continue
            sp = summaries.get(row["id"])
            out[os.path.normcase(os.path.basename(fp))] = {
                "db_id": row["id"],
                "title": row["title"],
                "db_created": row["created_at"],
                "summary_md": _extract_markdown(sp["result"]) if sp else None,
                "summary_model": _summary_model(sp["result"]) if sp else None,
                "summary_secs": round(sp["processing_time"], 1)
                                if sp and sp["processing_time"] else None,
            }
    except sqlite3.Error as exc:
        if log:
            log.warning("meetily db unreadable (%s); continuing without titles", exc)
    finally:
        if con is not None:
            con.close()
    return out


def _extract_markdown(result):
    """summary_processes.result is JSON; the markdown hides under english_cache."""
    if not result:
        return None
    try:
        j = json.loads(result)
    except (ValueError, TypeError):
        return result if isinstance(result, str) else None
    for key in ("english_cache", "markdown"):
        node = j.get(key)
        if isinstance(node, dict) and node.get("markdown"):
            return node["markdown"]
        if isinstance(node, str) and node.strip():
            return node
    return None


def _summary_model(result):
    try:
        src = json.loads(result).get("english_cache", {}).get("source", {}) or {}
        name, prov = src.get("model_name"), src.get("model_provider")
        if name and prov:
            return "%s (%s)" % (name, prov)
        return name or prov
    except Exception:
        return None


# ---------------------------------------------------------------- summary parsing

def parse_summary(md, meeting_date, segments):
    """Tolerant parse of a markdown summary into structured sections.

    The markdown is model output, so its shape drifts between runs and between
    models. Anything we fail to classify still reaches the UI as raw markdown —
    parsing is an enhancement, never a gate.
    """
    if not md:
        return None
    out = {"title": None, "overview": "", "decisions": [], "actions": [],
           "highlights": [], "raw": md}
    section = None
    buf, table = [], []

    def flush():
        text = "\n".join(buf).strip()
        if section == "overview" and text:
            out["overview"] = (out["overview"] + "\n\n" + text).strip()
        elif section == "decisions":
            out["decisions"].extend(_bullets(buf))
        elif section == "highlights":
            for b in _bullets(buf):
                m = re.match(r"\*\*(.+?)\*\*[:\s]*(.*)", b, re.S)
                if m:
                    out["highlights"].append({"title": m.group(1).strip(" :"),
                                              "body": m.group(2).strip()})
                else:
                    out["highlights"].append({"title": None, "body": b})
        elif section == "actions":
            out["actions"].extend(_action_table(table, meeting_date, segments))
            out["actions"].extend({"task": b, "owner": None, "due": None}
                                  for b in _bullets(buf))
        del buf[:]
        del table[:]

    for raw in md.splitlines():
        line = raw.rstrip()
        head = re.match(r"^\s*(?:#{1,4}\s*(.+?)|\*\*(.+?)\*\*)\s*:?\s*$", line)
        if head:
            name = (head.group(1) or head.group(2) or "").strip().strip(":").lower()
            if name in SECTION_ALIASES:
                flush()
                section = SECTION_ALIASES[name]
                continue
            if line.lstrip().startswith("#") and not out["title"]:
                out["title"] = (head.group(1) or "").strip()
                continue
        if line.lstrip().startswith("|"):
            table.append(line)
        else:
            buf.append(line)
    flush()
    return out


def _bullets(buf):
    items, cur = [], None
    for line in buf:
        m = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)", line)
        if m:
            if cur:
                items.append(cur.strip())
            cur = m.group(1)
        elif cur is not None and line.strip():
            cur += " " + line.strip()
        elif cur:
            items.append(cur.strip())
            cur = None
    if cur:
        items.append(cur.strip())
    return [i for i in items if i]


def _action_table(rows, meeting_date, segments):
    rows = [r for r in rows if r.strip().startswith("|")]
    if len(rows) < 2:
        return []

    def cells(r):
        return [c.strip() for c in r.strip().strip("|").split("|")]

    header = [h.lower() for h in cells(rows[0])]
    idx = {}
    for i, h in enumerate(header):
        if "owner" in h or "who" in h or "assign" in h:
            idx.setdefault("owner", i)
        elif "task" in h or "action" in h or "item" in h:
            idx.setdefault("task", i)
        elif "due" in h or "date" in h or "deadline" in h:
            idx.setdefault("due", i)
        elif "time" in h or "stamp" in h:
            idx.setdefault("ts", i)
        elif "ref" in h or "segment" in h or "transcript" in h or "quote" in h:
            idx.setdefault("ref", i)
    out = []
    for r in rows[1:]:
        c = cells(r)
        if not c or set("".join(c)) <= set("-: "):
            continue

        def get(k):
            i = idx.get(k)
            return c[i].strip() if i is not None and i < len(c) else ""

        task = get("task") or (c[1] if len(c) > 1 else "")
        if not task:
            continue
        ref, ts_text = get("ref"), get("ts")
        item = {
            "owner": get("owner").strip("*_ ") or None,
            "task": re.sub(r"\s+", " ", task),
            "due": get("due") or None,
            "ref": ref or None,
        }
        claimed = _claimed_seconds(ts_text)
        if claimed is None:
            claimed = _claimed_seconds(ref)
        if claimed is not None:
            item["claimed_at"] = claimed
        quote = _extract_quote(ref)
        if quote:
            item["quote"] = quote
            found = find_quote(quote, segments)
            if found is not None:
                item["quote_at"] = round(found, 1)
                if claimed is not None and abs(found - claimed) > 20:
                    item["ts_mismatch"] = True
            else:
                item["quote_missing"] = True
        if item["due"] and _due_implausible(item["due"], meeting_date):
            item["due_suspect"] = True
        out.append(item)
    return _mark_shared(out)


def _mark_shared(items):
    """Flag the fingerprints of a model filling in blanks rather than reading.

    Two signatures, both observed in real gemma3:4b output: every action item
    carrying the identical due date, and one transcript quote cited as the
    evidence for several unrelated tasks.
    """
    dues = Counter(i["due"] for i in items if i.get("due"))
    quotes = Counter(norm(i["quote"]) for i in items if i.get("quote"))
    for i in items:
        if i.get("due") and dues[i["due"]] >= 3 and len(items) >= 3:
            i["due_uniform"] = True
        if i.get("quote") and quotes[norm(i["quote"])] >= 2:
            i["quote_reused"] = True
    return items


def _claimed_seconds(text):
    if not text:
        return None
    m = TS_BRACKET.search(text)
    if not m:
        m = re.search(r"\b(\d{1,3}):(\d{2})\b", text)
        if not m:
            return None
        return int(m.group(1)) * 60 + int(m.group(2))
    a, b, c = m.group(1), m.group(2), m.group(3)
    if c:
        return int(a) * 3600 + int(b) * 60 + int(c)
    return int(a) * 60 + int(b)


def _extract_quote(ref):
    if not ref:
        return None
    m = re.search(u"[\"“‘']([^\"”’']{6,})[\"”’']", ref)
    if m:
        return m.group(1).strip()
    stripped = TS_BRACKET.sub("", ref).strip(" *_|")
    return stripped if len(stripped) >= 8 else None


def find_quote(quote, segments):
    """Locate a cited quote in the real transcript.

    A model citing a timestamp it did not derive from the text is the norm, not the
    exception, so the quote itself is the only trustworthy anchor. Returns audio
    seconds, or None when the quote appears nowhere.
    """
    q = norm(quote)
    if len(q) < 8:
        return None
    for s in segments:
        if q in norm(s["text"]):
            return s["start"]
    words = q.split()
    if len(words) >= 4:
        probe = " ".join(words[:4])
        for s in segments:
            if probe in norm(s["text"]):
                return s["start"]
    return None


def _due_implausible(due, meeting_date):
    """A due date in the past or absurdly far out is more likely invented than planned."""
    if not meeting_date:
        return False
    text = due.lower()
    if re.search(r"\b(tbd|asap|ongoing|n/?a|none|immediate)\b", text):
        return False
    base = meeting_date.replace(tzinfo=None)
    m = re.search(r"\b(" + "|".join(MONTHS) + r")[a-z]*\.?\s+(\d{1,2})\b", text)
    if m:
        month, day = MONTHS.index(m.group(1)) + 1, int(m.group(2))
        try:
            when = datetime(base.year, month, day)
        except ValueError:
            return False
        if (when - base).days < -180:
            try:
                when = when.replace(year=base.year + 1)
            except ValueError:
                return False
    else:
        m2 = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
        if not m2:
            return False
        try:
            when = datetime(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
        except ValueError:
            return False
    delta = (when.date() - base.date()).days
    return delta < -1 or delta > 120


# ---------------------------------------------------------------- metrics

def derive(segments, duration_s):
    spoken = sum(s["duration"] for s in segments)
    words = sum(len(s["text"].split()) for s in segments)
    pauses, longest_gap, gap_total = 0, 0.0, 0.0
    for a, b in zip(segments, segments[1:]):
        gap = b["start"] - (a["start"] + a["duration"])
        if gap > PAUSE_GAP:
            pauses += 1
            gap_total += gap
            longest_gap = max(longest_gap, gap)
    span = duration_s or spoken or 1.0
    nb = max(1, int(math.ceil(span / BUCKET_S)))
    activity = [0] * nb
    for s in segments:
        activity[min(nb - 1, int(s["start"] // BUCKET_S))] += len(s["text"].split())

    # Per-speaker talk time, only when a sidecar actually supplied speakers. Meetily
    # itself never does, and inventing them from silence gaps would be a guess wearing
    # the costume of a measurement.
    speakers = {}
    for s in segments:
        who = (s.get("speaker") or "").strip()
        if who:
            e = speakers.setdefault(who, {"speaker": who, "seconds": 0.0, "words": 0,
                                          "turns": 0})
            e["seconds"] += s["duration"]
            e["words"] += len(s["text"].split())
    if speakers:
        prev = None
        for s in segments:
            who = (s.get("speaker") or "").strip()
            if who and who != prev:
                speakers[who]["turns"] += 1
            prev = who or prev
        for e in speakers.values():
            e["seconds"] = round(e["seconds"], 1)
            e["share"] = round(e["seconds"] / spoken, 3) if spoken else None

    return {
        "spoken_s": round(spoken, 1),
        "words": words,
        "wpm": round(words / (spoken / 60.0), 1) if spoken > 30 else 0,
        "density": round(spoken / duration_s, 3) if duration_s else None,
        "n_segments": len(segments),
        "pauses": pauses,
        "pause_s": round(gap_total, 1),
        "longest_gap_s": round(longest_gap, 1),
        "longest_seg_s": round(max((s["duration"] for s in segments), default=0.0), 1),
        "questions": sum(1 for s in segments if "?" in s["text"]),
        "activity": activity,
        "speakers": sorted(speakers.values(), key=lambda e: -e["seconds"]),
    }


def term_counts(segments):
    """Unigrams plus content-word bigrams.

    Bigrams earn their place: "cut over", "account transfer" and "post migration" are
    the actual subject matter, while the unigrams they decompose into are generic.
    """
    c = Counter()
    for s in segments:
        text = s["text"].strip()
        if FILLER_ONLY.match(text):
            continue
        raw = [w.strip("'-") for w in WORD.findall(text.lower())]
        keep = [w for w in raw if len(w) > 2 and w.replace("'", "") not in STOP]
        for w in keep:
            c[w] += 1
        # Adjacent in the ORIGINAL text, so a dropped stopword never glues two
        # unrelated words into a phrase nobody said.
        for a, b in zip(raw, raw[1:]):
            if a in keep and b in keep and a != b:
                c[a + " " + b] += 2
    return c


def keywords(counts, doc_freq, n_docs, k=TOP_KEYWORDS):
    """TF-IDF, smoothed so it degrades gracefully to plain TF at n_docs=1."""
    total = sum(counts.values()) or 1
    scored = []
    for term, n in counts.items():
        if n < 2:
            continue
        idf = math.log((n_docs + 1.0) / (doc_freq.get(term, 0) + 1.0)) + 1.0
        scored.append((term, (n / float(total)) * idf, n))
    scored.sort(key=lambda t: -t[1])
    return [{"t": t, "n": n} for t, _, n in scored[:k]]


# ---------------------------------------------------------------- loading

def _norm_segments(raw, key_start, key_dur, key_end):
    """Normalise either transcript shape into {start, duration, text, speaker?}."""
    out = []
    for s in raw:
        if not isinstance(s, dict):
            continue
        text = s.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        try:
            start = float(s.get(key_start) or 0.0)
        except (TypeError, ValueError):
            start = 0.0
        dur = s.get(key_dur)
        if dur is None and key_end and s.get(key_end) is not None:
            try:
                dur = float(s[key_end]) - start
            except (TypeError, ValueError):
                dur = None
        try:
            dur = max(0.0, float(dur))
        except (TypeError, ValueError):
            dur = 0.0
        item = {"start": start, "duration": dur, "text": text.strip()}
        who = s.get("speaker")
        if isinstance(who, str) and who.strip():
            item["speaker"] = who.strip()
        out.append(item)
    out.sort(key=lambda s: s["start"])
    return out


def _read_json(path, log=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError) as exc:
        if log:
            log.warning("unreadable %s (%s)", path, exc)
        return None


def load_folder(folder, log=None):
    """One meeting folder -> its raw pieces, or None when there is no transcript."""
    enriched = os.path.join(folder, "transcript.enriched.json")
    plain = os.path.join(folder, "transcripts.json")

    segments, tsource, tmodel = None, None, None
    if os.path.isfile(enriched):
        j = _read_json(enriched, log)
        if isinstance(j, dict) and isinstance(j.get("segments"), list):
            segments = _norm_segments(j["segments"], "start", "duration", "end")
            if segments:
                tsource = j.get("source") or "transcript.enriched.json"
                tmodel = j.get("model")
    if not segments and os.path.isfile(plain):
        j = _read_json(plain, log)
        if isinstance(j, dict) and isinstance(j.get("segments"), list):
            segments = _norm_segments(j["segments"], "audio_start_time", "duration",
                                      "audio_end_time")
            tsource = "Meetily"
    if not segments:
        return None

    meta = _read_json(os.path.join(folder, "metadata.json"), log) or {}
    if not isinstance(meta, dict):
        meta = {}

    side = None
    sp = os.path.join(folder, "summary.json")
    if os.path.isfile(sp):
        j = _read_json(sp, log)
        if isinstance(j, dict) and (j.get("markdown") or "").strip():
            model, prov = j.get("model"), j.get("provider")
            side = {
                "summary_md": j["markdown"],
                "summary_model": ("%s (%s)" % (model, prov)) if model and prov else model,
                "summary_secs": j.get("elapsed_s"),
                "title": (j.get("title") or "").strip() or None,
                "from_sidecar": True,
            }
    return {"folder": folder, "segments": segments, "meta": meta, "sidecar": side,
            "transcript_source": tsource, "transcript_model": tmodel}


def build_index(recordings_dir, db_path=None, tz=None, log=None):
    """Scan the recordings tree and return the full payload the API serves.

    Returns {"corpus": {...}, "meetings": [...], "details": {id: {...}}}. The
    details map is kept separate so the list endpoint stays small.
    """
    tz = tz or timezone.utc
    if not recordings_dir or not os.path.isdir(recordings_dir):
        return {"corpus": {"n_meetings": 0, "total_s": 0.0, "total_words": 0,
                           "recordings_root": recordings_dir or "",
                           "available": False, "themes": [],
                           "generated_at": iso_local(datetime.now(tz)),
                           "first": None, "last": None},
                "meetings": [], "details": {}}

    db = read_db(db_path, log)
    loaded = []
    try:
        names = sorted(os.listdir(recordings_dir))
    except OSError as exc:
        if log:
            log.error("cannot list %s (%s)", recordings_dir, exc)
        names = []
    for name in names:
        folder = os.path.join(recordings_dir, name)
        if not os.path.isdir(folder):
            continue
        m = load_folder(folder, log)
        if m:
            loaded.append(m)

    # First pass: document frequency, so IDF has something to work with.
    doc_freq = Counter()
    for m in loaded:
        m["_counts"] = term_counts(m["segments"])
        for t in m["_counts"]:
            doc_freq[t] += 1
    n_docs = len(loaded)

    meetings, details = [], {}
    for m in loaded:
        folder, segs, meta = m["folder"], m["segments"], m["meta"]
        base = os.path.basename(folder)
        rec = dict(db.get(os.path.normcase(base), {}))
        if m["sidecar"]:
            rec.update({k: v for k, v in m["sidecar"].items() if v is not None})

        # metadata.json created_at is the canonical instant. The folder name's leading
        # timestamp is LOCAL while segment display_time is UTC; using either naively
        # puts every meeting hours off.
        start = parse_iso(meta.get("created_at")) or parse_iso(rec.get("db_created"))
        if not start:
            hit = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})", base)
            if hit:
                start = parse_iso("%sT%s:%s:%s" % (hit.group(1), hit.group(2),
                                                   hit.group(3), hit.group(4)))
        if not start:
            try:
                start = datetime.fromtimestamp(os.path.getmtime(folder), tz)
            except OSError:
                start = datetime.now(tz)
        local = to_local(start, tz)

        try:
            duration = float(meta.get("duration_seconds") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        if not duration:
            duration = max((s["start"] + s["duration"] for s in segs), default=0.0)
        end_local = local + timedelta(seconds=duration)
        mid = rec.get("db_id") or ("folder-" + slug(base))

        met = derive(segs, duration)
        summary = parse_summary(rec.get("summary_md"), local, segs)
        actions = (summary or {}).get("actions", [])
        audio = meta.get("audio_file")
        if not audio and os.path.isfile(os.path.join(folder, "audio.mp4")):
            audio = "audio.mp4"
        title = (rec.get("title") or "").strip()

        row = {
            "id": mid,
            "title": title or (summary or {}).get("title")
                     or meta.get("meeting_name") or base,
            "auto_title": meta.get("meeting_name") or base,
            "titled": bool(title),
            "folder": base,
            "date": local.strftime("%Y-%m-%d"),
            "week": iso_week(local),
            "month": local.strftime("%Y-%m"),
            "dow": local.weekday(),
            "start": iso_local(local),
            "end": iso_local(end_local),
            "start_min": local.hour * 60 + local.minute,
            "duration_s": round(duration, 1),
            "has_summary": bool(rec.get("summary_md")),
            "summary_model": rec.get("summary_model"),
            "summary_source": "sidecar" if rec.get("from_sidecar") else
                              ("Meetily" if rec.get("summary_md") else None),
            "transcript_source": m["transcript_source"],
            "transcript_model": m["transcript_model"],
            "n_decisions": len((summary or {}).get("decisions", [])),
            "n_actions": len(actions),
            "n_highlights": len((summary or {}).get("highlights", [])),
            "owners": sorted({a["owner"] for a in actions if a.get("owner")}),
            "flags": sum(1 for a in actions if a.get("due_suspect")
                         or a.get("ts_mismatch") or a.get("quote_missing")
                         or a.get("due_uniform") or a.get("quote_reused")),
            "keywords": keywords(m["_counts"], doc_freq, n_docs),
            "has_audio": bool(audio),
            # Carried in the list payload, not just the detail, so the day / week /
            # month roll-ups render without pulling every transcript.
            "overview": ((summary or {}).get("overview") or "")[:700],
            "decisions": (summary or {}).get("decisions", []),
            "actions": actions,
        }
        for k in ("spoken_s", "words", "wpm", "density", "n_segments", "pauses",
                  "pause_s", "longest_gap_s", "longest_seg_s", "questions",
                  "activity", "speakers"):
            row[k] = met[k]
        meetings.append(row)

        details[mid] = {
            "id": mid,
            "segments": [[round(s["start"], 2), round(s["duration"], 2), s["text"],
                          s.get("speaker")] for s in segs],
            "summary": summary,
            "folder": base,
            "audio": audio,
            "devices": meta.get("devices") or {},
            "meta": meta,
        }

    meetings.sort(key=lambda r: r["start"], reverse=True)
    all_counts = Counter()
    for m in loaded:
        all_counts.update(m["_counts"])
    corpus = {
        "generated_at": iso_local(datetime.now(tz)),
        "recordings_root": recordings_dir,
        "available": True,
        "n_meetings": len(meetings),
        "total_s": round(sum(r["duration_s"] for r in meetings), 1),
        "total_words": sum(r["words"] for r in meetings),
        "first": meetings[-1]["date"] if meetings else None,
        "last": meetings[0]["date"] if meetings else None,
        "themes": keywords(all_counts, doc_freq, n_docs, 24),
        "n_flagged": sum(r["flags"] for r in meetings),
        "n_summarised": sum(1 for r in meetings if r["has_summary"]),
        "n_enriched": sum(1 for r in meetings
                          if r["transcript_source"] not in (None, "Meetily")),
    }
    return {"corpus": corpus, "meetings": meetings, "details": details}
