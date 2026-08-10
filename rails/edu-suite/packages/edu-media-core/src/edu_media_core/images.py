"""Shared local image helpers: SDXL-Turbo generation + DuckDuckGo clipart search.

SDXL-Turbo does fast 1-4 step generation and fits comfortably in 24GB VRAM.
``torch``/``diffusers``/``PIL``/``ddgs`` are imported lazily so importing this
module is cheap. Callers own their own output paths and any domain glue (e.g.
per-word placeholders).
"""
from __future__ import annotations

import io
from pathlib import Path

_SDXL_MODEL = "stabilityai/sdxl-turbo"
_FLUX_MODEL = "black-forest-labs/FLUX.1-schnell"
_PROMPT_TMPL = (
    "simple flat cartoon illustration of {subject}, "
    "childrens book clip art, bold clean outlines, bright flat colors, "
    "plain solid white background, centered, single object, no text, no words"
)
_NEGATIVE = ("text, words, letters, watermark, signature, photo, realistic, "
             "blurry, cluttered, multiple objects")

_pipe = None
_flux_pipe = None


def get_flux():
    """Load FLUX.1-schnell once (nf4-quantized) and cache it in memory.

    FLUX's 12B transformer + T5-xxl text encoder are both 4-bit nf4 quantized so the
    whole pipeline fits alongside headroom on a 24GB card; compute stays bf16. Schnell
    is timestep-distilled for 1-4 step generation with guidance disabled, so it drops
    into the same few-step batch flow as SDXL-Turbo but with far better prompt
    adherence. ``enable_model_cpu_offload`` is the sanctioned way to run a bnb-quantized
    pipeline (a quantized module can't be moved with ``pipe.to(...)``)."""
    global _flux_pipe
    if _flux_pipe is None:
        import torch
        from diffusers import (BitsAndBytesConfig as DiffusersBnb, FluxPipeline,
                               FluxTransformer2DModel)
        from transformers import BitsAndBytesConfig as TransformersBnb, T5EncoderModel

        print("  Loading FLUX.1-schnell (nf4)...")
        nf4 = dict(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                   bnb_4bit_compute_dtype=torch.bfloat16)
        # device_map="cuda" quantizes each shard directly onto the GPU as it streams
        # in, so the full ~24GB bf16 transformer is NEVER materialized in CPU RAM (that
        # transient peak crashed the naive load on a busy box). nf4 transformer ~7GB +
        # nf4 T5 ~5GB + vae/clip ~1GB fits well inside 24GB, so everything stays
        # resident on the GPU — no cpu-offload shuffling (which also can't be combined
        # with a bnb device_map).
        transformer = FluxTransformer2DModel.from_pretrained(
            _FLUX_MODEL, subfolder="transformer", torch_dtype=torch.bfloat16,
            quantization_config=DiffusersBnb(**nf4), device_map="cuda")
        text_encoder_2 = T5EncoderModel.from_pretrained(
            _FLUX_MODEL, subfolder="text_encoder_2", torch_dtype=torch.bfloat16,
            quantization_config=TransformersBnb(**nf4), device_map="cuda")
        pipe = FluxPipeline.from_pretrained(
            _FLUX_MODEL, transformer=transformer, text_encoder_2=text_encoder_2,
            torch_dtype=torch.bfloat16)
        # transformer + T5 are already on CUDA (bnb device_map). A bnb-quantized pipe
        # can't be moved with pipe.to("cuda"), so nudge the small non-quantized pieces
        # (VAE, CLIP) onto the GPU individually.
        pipe.vae.to("cuda")
        pipe.text_encoder.to("cuda")
        _flux_pipe = pipe
        print("  FLUX.1-schnell ready.")
    return _flux_pipe


def flux_generate(pipe, prompt: str, *, steps: int = 4, size: int = 768, seed: int | None = None):
    """One FLUX-schnell render. Schnell wants guidance_scale=0 and takes no negative
    prompt; its text encoder caps at 256 tokens.

    ``seed`` defaults to random so recipes that share a subject string (e.g. every
    "bowl of ramen") get visually distinct icons instead of identical clones; pass an
    int only when you want a reproducible render."""
    import torch
    gen = None
    if seed is not None:
        gen = torch.Generator("cpu").manual_seed(seed)
    return pipe(
        prompt=prompt,
        guidance_scale=0.0,
        num_inference_steps=steps,
        height=size, width=size,
        max_sequence_length=256,
        generator=gen,
    ).images[0]


def get_sdxl():
    """Load the SDXL-Turbo pipeline once and cache it in memory."""
    global _pipe
    if _pipe is None:
        import torch
        from diffusers import AutoPipelineForText2Image

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if device == "cuda" else torch.float32
        print(f"  Loading SDXL-Turbo on {device} ({dtype})...")
        _pipe = AutoPipelineForText2Image.from_pretrained(
            _SDXL_MODEL, torch_dtype=dtype,
            variant="fp16" if device == "cuda" else None,
        ).to(device)
        print("  SDXL-Turbo ready.")
    return _pipe


def generate_image(subject: str, out_path: str | Path, *,
                   force: bool = False, steps: int = 4, size: int = 512) -> Path | None:
    """Generate a flat-cartoon illustration of ``subject`` to ``out_path``.

    Returns the path, the existing path if present (and not ``force``), or None
    on failure.
    """
    out_path = Path(out_path)
    if out_path.exists() and not force:
        return out_path
    try:
        # SDXL-Turbo requires guidance_scale == 0.0
        image = get_sdxl()(
            prompt=_PROMPT_TMPL.format(subject=subject),
            negative_prompt=_NEGATIVE,
            num_inference_steps=steps,
            guidance_scale=0.0,
            height=size, width=size,
        ).images[0]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(out_path)
        return out_path
    except Exception as exc:
        print(f"  [image-gen] FAILED for {subject!r}: {exc}")
        return None


def search_clipart(query: str, *, max_results: int = 8, timeout: int = 10) -> bytes | None:
    """DuckDuckGo image search; return raw bytes of the first usable result."""
    try:
        import requests
        from ddgs import DDGS
        with DDGS() as ddgs:
            # "clipart" suits child worksheets far better than real photos; fall
            # back to unfiltered so we still get something rather than nothing.
            results = list(ddgs.images(query, max_results=max_results, type_image="clipart"))
            if not results:
                results = list(ddgs.images(query, max_results=max_results))
        for result in results:
            url = result.get("image", "")
            if not url:
                continue
            try:
                r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 200 and "image" in r.headers.get("Content-Type", ""):
                    return r.content
            except Exception:
                continue
    except Exception:
        pass
    return None


def resize_png(raw: bytes, size: tuple[int, int] = (300, 300)) -> bytes:
    """Downscale raw image bytes to fit ``size`` and re-encode as PNG."""
    from PIL import Image
    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    img.thumbnail(size, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
