"""mutate_railcheck.py's mutations of ai-playground's frontend. NOT a test file.

Each names the check in railchecks/ai_playground.py that must fail. mutate_railcheck.py's header
says how a run works, and why this file ships exactly when its rail does.
"""
from __future__ import annotations

from . import Mutation

MUTATIONS = [
    # The standalone-dev page sheet: only the GLOBAL half of the css checks can see it (html,
    # body, #root, :root; no class in it at all).
    Mutation("ai-playground's main.tsx imports its dev page sheet again", "ai-playground", "main.tsx",
             'import AiPlaygroundModule from "./module";\n',
             'import AiPlaygroundModule from "./module";\nimport "./standalone.css";\n', "css-ai-playground"),
    Mutation("the ask bar's button rule matches Dictate again", "ai-playground", "theme.css",
             ".ai-playground .askbar > button {", ".ai-playground .askbar button {", "ap-dictate-pill"),
    # The controls row: the endpoint line wraps inside itself rather than pushing the row wide.
    Mutation("the endpoint line no longer wraps inside itself", "ai-playground", "theme.css",
             "flex: 1 1 240px; min-width: 0; text-align: right; ", "", "ap-controls-row"),
]
