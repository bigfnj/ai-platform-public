"""Broker-backed media facade — the shared seam that puts edu-suite on the platform.

Routes translate / image / tts through the platform **GPU/Model Broker**
(http://127.0.0.1:11500) instead of loading models locally. The broker is the
single owner of the GPU and enforces one-heavy-model-at-a-time across every app.

Lives in edu_media_core so BOTH layers can use it without inverting dependencies:
the dashboard workflows AND the lower-level cvc-worksheets package already import
edu_media_core. (No import cycle: the broker's own media worker imports the local
runners — tts/images/translate — never this client.)

The primitive/workflow split lives here: the broker exposes only *generic*
primitives (chat, image(prompt), tts(segments)), so edu-suite's opinionated bits
(clipart prompt template, JSON translate prompts, content-hash caching) stay in
this module. These functions are drop-in replacements for the edu_media_core
runners the workflows previously called, so callers change by a single import swap.

Synchronous (``requests``) on purpose: workflow steps run in the per-job
subprocess and are plain sync code. Broker URL / model are env-overridable.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

import requests

from . import cache as _cache

log = logging.getLogger(__name__)

BROKER_URL = os.getenv("BROKER_URL", "http://127.0.0.1:11500").rstrip("/")


def _broker_token() -> str:
    """The broker's shared control-plane token: BROKER_AUTH_TOKEN_FILE (a path, contents
    stripped) wins over BROKER_AUTH_TOKEN (the literal). The file form keeps the secret out
    of `docker inspect`, crash dumps and /proc/<pid>/environ. A configured-but-unreadable
    file yields "" — no header, so the broker 401s visibly — rather than silently falling
    back to the environment the deployment believes it has moved off. See RAIL_CONTRACT.md.
    """
    path = os.environ.get("BROKER_AUTH_TOKEN_FILE", "").strip()
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return ""
    return os.environ.get("BROKER_AUTH_TOKEN", "").strip()


# Broker control-plane token (shared secret); empty => no header (dev / broker not enforcing).
_TOK = _broker_token()
_AUTH = {"Authorization": f"Bearer {_TOK}"} if _TOK else {}
# The @edu role, resolved by the broker — currently to the size-scoped wildcard
# "mistral-small3*:24b", so this is the same model as before, just chosen in one place.
# Pinning the glob here instead meant the admin could repoint @edu in Admin -> Rails and
# this rail would quietly keep using the old model with nothing reporting the disagreement.
# Override with EDU_LLM_MODEL (another @role, a concrete name, or a size-scoped glob).
LLM_MODEL = os.getenv("EDU_LLM_MODEL", "@edu")
_TIMEOUT = float(os.getenv("BROKER_TIMEOUT", "1200"))
# Audio is synthesized in sub-batches of this many clips per broker request. A
# single request only returns when its whole batch is done, so an unbounded batch
# on a large document blows the read timeout above; capping keeps each request short
# (XTTS still loads once per request). Override with TTS_BATCH_SIZE.
def _env_int(name: str, default: int, floor: int = 0) -> int:
    """Coerce an env var at import time, but never RAISE at import time.

    A typo'd value in deploy/.env otherwise fails `import broker_media`, which fails every
    rail module that imports it — so the workflow vanishes from /api/workflows rather than
    reporting a bad setting. app.py already wraps its retention vars this way.
    """
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return max(floor, int(raw))
    except ValueError:
        log.warning("%s=%r is not a number; using the default of %d", name, raw, default)
        return default


# TWO caps on a TTS sub-batch, because they bound different things and a single number
# cannot do both. The COUNT cap bounds the RESPONSE: every clip comes back as base64 wav,
# about 180 KB for a median sentence, so 96 clips is ~17 MB read into memory and decoded —
# in the same range as the 15 MB the broker already caps a transcribe body at, and the
# reason this is not simply set to "all of them".
_TTS_BATCH = _env_int("TTS_BATCH_SIZE", 96, floor=1)

# The COST cap bounds TIME. Measured on the live broker 2026-09-16, es_MX, six same-length
# clips per request with the per-request load divided out:
#
#     model load, per request                     34.0s
#     56-char clip  (the rail's median)            1.93s
#     112-char clip (its p95)                      4.50s
#     230-char clip (near its 341-char max)        9.29s
#
# so the marginal cost is close to linear in characters at ~0.040 s/char. Rounded up here.
#
# WHY A FIXED COUNT CANNOT BE MADE SAFE, which is what these replace. 48 clips of dialogue
# is ~90s and 48 clips of glossary text is ~480s, and only one of those fits a 480s read
# timeout. The old fixed 48 was therefore already on the edge for a glossary-heavy book at
# the DEFAULT timeout, and a timeout there costs the whole sub-batch because _post is called
# with retry_on_timeout=False. Packing by estimated cost makes the batch small when the text
# is long and large when it is short, which is both safer and faster than either constant.
_TTS_LOAD_S = 40.0            # 34.0s measured, plus margin for a genuinely cold first load
_TTS_S_PER_CHAR = 0.045       # 0.040 measured; rounded up
_TTS_CLIP_FLOOR_S = 1.0       # a one-word clip is not free
# Fill only part of the read timeout. The estimate above is a model, the box is shared with
# whatever else is resident, and overrunning costs the entire sub-batch.
_TTS_BUDGET_FRACTION = 0.6


def _tts_clip_cost_s(text: str) -> float:
    return max(_TTS_CLIP_FLOOR_S, len(text) * _TTS_S_PER_CHAR)


def tts_batches(items: list[dict], *, timeout: float | None = None,
                count_cap: int | None = None) -> list[list[dict]]:
    """Split ``items`` into sub-batches that respect BOTH caps.

    A batch is closed when adding the next clip would push the estimated cost past the
    time budget, or when it reaches the count cap. Never returns an empty batch: one clip
    whose own estimate exceeds the whole budget still goes out alone, because refusing it
    would drop a sentence from a student's book, and the broker's own ceiling is the
    backstop for a clip that really is too long.
    """
    budget = max(_TTS_CLIP_FLOOR_S,
                 (timeout if timeout is not None else _MEDIA_TIMEOUT)
                 * _TTS_BUDGET_FRACTION - _TTS_LOAD_S)
    cap = count_cap if count_cap is not None else _TTS_BATCH
    out: list[list[dict]] = []
    cur: list[dict] = []
    spent = 0.0
    for it in items:
        cost = _tts_clip_cost_s(it.get("text") or "")
        if cur and (len(cur) >= cap or spent + cost > budget):
            out.append(cur)
            cur, spent = [], 0.0
        cur.append(it)
        spent += cost
    if cur:
        out.append(cur)
    return out
# Media/batch calls (TTS, image) can wait in the broker's queue behind another client, so
# they retry with backoff — but ONLY on a connection error, never on a timeout (see _post).
#
# ⚠ The 480 default below is now wrong for any deployment that talks to this broker, and is
# kept only so an un-configured caller still works. The broker's own media_timeout is 1200s,
# so 480 made the CLIENT give up first: the worker kept rendering, the retry queued behind the
# job still holding the GPU, and a picture run needing ~660s burned 967s and produced nothing.
# deploy/docker-compose.yml sets BROKER_MEDIA_TIMEOUT=1320 for the rails that use this module,
# i.e. above the broker's ceiling, so the broker's own 502 always arrives first. The original
# intent — fail fast against a wedge rather than the full chat timeout × retries — still holds,
# but it has to come from the broker's error, not from the client undercutting it.
# Interactive chat keeps retries=0 / the long _TIMEOUT and fails fast on its own terms.
_MEDIA_RETRIES = _env_int("BROKER_MEDIA_RETRIES", 1)
_MEDIA_BACKOFF = float(os.getenv("BROKER_MEDIA_BACKOFF", "5"))
_MEDIA_TIMEOUT = float(os.getenv("BROKER_MEDIA_TIMEOUT", "480"))

# edu-suite's clipart look (moved here from edu_media_core.images; the broker's
# image primitive is deliberately template-free).
_IMG_PROMPT = (
    "simple flat cartoon illustration of {subject}, childrens book clip art, "
    "bold clean outlines, bright flat colors, plain solid white background, "
    "centered, single object, no text, no words"
)
_IMG_NEGATIVE = (
    "text, words, letters, watermark, signature, photo, realistic, blurry, "
    "cluttered, multiple objects"
)


class BrokerUnavailable(RuntimeError):
    """The broker could not be reached — surfaced with a clear operator message."""


class BrokerTimeout(BrokerUnavailable):
    """The broker accepted the connection but did not respond in time (busy GPU or
    an over-large request) — distinct from 'not running'. Subclasses
    BrokerUnavailable so existing handlers still catch it."""

# Canonical facade name. Every rail's broker facade exposes roles/models/status and a
# BrokerError, which is what lets modelstate.py be byte-identical across rails instead of
# each one adapting to its own exception name. This module predates that contract, so the
# original name stays as the raised type and BrokerError aliases it.
class BrokerBadJSON(ValueError):
    """The broker answered, but the model's JSON could not be parsed even after a retry.

    Distinct from BrokerUnavailable on purpose: nothing is wrong with the broker or the
    GPU, so retrying the whole job is pointless — this ONE request produced garbage and
    the caller should decide whether to skip the item or fail. It carries the raw text,
    because `json.loads` alone reports the parse position and never what was being parsed,
    which is what made this class of failure so hard to see.
    """

    def __init__(self, message: str, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw


class BrokerHTTPError(BrokerUnavailable):
    """The broker answered with an HTTP error.

    Subclasses BrokerUnavailable so `except BrokerError` catches it, which it previously did
    NOT: this was a bare RuntimeError, and the broker turns EVERY backend fault into a 502 —
    a media worker that died, a model pull in flight, Ollama restarting, a 401 after a token
    rotation. So the degrade handlers written specifically for broker faults did not fire for
    the most common broker fault there is. `status` is kept for callers that want to tell a
    502 (the backend broke) from a 401 (the token is wrong) without parsing the message.
    """

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


BrokerError = BrokerUnavailable
def _post(path: str, payload: dict, *, retries: int = 0, backoff: float = _MEDIA_BACKOFF,
          timeout: float = _TIMEOUT, retry_on_timeout: bool = True) -> dict:
    """POST to the broker.

    Retries up to ``retries`` times with linear backoff on a connection error (the broker
    briefly restarting), and on a timeout only when ``retry_on_timeout`` is true. HTTP error
    responses are never retried — they are the broker's considered answer, and it returns 502
    for every backend fault. ``retries=0`` (default) fails fast for interactive calls.

    Media/batch calls pass ``retries=_MEDIA_RETRIES``, ``timeout=_MEDIA_TIMEOUT`` and
    ``retry_on_timeout=False``: their ceiling sits above the broker's own, so a timeout means
    the broker never answered while the worker it spawned may still hold the GPU, and retrying
    that queues a second identical job behind the first."""
    attempt = 0
    while True:
        try:
            r = requests.post(f"{BROKER_URL}{path}", json=payload, timeout=timeout, headers=_AUTH)
        except requests.Timeout as exc:
            # Media calls pass retry_on_timeout=False. Their client ceiling now sits ABOVE the
            # broker's own media_timeout, so a timeout no longer means "slow" — it means the
            # broker never answered at all, and the worker it spawned may still be holding the
            # GPU. Retrying then queues a second identical job behind the first and doubles an
            # already long wait: at 1320s that is ~44 minutes to learn the same thing twice.
            # Connection errors below still retry, because a briefly restarting broker is the
            # case a retry genuinely fixes.
            if attempt < retries and retry_on_timeout:
                attempt += 1
                time.sleep(backoff * attempt)
                continue
            raise BrokerTimeout(
                f"GPU/Model Broker at {BROKER_URL} did not respond within {timeout:.0f}s "
                f"on {path} ({exc}), even after {retries} retr{'y' if retries == 1 else 'ies'}. "
                "It may be wedged or overloaded; check the broker, then re-run."
            ) from exc
        except requests.RequestException as exc:
            if attempt < retries:
                attempt += 1
                time.sleep(backoff * attempt)
                continue
            raise BrokerUnavailable(
                f"GPU/Model Broker unreachable at {BROKER_URL} ({exc}). "
                "Start the broker (uvicorn app.main:app --app-dir services/broker --port 11500)."
            ) from exc
        if r.status_code >= 400:
            raise BrokerHTTPError(f"broker {path} -> {r.status_code}: {r.text[:500]}",
                                  status=r.status_code)
        return r.json()


# --- broker reads (roles / models / status, for the header chips) -----------
# The rail header polls these to render the four-state model chips (see
# dashboard.modelstate). They are read-only and must degrade to a clear
# "broker unreachable" (BrokerUnavailable) rather than raising something the
# resolver doesn't catch — so a header keeps rendering while the GPU layer is down.

def _get(path: str, *, timeout: float = _TIMEOUT):
    """GET a broker read endpoint. Raises BrokerUnavailable on ANY failure (down,
    timeout, or an error status) so the header resolver's single ``except
    BrokerUnavailable`` covers every case. Reuses BROKER_URL/_AUTH/_TIMEOUT."""
    try:
        r = requests.get(f"{BROKER_URL}{path}", timeout=timeout, headers=_AUTH)
    except requests.Timeout as exc:
        raise BrokerTimeout(
            f"GPU/Model Broker at {BROKER_URL} did not respond within {timeout:.0f}s on {path} ({exc})."
        ) from exc
    except requests.RequestException as exc:
        raise BrokerUnavailable(
            f"GPU/Model Broker unreachable at {BROKER_URL} ({exc})."
        ) from exc
    if r.status_code >= 400:
        raise BrokerUnavailable(f"broker {path} -> {r.status_code}: {r.text[:500]}")
    return r.json()


def roles() -> list[dict]:
    """The broker's role table (each role + the concrete model it resolves to). Lets the
    header name the actual model behind a @role (e.g. @edu -> mistral-small3.2:24b).
    Tolerant of a ``{"roles": [...]}`` envelope vs. a bare list."""
    resp = _get("/v1/roles")
    if isinstance(resp, dict) and isinstance(resp.get("roles"), list):
        return resp["roles"]
    return resp if isinstance(resp, list) else []


def models() -> list[dict]:
    """The models the broker can serve (Ollama tags) — used to decide whether a resolved
    model is installed. Tolerant of a ``{"models": [...]}`` envelope vs. a bare list."""
    resp = _get("/v1/models")
    if isinstance(resp, dict) and isinstance(resp.get("models"), list):
        return resp["models"]
    return resp if isinstance(resp, list) else []


def status() -> dict:
    """Broker/GPU status passthrough (loaded models + the live jobs queue), used to tell
    a resident model (loaded) from one a job is waiting on (warming)."""
    resp = _get("/v1/status")
    return resp if isinstance(resp, dict) else {}


# --- translate (JSON-mode chat + content-hash caching) ----------------------
# Same mechanic as edu_media_core.translate, but the chat goes through the broker.

# Cache mechanics live in edu_media_core.cache (one shared, content-addressed store);
# these are back-compat re-exports.
content_hash = _cache.content_hash


def load_cache(path: str | Path) -> dict:
    return _cache.load(path)


def save_cache(path: str | Path, data: dict) -> None:
    _cache.save(path, data)


def clear_cache(path: str | Path | None = None) -> None:
    _cache.clear(path)


def chat_json(system_prompt: str, user_message: str, *,
              model: str = LLM_MODEL, options: dict | None = None,
              images: list[str] | None = None) -> dict:
    """One JSON-mode chat turn through the broker; returns the parsed dict. Pass
    ``images`` (base64 PNG/JPEG) to run a vision-capable model over an image, e.g.
    an image-only worksheet with no extractable text."""
    user_msg: dict = {"role": "user", "content": user_message}
    if images:
        user_msg["images"] = images

    def call() -> str:
        data = _post("/v1/chat", {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                user_msg,
            ],
            "format": "json",
            "options": options or {},
            "keep_alive": "30m",
        })
        return data["message"]["content"]

    # A local model can degenerate on a short or garbled input: it opens the JSON string,
    # never closes it, and loops on filler until the generation cap. Observed live on a
    # 30-character OCR fragment containing a curly quote, which returned ~3,900 characters
    # of `{"es": "…}```.```.```…`. A bare json.loads here raised straight through the
    # caller and destroyed a 59-page book over one bad page, and the traceback never showed
    # the text that failed to parse. So: try once, retry once (sampling differs, and the
    # second attempt usually lands), then hand the caller a typed error carrying the raw.
    raw = call()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    retry = call()
    try:
        return json.loads(retry)
    except json.JSONDecodeError as exc:
        raise BrokerBadJSON(
            f"the model returned malformed JSON twice ({exc}); "
            f"first {len(raw)} chars, retry {len(retry)} chars",
            raw=retry[:400],
        ) from exc


def translate_cached(*, cache_path: str | Path | None = None, cache_key: str | None = None,
                     system_prompt: str, user_message: str,
                     model: str = LLM_MODEL, options: dict | None = None,
                     required_keys: tuple[str, ...] = ()) -> dict:
    """Cached translation; queries the broker on a miss. Defaults to the shared suite
    cache with a content-addressed key from ``(model, system_prompt, user_message)`` so
    identical requests are reused across apps + restarts. Pass ``cache_path``/``cache_key``
    to override. Raises ValueError if any ``required_keys`` is missing from the output."""
    path = cache_path or _cache.translations_path()
    key = cache_key or _cache.make_key(model, system_prompt, user_message)
    store = _cache.load(path)
    if key in store:
        return store[key]
    result = chat_json(system_prompt, user_message, model=model, options=options)
    missing = [k for k in required_keys if k not in result]
    if missing:
        raise ValueError(f"Translation output missing {missing}: {result}")
    store[key] = result
    _cache.save(path, store)
    return result


# --- image (SDXL-Turbo via the broker) --------------------------------------

def generate_image(subject: str, out_path: str | Path, *,
                   force: bool = False, steps: int = 4, size: int = 512) -> Path | None:
    """Generate a clipart illustration of ``subject`` (matches the old
    edu_media_core.images.generate_image contract). Returns the path, the existing
    path if present, or None on failure."""
    out_path = Path(out_path)
    if out_path.exists() and not force:
        return out_path
    data = _post("/v1/image", {
        "prompts": [_IMG_PROMPT.format(subject=subject)],
        "negative_prompt": _IMG_NEGATIVE,
        "steps": steps,
        "size": size,
    }, retries=_MEDIA_RETRIES, timeout=_MEDIA_TIMEOUT, retry_on_timeout=False)
    imgs = data.get("images") or []
    if not imgs or not imgs[0]:
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(base64.b64decode(imgs[0]))
    return out_path


def generate_images(subjects: list[str], out_paths: list[str | Path], *,
                    steps: int = 4, size: int = 512) -> list[Path | None]:
    """Batch image generation: ONE broker call for all subjects, so SDXL loads once
    for the whole set instead of once per image. Returns a list of Path|None aligned
    with ``subjects``/``out_paths`` (None where that image failed)."""
    prompts = [_IMG_PROMPT.format(subject=s) for s in subjects]
    data = _post("/v1/image", {"prompts": prompts, "negative_prompt": _IMG_NEGATIVE,
                               "steps": steps, "size": size}, retries=_MEDIA_RETRIES,
                 timeout=_MEDIA_TIMEOUT, retry_on_timeout=False)
    imgs = data.get("images") or []
    # The worker records WHY each prompt failed and every client used to drop it, so a batch
    # where all 48 failed (CUDA OOM, a malformed prompt) was indistinguishable from a batch of
    # 48 undrawable subjects: same 200, same list of nulls, no reason anywhere. Log it, and
    # shout when nothing at all came back, because that is a broken run and not a quiet one.
    errs = [e for e in (data.get("errors") or []) if e]
    if errs:
        got = sum(1 for b in imgs if b)
        (log.error if got == 0 else log.warning)(
            "broker /v1/image: %d of %d prompt(s) failed%s. First: %s",
            len(errs), len(prompts), " — NONE succeeded" if got == 0 else "", str(errs[0])[:200])
    results: list[Path | None] = []
    for i, out_path in enumerate(out_paths):
        b64 = imgs[i] if i < len(imgs) else None
        if not b64:
            results.append(None)
            continue
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(base64.b64decode(b64))
        results.append(out_path)
    return results


# --- audio (XTTS v2 via the broker) -----------------------------------------

def synthesize_wav(text: str, lang: str, out_path: str | Path) -> Path:
    """Synthesize one segment to a WAV file (replaces the local
    synthesize_segment + save_wav pair used by the cvc workflow)."""
    data = _post("/v1/tts", {"segments": [{"lang": lang, "text": text}]},
                 retries=_MEDIA_RETRIES, timeout=_MEDIA_TIMEOUT, retry_on_timeout=False)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(base64.b64decode(data["audio_b64"]))
    return out_path


def synthesize_wavs(
    items: list[dict],
    out_paths: list[str | Path],
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[Path]:
    """Batch audio, synthesized in sub-batches sized by ``tts_batches`` (XTTS loads once
    per request, ~34s, so fewer requests is faster; one giant request would only return
    when the whole set is done and trips the read timeout). Sub-batches are therefore NOT
    a uniform size: they are packed by estimated cost, so a page of glossary definitions
    goes out in a smaller request than a page of dialogue. ``items`` are
    ``{"lang","text"}``; writes each clip to the matching ``out_paths`` entry and returns
    them aligned. ``on_progress(done, total)`` fires after each sub-batch."""
    if len(out_paths) != len(items):
        raise ValueError(f"out_paths ({len(out_paths)}) must align with items ({len(items)})")
    total = len(items)
    written: list[Path] = []
    # done, not a stride: sub-batches are no longer a uniform size, so out_paths cannot be
    # indexed from a fixed step. Getting this wrong would write every clip past the first
    # short batch to the WRONG path, which is a book read aloud in the wrong order.
    done = 0
    for chunk in tts_batches(items):
        data = _post("/v1/tts_batch",
                     {"items": [{"lang": it["lang"], "text": it["text"]} for it in chunk]},
                     retries=_MEDIA_RETRIES, timeout=_MEDIA_TIMEOUT, retry_on_timeout=False)
        audios = data.get("audios") or []
        if len(audios) != len(chunk):
            raise RuntimeError(
                f"broker /v1/tts_batch returned {len(audios)} clip(s) for a batch of {len(chunk)}"
            )
        for j, b64 in enumerate(audios):
            out_path = Path(out_paths[done + j])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            # Temp-then-replace, not a bare write_bytes. A cancelled job (the queue
            # terminates, then kills after 5s) landing mid-write leaves a TRUNCATED wav at a
            # path whose only test anywhere is `.exists()`. Today such a corpse dies with the
            # per-job directory, which is why this was survivable — but a shared audio cache
            # would serve it to every future unit for ever, while the stage line reported
            # success. Same discipline as cache.save and the picture cache's .part files.
            tmp = out_path.with_name(f"{out_path.name}.{os.getpid()}.part")
            try:
                tmp.write_bytes(base64.b64decode(b64))
                os.replace(tmp, out_path)
            finally:
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
            written.append(out_path)
        done += len(chunk)
        if on_progress:
            on_progress(done, total)
    return written
