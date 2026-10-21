"""Shared local-first media engine for edu-suite.

Submodules: translate, tts, images, pdf, classify, jobs, broker_media, cache, profiles
(and present_levels, private). Import them
directly, e.g.
`from edu_media_core import translate as core`. Heavy modules (tts, images) lazy-
import torch/TTS/diffusers inside their functions, so importing this package is
cheap.
"""

# "models" is gone: V-20 deleted the whole module with the unreachable ModelManager.
# Harmless while it sat here (no star import exists and _handle_fromlist swallows the
# miss) but a list of submodules that names one that does not exist is a lie the next
# reader has to disprove.
# present_levels is deliberately NOT listed: the public snapshot withholds that module,
# and a star import of a name the package does not ship would fail there.
# tests/test_upkeep.py holds this list to the package's own files, that one exception
# included.
__all__ = ["translate", "tts", "images", "pdf", "classify", "jobs", "broker_media",
           "cache", "profiles"]
