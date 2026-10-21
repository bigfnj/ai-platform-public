"""Request bodies for the broker API. Responses pass through as backend JSON."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, model_validator


class LoadRequest(BaseModel):
    model: str
    # -1 keeps the model resident indefinitely; "5m" / 0 also valid.
    keep_alive: str | int | None = None


class UnloadRequest(BaseModel):
    model: str


class CancelRequest(BaseModel):
    seq: int  # the queue seq of the job to cancel


class RoleUpdate(BaseModel):
    """Repoint one model role to a new model name or glob pattern (persisted to the
    roles.json overlay; hot-read on the next resolve)."""
    model: str


class CliRoleUpdate(BaseModel):
    """Switch one role's Claude CLI eligibility on or off, choose its Claude model, or both
    (persisted to BROKER_CLI_ROLES_FILE; hot-read). `enabled` is a JSON boolean only: pydantic's
    lax mode would read "false", "no" or 0 as a bool, and a switch that parses a string is a
    switch somebody will mistype. `model` is a string, or null to clear the role's own; left
    out, the model stays as it is. A body that sends neither is refused (422)."""
    enabled: StrictBool | None = None
    model: StrictStr | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _says_something(self) -> "CliRoleUpdate":
        if self.enabled is None and "model" not in self.model_fields_set:
            raise ValueError('send "enabled" (true or false), "model" (an id, or null), or both')
        return self


class TokenCreate(BaseModel):
    """A named broker access token. The LABEL is the whole point -- it is how the right row is
    found months later when a workstation is decommissioned -- so it has no default."""
    label: str
    scope: str = "inference"


class DisabledUpdate(BaseModel):
    """The full set of admin-disabled model names (persisted to disabled.json). A disabled model
    is hidden from pickers/UI and unloaded, but the broker still SERVES it if a role resolves to
    it — disabling is availability control, not enforcement."""
    names: list[str]


class ChatMessage(BaseModel):
    role: str
    content: str
    # Optional base64-encoded images for vision-capable models (e.g. mistral-small3.2,
    # gemma3): Ollama's /api/chat reads `images` on a message. Omitted when None.
    images: list[str] | None = None


class ChatRequest(BaseModel):
    # An unknown top-level field is KEPT in `model_extra` rather than refused or silently
    # dropped, so the route can name it in a warning (main._warn_unknown_fields). Refusing
    # would 422 every rail that sends a harmless extra today. Dropping it unannounced, pydantic's
    # default, is how watch-builder's top-level `max_tokens` cap never reached Ollama while
    # every call returned 200. A kept extra goes nowhere: the routes forward each known field
    # by name and never the model as a whole.
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[ChatMessage] = Field(min_length=1)
    options: dict[str, Any] | None = None
    keep_alive: str | int | None = None
    # Ollama structured-output mode: "json" or a JSON schema dict. edu-suite's
    # translate relies on JSON mode.
    format: str | dict[str, Any] | None = None
    # Ollama thinking toggle for reasoning models (qwen3.6 et al.). None = leave the
    # model's default. Set false for structured/JSON work: a thinking model otherwise
    # spends its whole output budget on reasoning and returns EMPTY content — measured at
    # a ~33% empty-JSON rate and ~8x the latency on the IEP rail's worksheet drafting.
    think: bool | None = None
    # WHO is waiting on this answer, as the gateway authenticated them and the rail forwarded
    # them. Used by one decision only: whether the call may be served by the platform owner's
    # own Claude subscription instead of the local GPU (see Broker.cli_decision), on /v1/chat
    # and /v1/chat/stream alike (the OpenAI-compatible surface has no such field).
    #
    # Trust: the broker cannot check this name, so it is honoured only from a FULL-scope
    # credential (main._trusted_requester). A rail holds the shared full-scope token and could
    # repoint every role anyway, so trusting its name adds no hole. An INFERENCE-scoped named
    # token (a LAN workstation) is limited trust by design, and its requester is DROPPED before
    # the CLI decision: the call runs as background work, locally. An open broker (no token
    # configured at all) treats every caller as full scope, as it does for every route. It is
    # also why the owner list is an operator setting no caller can influence, and why only an
    # admin-set @role is eligible. Absent (the default) means background work, which never
    # reaches the CLI.
    requester: str | None = None


class EmbedRequest(BaseModel):
    model: str
    input: str | list[str]


class EmbedImageRequest(BaseModel):
    """IMAGE embeddings for retrieval / grounding. Base64 images -> unit-normalised
    vectors from a small CLIP-class encoder (SigLIP). Runs CPU-only in the media
    worker, un-gated, so it never contends for the GPU or evicts a resident model."""
    images: list[str] = Field(min_length=1)   # base64 PNG/JPEG
    model: str | None = None                   # default: a SigLIP base encoder


class ImageRequest(BaseModel):
    """Text-to-image. The caller supplies the full prompt; the broker imposes no
    template (that stays in the app that wants it). ``model`` selects the backend
    the media worker loads: ``sdxl-turbo`` (default, fast 4-step) or ``flux-schnell``
    (FLUX.1-schnell, nf4-quantized — far stronger prompt adherence, still few-step)."""
    prompts: list[str] = Field(min_length=1)
    negative_prompt: str | None = None
    steps: int = Field(default=4, ge=1, le=50)
    size: int = Field(default=512, ge=128, le=1536)
    model: str = "sdxl-turbo"


# Bounds for the GPU-gated speech bodies. The 8000-character figure is the one
# TtsLightRequest already documents: roughly ten minutes of speech, well past any clip a
# user waits for, and the same cap the two RAG rails put on their own SpeakBody.
#
# A different blast radius from the light route, which is why these were left when that one
# was capped, and why they are notes rather than an incident: these need the broker token
# and are GPU-gated, so they SERIALISE rather than fan out. The cost is still real, one
# oversized item ties up the card and materialises a temp file, and "you need a token to do
# it" is not a reason to accept an unbounded body.
_TTS_MAX_CHARS = 8000
# Public, because the OpenAI surface calls the broker below this model and has to apply the same
# bounds itself: /openai/v1/audio/speech and /audio/transcriptions never pass through pydantic.
TTS_LIGHT_MAX_CHARS = _TTS_MAX_CHARS
TRANSCRIBE_MAX_B64 = 15_000_000
_TTS_MAX_ITEMS = 500


class TtsSegment(BaseModel):
    lang: str  # "en" | "es" | "pause"
    text: str = Field(default="", max_length=_TTS_MAX_CHARS)
    type: str | None = None
    duration: float | None = None  # required when lang == "pause"


class TtsRequest(BaseModel):
    segments: list[TtsSegment] = Field(min_length=1, max_length=_TTS_MAX_ITEMS)


class TtsBatchItem(BaseModel):
    lang: str  # "en" | "es"
    text: str = Field(max_length=_TTS_MAX_CHARS)


class TtsBatchRequest(BaseModel):
    """Many independent clips synthesized in one XTTS load; one wav returned per item."""
    # Capped on COUNT as well as on each item: a batch is one XTTS load, so an unbounded
    # list holds the card for as long as the list is long.
    items: list[TtsBatchItem] = Field(min_length=1, max_length=_TTS_MAX_ITEMS)


class TtsLightRequest(BaseModel):
    """Kokoro-82M read-aloud — the platform-wide light TTS.

    Unlike ``TtsRequest`` (XTTS voice-cloning, GPU-gated, returns highlight-sync timings)
    this is CPU/ONNX and runs without disturbing the resident chat model, which is what
    makes it safe to offer on every rail.
    """
    # Bounded like every other text field that reaches a model. 8000 characters is roughly ten
    # minutes of speech, well past any read-aloud a user waits for, and it matches the cap the
    # two RAG rails put on their own SpeakBody so the platform-wide route is not the loose one.
    # Unbounded, this was one of two speech bodies with no cap at any hop: measured, a single
    # 41.9 MB request was accepted, reached the worker, and materialised a 30 MB temp file on the
    # system drive, on a route that needs only `require_user`.
    text: str = Field(min_length=1, max_length=_TTS_MAX_CHARS)
    voice: str | None = None       # Kokoro voice id, e.g. "af_heart" (en) or "ef_dora" (es)
    lang_code: str | None = None   # 'a' = American English (default), 'b' = British, 'e' = Spanish
    speed: float | None = Field(default=None, ge=0.5, le=2.0)


class TranscribeRequest(BaseModel):
    """Speech-to-text for one recorded utterance (faster-whisper, CPU/int8)."""
    # 15 MB of base64 is a couple of minutes of opus, past any single dictated utterance, and is
    # deliberately the same bound smb-partner already puts on its own TranscribeBody. The reason
    # it is here rather than only at the gateway: the bytes are base64-decoded and written to a
    # temp file by a LocalSystem subprocess, and the spec is round-tripped through a second temp
    # file on the way, so an uncapped body is several copies of itself in RAM plus transient disk
    # on the system drive. Measured before this cap: 41.9 MB accepted end to end, 13.3 s.
    audio_b64: str = Field(min_length=1, max_length=TRANSCRIBE_MAX_B64)
    # Constrained, because this reaches a filename. Unpatterned, it was an arbitrary-write
    # primitive: the worker interpolated it into the temp clip's name and a "/../.." value
    # escaped the temp directory entirely, one statement before write_bytes, in a service
    # running as LocalSystem. media_worker.safe_clip_suffix is the authoritative check; this
    # one turns the attempt into a 422 at the edge instead of a silently-substituted default.
    suffix: str | None = Field(default=None, pattern=r"^\.[A-Za-z0-9]{1,7}$")
    language: str | None = None            # ISO code; None lets Whisper detect


class VoiceRequest(BaseModel):
    """ai-voice rail: synthesize ``text`` in a registered voice (Chatterbox/RVC/...)."""
    voice_id: str
    text: str = Field(min_length=1, max_length=_TTS_MAX_CHARS)
