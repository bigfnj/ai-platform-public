"""Empty stand-ins for edu-media-core's heavy optional dependencies, for an import and no longer.

The modules under test import ollama, pdfplumber, fitz (PyMuPDF), pytesseract and Pillow at their
top. These tests exercise the caching, classification, PDF parsing and wiring around those
libraries, not the libraries, so they run on a bare Python with empty modules in their place.
test_core.py, test_resource_safety.py and test_upkeep.py used to put those into sys.modules when
pytest collected them, and there they stayed for the session. tools/test-targets.json runs this
directory in core's one pytest process with tools/tests (run-tests.ps1, and private CI since
2026-10-20), so a later test there that imported pdfplumber, fitz, pytesseract or PIL was handed
an empty module, whatever was installed: tools/tests/test_rail_smoke.py and test_requester.py
each grew a workaround for PIL.

`stand_ins()` puts a stand-in into sys.modules for each of them that nothing has imported yet, for
the block only, and on leaving takes out exactly the ones it put in. A module imported inside the
block keeps the stand-ins it bound, as it did before; sys.modules holds none of them afterwards.
Not a test file.
"""
from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from typing import Iterator

#: The plain stand-ins (Pillow is the PIL package and its Image module: see `stand_ins`).
NAMES = ("ollama", "pdfplumber", "fitz", "pytesseract")


@contextmanager
def stand_ins(names: tuple[str, ...] = NAMES, *, pil: bool = True) -> Iterator[None]:
    """An empty module in sys.modules for each of `names` not imported yet, and for PIL and
    PIL.Image when `pil` and PIL is not imported yet, while the block runs."""
    planted: dict[str, types.ModuleType] = {}
    for name in names:
        if name not in sys.modules:
            planted[name] = sys.modules[name] = types.ModuleType(name)
    if pil and "PIL" not in sys.modules:
        package, image = types.ModuleType("PIL"), types.ModuleType("PIL.Image")
        package.Image = image
        planted["PIL"] = sys.modules["PIL"] = package
        planted["PIL.Image"] = sys.modules["PIL.Image"] = image
    try:
        yield
    finally:
        for name, module in planted.items():
            if sys.modules.get(name) is module:
                del sys.modules[name]
