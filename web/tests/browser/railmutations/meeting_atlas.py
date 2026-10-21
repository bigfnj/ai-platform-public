"""mutate_railcheck.py's mutations of meeting-atlas's frontend. NOT a test file.

Each names the check in railchecks/meeting_atlas.py that must fail. mutate_railcheck.py's header
says how a run works, and why this file ships exactly when its rail does.
"""
from __future__ import annotations

from . import Mutation

MUTATIONS = [
    # useMeasure measuring once, after the first commit, which is the loading screen.
    Mutation("meeting-atlas's chart width is measured only on the first commit again",
             "meeting-atlas", "module.tsx", "  }, [el])\n", "  }, [])\n", "ma-activity-chart"),
]
