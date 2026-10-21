"""mutate_rails_cli_switch.py's rail cases, one module per rail (with `-` spelled `_`).

A case lives with the rail whose line it quotes: that rail's own source, or its slot rows in the
gateway's catalog (rails_models.py), which tools/publish.py de-wires when it withholds the rail.
So a module goes wherever its rail goes, and the public snapshot withholds a withheld rail's
module (tools/publish.json) rather than shipping its quoted code to skip it there. The cases the
harness keeps itself quote only the gateway's own code: main.py, the catalog's shared rules, the
tests and conftest.

Each module's MUTATIONS has the harness's tuple shape: (label, file, old, new, expected tests).
mutate_rails_cli_switch.py names every module it expects (RAIL_MODULES), refuses one it does not,
and refuses a missing one except in a published tree that does not carry its rail either.
"""
from __future__ import annotations

CAT = "apps/platform/backend/platform_gateway_app/rails_models.py"
FLAG = "test_the_cli_flag_is_each_rails_own_cli_slots_opt_in"
