"""Bouquet Builder — a platform rail that turns a bouquet photo into a full report.

A florist-grade flower knowledge base (50 profiles + cross-cutting references +
licensed reference photos) plus a vision→report pipeline: identify every flower
in a photo through the platform broker's vision model, pull each flower's profile,
apply the color/occasion/culture lenses across the whole arrangement, and write
either an expert analysis report or Frenchies-Flowers customer copy.

All GPU/model work goes through the platform broker (never Ollama directly).
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
