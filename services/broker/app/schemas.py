"""Request bodies for the broker API. Responses pass through as backend JSON."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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


class TtsSegment(BaseModel):
    lang: str  # "en" | "es" | "pause"
    text: str = ""
    type: str | None = None
    duration: float | None = None  # required when lang == "pause"


class TtsRequest(BaseModel):
    segments: list[TtsSegment] = Field(min_length=1)


class TtsBatchItem(BaseModel):
    lang: str  # "en" | "es"
    text: str


class TtsBatchRequest(BaseModel):
    """Many independent clips synthesized in one XTTS load; one wav returned per item."""
    items: list[TtsBatchItem] = Field(min_length=1)


class TtsLightRequest(BaseModel):
    """Kokoro-82M read-aloud — the platform-wide light TTS.

    Unlike ``TtsRequest`` (XTTS voice-cloning, GPU-gated, returns highlight-sync timings)
    this is CPU/ONNX and runs without disturbing the resident chat model, which is what
    makes it safe to offer on every rail.
    """
    text: str = Field(min_length=1)
    voice: str | None = None       # Kokoro voice id, e.g. "af_heart" (en) or "ef_dora" (es)
    lang_code: str | None = None   # 'a' = American English (default), 'b' = British, 'e' = Spanish
    speed: float | None = Field(default=None, ge=0.5, le=2.0)


class TranscribeRequest(BaseModel):
    """Speech-to-text for one recorded utterance (faster-whisper, CPU/int8)."""
    audio_b64: str = Field(min_length=1)   # raw MediaRecorder bytes, base64
    suffix: str | None = None              # container hint: ".webm" Chrome, ".ogg" Firefox
    language: str | None = None            # ISO code; None lets Whisper detect


class VoiceRequest(BaseModel):
    """ai-voice rail: synthesize ``text`` in a registered voice (Chatterbox/RVC/...)."""
    voice_id: str
    text: str = Field(min_length=1)
