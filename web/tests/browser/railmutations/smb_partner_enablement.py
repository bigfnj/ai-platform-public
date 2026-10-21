"""mutate_railcheck.py's mutations of smb-partner-enablement's frontend. NOT a test file.

Each names the check in railchecks/smb_partner_enablement.py that must fail. mutate_railcheck.py's header
says how a run works, and why this file ships exactly when its rail does.
"""
from __future__ import annotations

from . import Mutation

MUTATIONS = [
    # smb-partner-enablement: the Why aside, the phone's safe area, the device icons.
    # `false && <MicIcon />` keeps the component read for tsc.
    Mutation("the card's paragraph rule outranks the Why aside again", "smb-partner-enablement",
             "theme.css", ".smbp .why,\n.smbp .card p.why {", ".smbp .why {", "smb-why-aside"),
    Mutation("the phone screen has no safe area again", "smb-partner-enablement", "theme.css",
             "  padding: 59px 0 34px;\n", "", "smb-mobile-safe-area"),
    Mutation("only the top inset is kept, so the home bar crosses the app", "smb-partner-enablement",
             "theme.css", "  padding: 59px 0 34px;\n", "  padding: 59px 0 0;\n", "smb-mobile-safe-area"),
    Mutation("the microphone is the grey emoji again", "smb-partner-enablement", "module.tsx",
             "<MicIcon /></button>", "🎙️{false && <MicIcon />}</button>", "smb-device-icons"),
    Mutation("the speaker is the grey emoji again", "smb-partner-enablement", "module.tsx",
             "<SpeakerIcon /></button>", "🔊{false && <SpeakerIcon />}</button>", "smb-device-icons"),
    Mutation("the device menus open leftward, off the rail, again", "smb-partner-enablement", "theme.css",
             "  left: 0;\n  z-index: 200;\n", "  right: 0;\n  z-index: 200;\n", "smb-device-menus"),
]
