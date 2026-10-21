"""Async client apps use to reach the GPU/Model Broker.

This is the ONLY sanctioned way for a platform app to do model work. Apps must
not import Ollama SDKs or hit ``localhost:11434`` themselves; routing everything
through the broker is what enforces the one-heavy-model VRAM policy.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

#: BrokerClient.set_cli_role's `model` when the call leaves the role's Claude model as it is.
_KEEP_MODEL: Any = object()


def broker_token() -> str:
    """The broker's shared control-plane token. Two spellings, highest precedence first.

    ``BROKER_AUTH_TOKEN_FILE`` — a PATH whose contents are the token. This is the preferred
    form: the secret never enters the container's environment, so it is absent from
    ``docker inspect``, from a crash dump, and from ``/proc/<pid>/environ``. That last one is
    the one that matters: a rail that runs interactive processes beside its own uvicorn, in the
    same PID namespace, exposes the single credential every broker client shares to anything
    that can read the backend's environment, unless those processes run under a different uid.
    terminal-fun's image can do that, but only its deployment switches it on: the service needs
    ``user: "0:0"`` plus ``cap_add: [SETUID, SETGID]``. deploy/docker-compose.yml sets both;
    the lean installer compose sets neither, so there its games share the backend's uid.

    ``BROKER_AUTH_TOKEN`` — the literal value. Unchanged, still supported, and still what
    every deployed compose file passes today. With no _FILE set this function is exactly the
    expression it replaced, so the existing path cannot regress.

    Trailing whitespace is stripped, because a secret file written by ``echo`` or an editor
    ends in a newline and a bearer token with a trailing ``\\n`` is a 401 nobody can explain.

    A configured but UNREADABLE file yields ``""`` rather than falling back to the plain
    variable. The fallback would be friendlier and wrong: it would leave a deployment that
    believes it has moved off the environment still reading the secret from it, and would
    hide a typo'd path behind a rail that appears to work. An empty token sends no header and
    earns the broker's 401 — a failure someone can see.

    NOTE what this does and does not buy. It removes the token from the PROCESS ENVIRONMENT,
    so a same-uid reader can no longer lift it out of ``/proc/*/environ``. It does NOT stop
    that same reader from simply opening the file. Closing that needs file ownership the
    reading process does not hold — which means a uid split in the image that runs it.
    """
    path = os.environ.get("BROKER_AUTH_TOKEN_FILE", "").strip()
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return ""
    return os.environ.get("BROKER_AUTH_TOKEN", "").strip()


def _auth_headers() -> dict[str, str]:
    """Bearer header for the broker's control-plane token, or empty if unset."""
    tok = broker_token()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


class BrokerError(RuntimeError):
    """Raised when the broker returns an error or is unreachable.

    ``status`` is the broker's HTTP status (None when it never answered) and ``detail`` its
    own message: the ``detail`` string of a FastAPI error body, else the body text. A caller
    that relays a broker refusal reads these rather than splitting ``str(exc)``, which once
    cut a refused label like ``a: b`` in half at its own colon. ``str(exc)`` is unchanged, so
    a caller that matches on it still works.
    """

    def __init__(self, message: str, *, status: int | None = None,
                 detail: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail


def _error_detail(resp: httpx.Response) -> str:
    """The broker's own words for a failure: FastAPI's ``{"detail": "..."}`` when that is the
    body, the raw text otherwise (a proxy's HTML page, a non-string detail)."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text
    if isinstance(body, dict) and isinstance(body.get("detail"), str):
        return body["detail"]
    return resp.text


class BrokerClient:
    """Thin async wrapper over the broker's HTTP API.

    Use as an async context manager, or pass a shared ``httpx.AsyncClient``.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11500",
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._auth = _auth_headers()
        # Long default timeout: a cold heavy-model load can take a while. Env-overridable
        # because the hardcoded 600 sat BELOW the broker's own media_timeout of 1200, so on a
        # slow media call the CLIENT gave up first while the worker kept running — the caller
        # saw a timeout, the GPU stayed busy, and a retry queued behind the job still holding
        # it. The gateway fronts /api/platform/transcribe, where a first-ever dictation
        # downloads ~500 MB of whisper weights inside the call. A client ceiling must sit
        # ABOVE the server's, or the server's own error can never be the thing you see.
        if timeout is None:
            timeout = float(os.getenv("PLATFORM_BROKER_TIMEOUT", "600"))
        self._client = client or httpx.AsyncClient(base_url=self._base_url, timeout=timeout)

    async def __aenter__(self) -> "BrokerClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if self._auth:
            kwargs["headers"] = {**self._auth, **(kwargs.get("headers") or {})}
        try:
            resp = await self._client.request(method, path, **kwargs)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            raise BrokerError(f"broker {method} {path} -> {code}: {exc.response.text}",
                              status=code, detail=_error_detail(exc.response)) from exc
        except httpx.HTTPError as exc:
            raise BrokerError(f"broker {method} {path} unreachable: {exc}") from exc
        # Inside the contract too: a 200 that is not JSON (a proxy's page, a truncated body)
        # used to escape as a bare JSONDecodeError, which no caller here catches.
        try:
            return resp.json()
        except ValueError as exc:
            raise BrokerError(f"broker {method} {path} -> {resp.status_code}: not JSON "
                              f"({resp.text[:120]!r})", status=resp.status_code) from exc

    # --- read ---------------------------------------------------------------

    async def status(self) -> dict[str, Any]:
        return await self._request("GET", "/v1/status")

    async def models(self, upstream: str | None = None) -> dict[str, Any]:
        """Installed models. ``upstream`` names a registered remote broker instead of this box.

        Needed once a role can be delegated: the choices for a slot pointed off-site have to
        come from the box that will run it, not from whatever happens to be installed here.
        The argument is optional and the query param is omitted when unset, so every existing
        no-arg caller still puts byte-for-byte the request it always did on the wire.
        """
        params = {"upstream": upstream} if upstream else None
        return await self._request("GET", "/v1/models", params=params)

    async def upstreams(self) -> dict[str, Any]:
        """Every broker a role may be delegated to, ``local`` first.

        Rows are ``{"name", "url", "healthy"}`` and never carry a token: the caller needs to
        know a box is registered and answering, not how to authenticate to it. ``local`` is
        always present, so no caller has to synthesise it.
        """
        return await self._request("GET", "/v1/upstreams")

    async def ps(self) -> dict[str, Any]:
        return await self._request("GET", "/v1/ps")

    async def roles(self, *, inventory: bool = False, local_only: bool = False) -> dict[str, Any]:
        """Every model role with its pattern + the concrete model it resolves to.

        ``inventory=True`` also asks for every registered box's health and installed models,
        read by the SAME probe that judged the delegated rows: ``{"roles", "upstreams",
        "models": {box: [...]}}``. That is the whole Rails tab in one call, where asking
        ``upstreams()`` and ``models(box)`` separately probed each remote a second time. A
        broker that predates it ignores the parameter and answers ``{"roles"}`` alone, so a
        caller must check for the extra keys. Omitted when False, so every existing caller
        puts byte-for-byte the request it always did on the wire.

        ``local_only=True`` asks for THIS card's roles only: no delegated rows and no remote
        dialled. An older broker ignores it and answers every row after probing every remote,
        which is slower but still correct for a caller that skips delegated rows itself.
        """
        params = {"inventory": "true"} if inventory else None
        if local_only:
            params = {**(params or {}), "local_only": "true"}
        return await self._request("GET", "/v1/roles", params=params)

    async def set_role(self, role: str, model: str) -> dict[str, Any]:
        """Repoint a role to a new model name / glob (persisted to the broker's roles.json)."""
        return await self._request("PUT", f"/v1/roles/{role}", json={"model": model})

    async def set_cli_role(self, role: str, enabled: bool | None = None, *,
                           model: Any = _KEEP_MODEL) -> dict[str, Any]:
        """Switch ONE role's Claude CLI eligibility on or off, choose the Claude model its
        calls use, or both (the broker's BROKER_CLI_ROLES_FILE, hot-read). ``enabled`` None
        sends no switch. ``model`` is an id from the broker's BROKER_CLI_MODELS, None to clear
        the role's own (null on the wire), or left out to leave it as it is (no ``model`` key).
        Answers ``{"cli": {...}}``, the same block ``roles(inventory=True)`` carries. Needs a
        full-scope token. A broker 400 (unknown role, or a model it does not allow) or 409 (no
        switch file, or an invalid one) arrives as BrokerError with ``status`` and the broker's
        own ``detail``."""
        body: dict[str, Any] = {}
        if enabled is not None:
            body["enabled"] = bool(enabled)
        if model is not _KEEP_MODEL:
            body["model"] = model
        return await self._request("PUT", f"/v1/cli/roles/{role}", json=body)

    async def tokens(self) -> dict[str, Any]:
        """Named broker tokens (hashes omitted), the valid scopes, and whether the shared
        BROKER_AUTH_TOKEN is also in use."""
        return await self._request("GET", "/v1/tokens")

    async def create_token(self, label: str, scope: str) -> dict[str, Any]:
        """Mint a named token. The response carries the PLAINTEXT and is the only place it ever
        appears -- the broker stores a sha256 hash. Callers must not log this."""
        return await self._request("POST", "/v1/tokens",
                                   json={"label": label, "scope": scope})

    async def revoke_token(self, token_id: str) -> dict[str, Any]:
        """Revoke by id. Effective on the broker's next request (tokens.json is hot-read)."""
        return await self._request("DELETE", f"/v1/tokens/{token_id}")

    async def disabled(self) -> list[str]:
        """Admin-disabled model names (availability control; still served if a role uses one)."""
        return (await self._request("GET", "/v1/disabled")).get("disabled", [])

    async def set_disabled(self, names: list[str]) -> dict[str, Any]:
        """Replace the full disabled-name set (persisted to the broker's disabled.json)."""
        return await self._request("PUT", "/v1/disabled", json={"names": names})

    # --- GPU control --------------------------------------------------------
    # No load() wrapper: its one caller was the gateway's POST /api/platform/load, which had no
    # caller of its own and went with it.

    async def unload(self, model: str) -> dict[str, Any]:
        return await self._request("POST", "/v1/unload", json={"model": model})

    async def cancel(self, seq: int) -> dict[str, Any]:
        """Cancel a queued/active GPU job by its queue seq."""
        return await self._request("POST", "/v1/cancel", json={"seq": seq})

    # --- inference ----------------------------------------------------------

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        options: dict[str, Any] | None = None,
        keep_alive: str | int | None = None,
        format: str | dict[str, Any] | None = None,
        think: bool | None = None,
        requester: str | None = None,
    ) -> dict[str, Any]:
        """One buffered chat turn.

        ``requester`` is the platform username this call is FOR. The broker uses it for one
        decision only: whether the platform owner's own Claude CLI subscription may answer
        instead of the local GPU (``Broker.cli_decision``). Omitting it is always safe and
        always means local. Pass a name only when that person asked for this call AND the
        material in the prompt is theirs; deciding that is the rail's job, because the
        broker sees nothing but the name.
        """
        payload: dict[str, Any] = {"model": model, "messages": messages}
        if options is not None:
            payload["options"] = options
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        if format is not None:
            payload["format"] = format
        # Ollama thinking toggle (reasoning models). None = model default; False makes a
        # thinking model emit content directly instead of burning its budget on reasoning.
        if think is not None:
            payload["think"] = think
        # Omitted rather than sent as null or "" when unknown, so an older broker whose
        # ChatRequest has no such field sees a byte-identical payload, and a blank name is an
        # absence the broker reads as background work rather than a name it must reject.
        if requester:
            payload["requester"] = requester
        return await self._request("POST", "/v1/chat", json=payload)

    # No wrapper for /v1/embed: it had zero callers (every rail that embeds goes through its own
    # sync client), and an unused wrapper is a second contract to keep in step with the route.

    # --- media --------------------------------------------------------------

    async def image(
        self,
        prompts: list[str],
        *,
        negative_prompt: str | None = None,
        steps: int = 4,
        size: int = 512,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Generate images. Returns {"images": [<b64 png>|None, ...]}.
        Caller owns the full prompt; the broker imposes no template.

        ``model`` picks the media backend (``sdxl-turbo``, ``flux-schnell``) or an image
        ``@role``; omitted, the broker's default (sdxl-turbo) applies and the request is
        byte-for-byte what it always was."""
        payload: dict[str, Any] = {"prompts": prompts, "steps": steps, "size": size}
        if negative_prompt is not None:
            payload["negative_prompt"] = negative_prompt
        if model:
            payload["model"] = model
        return await self._request("POST", "/v1/image", json=payload)

    # No wrapper for /v1/tts (XTTS segment synthesis with per-segment timings). The ROUTE is
    # live, and nothing in the repo calls it: edu-suite and IEP narrate through /v1/tts_batch
    # (tts_batch below, and edu_media_core's synthesize_wavs), and edu_media_core's one
    # single-clip client, synthesize_wav, had no caller and was removed 2026-10-14. A sanctioned
    # wrapper nobody calls is a second contract to keep in step with the first; add it back
    # the day a rail wants it, rather than keeping a shape to prove it could.

    async def tts_batch(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Synthesize many independent clips to separate wavs in one XTTS load.
        Returns {"audios": [<b64 wav>, ...]}. Each item is {"lang":.., "text":..}."""
        return await self._request("POST", "/v1/tts_batch", json={"items": items})

    # --- light speech (ungated: never disturbs a resident model) -------------
    # Both of these run CPU/ONNX in the broker's media worker WITHOUT the GPU gate and
    # without evicting anything, which is the only reason voice can be offered on every rail:
    # a read-aloud or mic press mid-conversation must not displace the model you are using.

    async def tts_light(
        self,
        text: str,
        *,
        voice: str | None = None,
        lang_code: str | None = None,
        speed: float | None = None,
    ) -> dict[str, Any]:
        """Kokoro-82M read-aloud. Returns {"audio_b64", "sample_rate", "voice", "lang"}.

        Not a replacement for /v1/tts: that one clones a voice from a reference clip and
        returns per-segment timings for highlight-sync. This returns audio only.
        """
        payload: dict[str, Any] = {"text": text}
        if voice:
            payload["voice"] = voice
        if lang_code:
            payload["lang_code"] = lang_code
        if speed is not None:
            payload["speed"] = speed
        return await self._request("POST", "/v1/tts_light", json=payload)

    async def transcribe(
        self,
        audio_b64: str,
        *,
        suffix: str | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        """faster-whisper dictation. Returns {"text", "language", "duration", "model"}.

        ``suffix`` is the container hint from MediaRecorder (".webm" Chrome, ".ogg"
        Firefox, ".mp4" Safari); the broker writes the bytes as-is, no transcode.
        ``language`` None lets Whisper detect — the broker's model is multilingual.
        """
        payload: dict[str, Any] = {"audio_b64": audio_b64}
        if suffix:
            payload["suffix"] = suffix
        if language:
            payload["language"] = language
        return await self._request("POST", "/v1/transcribe", json=payload)
