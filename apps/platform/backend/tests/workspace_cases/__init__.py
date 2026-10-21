"""mutate_workspace_delegation.py's rail cases, one module per rail (with `-` spelled `_`).

A case lives with the rail it is about: one whose expected tests only that rail's tiles let run
(they skip in a tree without them). So a module goes wherever its rail goes, and the public
snapshot withholds a withheld rail's module (tools/publish.json): there publish empties both
allowlists and those tests skip, and since this harness wants every expected test to fail, a case
shipped with them could only report FIRED-WRONG-TEST. The cases the harness keeps itself expect
only tests that every tree runs.

Each module's MUTATIONS has the harness's tuple shape: (label, old, new, expected tests), each a
rewrite of the gateway's main.py. mutate_workspace_delegation.py names every module it expects
(RAIL_MODULES), refuses one it does not, and refuses a missing one except in a published tree
that does not carry its rail either: the pattern of mutate_gateway_upkeep.py's upkeep_cases/.
"""
