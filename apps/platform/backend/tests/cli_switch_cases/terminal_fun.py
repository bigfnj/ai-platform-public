"""mutate_rails_cli_switch.py's cases for terminal-fun. NOT a test file.

They quote its own CLI_SLOTS and its chip call. A case lives with the rail whose line it quotes, so
this module ships exactly when terminal-fun does; cli_switch_cases/__init__.py says why.
"""
from __future__ import annotations

from . import FLAG

TF = "rails/terminal-fun/backend/terminal_fun_app/main.py"

MUTATIONS: list[tuple[str, str, str, str, list[str]]] = [
    # The two inline-allowance cases went with the allowance (2026-10-09): terminal-fun's chip
    # now passes its own CLI_SLOTS, so the scan holds it like every other rail. The case below
    # is the one that allowance used to excuse.
    (
        "terminal-fun's chip passes an inline literal again",
        TF,
        "                              requester=ident.user, cli_slots=CLI_SLOTS)",
        '                              requester=ident.user, cli_slots=("assistant",))',
        [FLAG],
    ),
    (
        "terminal-fun rebinds its CLI_SLOTS through setattr",
        TF,
        'CLI_SLOTS = ("assistant",)\n',
        'CLI_SLOTS = ("assistant",)\n'
        'setattr(__import__("sys").modules[__name__], "CLI_SLOTS", ("assistant", "vision"))\n',
        [FLAG],
    ),
]
