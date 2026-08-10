"""Shared local-first media engine for edu-suite.

Submodules: translate, tts, images, pdf, classify. Import them directly, e.g.
`from edu_media_core import translate as core`. Heavy modules (tts, images) lazy-
import torch/TTS/diffusers inside their functions, so importing this package is
cheap.
"""

__all__ = ["translate", "tts", "images", "pdf", "classify", "models", "jobs",
           "broker_media"]
