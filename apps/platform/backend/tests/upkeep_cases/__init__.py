"""mutate_gateway_upkeep.py's rail cases, one module per rail (with `-` spelled `_`).

A case lives with the rail it is about: one that quotes a rail's tile id (config.py's PEER_TILES
and ROOM_TILES, one tile a line), or whose expected tests only that rail's tile lets run (they
skip in a tree without it). So a module goes wherever its rail goes, and the public snapshot
withholds a withheld rail's module (tools/publish.json): there publish empties both allowlists
and those tests skip, so a case shipped with them could only report PATTERN or SURVIVED. The
cases the harness keeps itself quote only the gateway's own code and expect only tests that every
tree runs.

Each module's MUTATIONS has the harness's tuple shape: (label, file, old, new, expected tests).
mutate_gateway_upkeep.py names every module it expects (RAIL_MODULES), refuses one it does not,
and refuses a missing one except in a published tree that does not carry its rail either: the
pattern of mutate_rails_cli_switch.py's cli_switch_cases/.
"""
from __future__ import annotations

MAIN = "apps/platform/backend/platform_gateway_app/main.py"
CONFIG = "apps/platform/backend/platform_gateway_app/config.py"
RAILS = "apps/platform/backend/platform_gateway_app/rails_models.py"

#: The gateway's own files a rail's case may rewrite: the lines there that name the rail.
GATEWAY = (MAIN, CONFIG, RAILS)

#: Tests in apps/platform/backend/tests/test_gate_queries.py that every tree runs.
HANDSHAKES = "test_each_websocket_handshake_carries_only_the_list_its_tile_reads"
ROOT_ASSET = "test_a_root_asset_works_out_only_its_owners_list"
