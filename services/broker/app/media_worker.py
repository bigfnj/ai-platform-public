"""Short-lived media worker: ``python media_worker.py <in.json> <out.json>``.

Run by the broker with **edu-suite's CUDA venv** (not the broker's own), because
torch/diffusers/TTS live there. It loads exactly one heavy torch model, does the
requested batch, writes a JSON result, and EXITS — process exit is what fully
reclaims VRAM (torch does not release it in-process; edu-suite learned this the
hard way with its per-job subprocess runner).

This file imports only stdlib + ``edu_media_core`` (+ its deps). It must NOT import
any broker (``app.*``) code, since it runs under a different interpreter.

Spec (in.json), one of:
  {"op": "image", "media_core_src": "...", "prompts": [...],
   "negative_prompt": "", "steps": 4, "size": 512}
  {"op": "tts", "media_core_src": "...", "voices_dir": "...",
   "segments": [{"lang": "en"|"es"|"pause", "text": "...", "duration": 0.5}, ...]}

Result (out.json):
  image -> {"images": ["<b64 png>"|null, ...], "errors": [...]}
  tts   -> {"audio_b64": "<b64 wav>", "sample_rate": 24000, "timings": [...]}
  error -> {"error": "..."}  (also exit code 1)
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import traceback
from pathlib import Path

# This worker's stdout/stderr is a pipe (see media.py), and a pipe defaults to the
# locale encoding — cp1252 under a Windows service — so ANY non-ASCII diagnostic
# print (an es_MX string, or the "Saved →" arrow in edu_media_core) would raise
# UnicodeEncodeError and fail the whole job. Force UTF-8 so prints can never do that.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - older/replaced streams without reconfigure
        pass


def _add_core_to_path(spec: dict) -> None:
    src = spec.get("media_core_src")
    if src and src not in sys.path:
        sys.path.insert(0, src)


def do_image(spec: dict) -> dict:
    """Text-to-image. The caller owns the full prompt; the broker deliberately does
    NOT impose edu-suite's clipart template here. ``spec['model']`` picks the backend:
    ``sdxl-turbo`` (default) or ``flux-schnell`` (nf4 FLUX.1-schnell)."""
    from edu_media_core import images  # noqa: E402 (path set up above)

    model = str(spec.get("model") or "sdxl-turbo").lower()
    is_flux = model.startswith("flux")
    negative = spec.get("negative_prompt") or ""
    steps = int(spec.get("steps", 4))
    size = int(spec.get("size", 512))

    pipe = images.get_flux() if is_flux else images.get_sdxl()

    out: list[str | None] = []
    errors: list[str] = []
    for prompt in spec["prompts"]:
        try:
            if is_flux:
                # Schnell: guidance_scale 0, no negative prompt, 256-token cap.
                image = images.flux_generate(pipe, prompt, steps=steps, size=size)
            else:
                # SDXL-Turbo requires guidance_scale == 0.0.
                image = pipe(
                    prompt=prompt,
                    negative_prompt=negative,
                    num_inference_steps=steps,
                    guidance_scale=0.0,
                    height=size,
                    width=size,
                ).images[0]
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            out.append(base64.b64encode(buf.getvalue()).decode())
        except Exception as exc:  # noqa: BLE001 - one bad prompt shouldn't kill the batch
            out.append(None)
            errors.append(f"{prompt!r}: {exc}")
    return {"images": out, "errors": errors}


def do_tts(spec: dict) -> dict:
    """Synthesize an ordered segment list into one WAV + karaoke timings."""
    # tts.voices_dir() reads this at call time; set it before importing/using tts.
    os.environ["VOICES_DIR"] = spec["voices_dir"]
    from edu_media_core import tts  # noqa: E402
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        out_path = Path(td) / "out.wav"
        timings = tts.generate_timed_audio(spec["segments"], out_path)
        data = out_path.read_bytes()

    return {
        "audio_b64": base64.b64encode(data).decode(),
        "sample_rate": tts.SAMPLE_RATE,
        "timings": timings,
    }


def do_tts_batch(spec: dict) -> dict:
    """Synthesize each item to its OWN wav, loading XTTS once for the whole batch.

    This is the batch counterpart to do_tts: instead of one combined wav + timings,
    it returns a separate wav per item, so a caller generating many independent
    clips (e.g. a CVC worksheet's per-word audio) pays a single model load, not one
    per clip. Result: {"audios": [<b64 wav>, ...]} aligned with spec["items"].
    """
    os.environ["VOICES_DIR"] = spec["voices_dir"]
    from edu_media_core import tts  # noqa: E402

    audios: list[str] = []
    for item in spec["items"]:
        wav = tts.synthesize_segment(item["text"], item["lang"])
        audios.append(tts.wav_to_b64(wav))
    return {"audios": audios, "sample_rate": tts.SAMPLE_RATE}


def do_embed_image(spec: dict) -> dict:
    """Embed base64 images with a CLIP-class encoder (SigLIP), CPU-only, returning
    unit-normalised vectors for nearest-neighbour retrieval-grounding. Independent of
    edu_media_core — only torch + transformers + PIL (all in the media venv). Loads on
    CPU (no .to(cuda)) so it never competes with the GPU chat/vision model."""
    import torch
    from PIL import Image
    from transformers import AutoModel, AutoProcessor

    model_id = str(spec.get("model") or "google/siglip2-base-patch16-384")
    model = AutoModel.from_pretrained(model_id).eval()
    proc = AutoProcessor.from_pretrained(model_id)

    vecs: list[list[float]] = []
    for b64 in spec["images"]:
        img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
        inp = proc(images=[img], return_tensors="pt")
        with torch.no_grad():
            out = model.get_image_features(**inp)
        feat = out.pooler_output if hasattr(out, "pooler_output") else out
        feat = torch.nn.functional.normalize(feat, dim=-1)
        vecs.append([round(x, 6) for x in feat[0].tolist()])
    return {"embeddings": vecs, "dim": (len(vecs[0]) if vecs else 0), "model": model_id}


_OPS = {"image": do_image, "tts": do_tts, "tts_batch": do_tts_batch,
        "embed_image": do_embed_image}


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: media_worker.py <in.json> <out.json>", file=sys.stderr)
        return 2
    in_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])
    try:
        spec = json.loads(in_path.read_text(encoding="utf-8"))
        _add_core_to_path(spec)
        op = spec.get("op")
        if op not in _OPS:
            raise ValueError(f"unknown op {op!r}; known: {sorted(_OPS)}")
        result = _OPS[op](spec)
        out_path.write_text(json.dumps(result), encoding="utf-8")
        return 0
    except Exception as exc:  # noqa: BLE001 - report the failure back to the broker
        traceback.print_exc()
        try:
            out_path.write_text(json.dumps({"error": f"{type(exc).__name__}: {exc}"}),
                                encoding="utf-8")
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
