"""GPU model lifecycle management.

The suite's heavy models (qwen via Ollama, XTTS v2, SDXL-Turbo) do not all fit on
a 24GB card at once, so exactly one may be resident at a time. ``ModelManager``
is the single owner of that invariant: ``ensure(key)`` swaps the resident model
(emitting unload/load events) and ``validate(key)`` asserts the right one is
loaded before a stage runs.

Handles are pluggable so this is unit-testable without a GPU: tests use
``StubHandle``; runtime uses ``default_handles()`` (Ollama + in-process torch).
Heavy imports live inside the runtime handles, so importing this module is cheap.
"""
from __future__ import annotations

import os
from typing import Callable, Protocol, runtime_checkable


class ModelValidationError(RuntimeError):
    """Raised when the model required for a stage is not the one resident."""


@runtime_checkable
class ModelHandle(Protocol):
    key: str
    name: str

    def load(self) -> None: ...
    def unload(self) -> None: ...
    def is_resident(self) -> bool: ...


# emit(status, model_name, message): status in loading/unloading/loaded/ready
ModelEmit = Callable[[str, str, str], None]


class ModelManager:
    """Keeps at most one heavy model resident; the only thing that loads/unloads."""

    def __init__(self, handles: dict[str, ModelHandle] | None = None,
                 emit: ModelEmit | None = None):
        self._handles: dict[str, ModelHandle] = dict(handles or {})
        self._emit: ModelEmit = emit or (lambda status, name, message="": None)
        self.current: str | None = None

    def register(self, handle: ModelHandle) -> None:
        self._handles[handle.key] = handle

    def set_emit(self, emit: ModelEmit) -> None:
        self._emit = emit

    def ensure(self, key: str) -> None:
        """Make ``key`` the resident model, unloading any other first."""
        if key not in self._handles:
            raise KeyError(f"unknown model {key!r}; known: {sorted(self._handles)}")
        if self.current == key:
            self._emit("ready", self._handles[key].name, "already loaded")
            return
        if self.current is not None:
            cur = self._handles[self.current]
            self._emit("unloading", cur.name, "")
            cur.unload()
            self.current = None
        h = self._handles[key]
        self._emit("loading", h.name, "")
        h.load()
        self.current = key
        self._emit("loaded", h.name, "")

    def validate(self, key: str) -> None:
        """Assert ``key`` is the resident model and the handle agrees."""
        if self.current != key:
            raise ModelValidationError(
                f"stage requires {key!r} but resident model is {self.current!r}")
        if not self._handles[key].is_resident():
            raise ModelValidationError(f"{key!r} handle reports it is not resident")

    def unload_all(self) -> None:
        """Free VRAM by unloading whatever is resident (used on failure/finish)."""
        if self.current is not None:
            cur = self._handles[self.current]
            self._emit("unloading", cur.name, "")
            cur.unload()
            self.current = None


class StubHandle:
    """In-memory handle for tests and the GPU-free dev path."""

    def __init__(self, key: str, name: str | None = None):
        self.key = key
        self.name = name or key
        self._resident = False
        self.loads = 0
        self.unloads = 0

    def load(self) -> None:
        self._resident = True
        self.loads += 1

    def unload(self) -> None:
        self._resident = False
        self.unloads += 1

    def is_resident(self) -> bool:
        return self._resident


class InProcessHandle:
    """Wraps an in-process model (XTTS/SDXL) via load/unload/resident callables."""

    def __init__(self, key: str, name: str,
                 load_fn: Callable[[], None],
                 unload_fn: Callable[[], None],
                 resident_fn: Callable[[], bool]):
        self.key = key
        self.name = name
        self._load = load_fn
        self._unload = unload_fn
        self._resident = resident_fn

    def load(self) -> None:
        self._load()

    def unload(self) -> None:
        self._unload()

    def is_resident(self) -> bool:
        return self._resident()


class OllamaHandle:
    """A model managed by the Ollama server (loaded on demand, unloaded via stop)."""

    def __init__(self, name: str, key: str = "qwen", host: str | None = None):
        self.key = key
        self.name = name
        self._host = host or os.getenv("OLLAMA_HOST", "http://localhost:11434")

    def _client(self):
        import ollama
        return ollama.Client(host=self._host)

    def load(self) -> None:
        # A zero-length generate warms the model into VRAM with a long keep-alive.
        self._client().generate(model=self.name, prompt="", keep_alive="30m")

    def unload(self) -> None:
        # `ollama stop` reliably evicts the model from VRAM; the keep_alive=0 API
        # call is a fallback if the CLI isn't on PATH.
        import subprocess
        try:
            subprocess.run(["ollama", "stop", self.name], timeout=30,
                           capture_output=True)
            return
        except Exception:
            pass
        try:
            import requests
            requests.post(f"{self._host}/api/generate",
                          json={"model": self.name, "keep_alive": 0}, timeout=30)
        except Exception:
            pass

    def is_resident(self) -> bool:
        try:
            import requests
            r = requests.get(f"{self._host}/api/ps", timeout=5)
            names = [m.get("name", "") for m in r.json().get("models", [])]
            return any(self.name in n or n in self.name for n in names)
        except Exception:
            return False


def default_handles() -> dict[str, ModelHandle]:
    """Runtime handles for the real engines. Construction is cheap (no GPU work
    until load() is called)."""
    from . import tts, images

    def _xtts_unload():
        import torch
        tts._tts = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _sdxl_unload():
        import torch
        images._pipe = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    return {
        "qwen": OllamaHandle(os.getenv("EDU_LLM_MODEL", "mistral-small3*:24b")),
        "xtts": InProcessHandle(
            "xtts", tts.MODEL_NAME,
            load_fn=lambda: tts.get_tts(),
            unload_fn=_xtts_unload,
            resident_fn=lambda: tts._tts is not None,
        ),
        "sdxl": InProcessHandle(
            "sdxl", "stabilityai/sdxl-turbo",
            load_fn=lambda: images.get_sdxl(),
            unload_fn=_sdxl_unload,
            resident_fn=lambda: images._pipe is not None,
        ),
    }
