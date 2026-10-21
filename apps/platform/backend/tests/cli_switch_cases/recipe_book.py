"""mutate_rails_cli_switch.py's cases for recipe-book. NOT a test file.

They quote its vision and icon slots' rows in the gateway's catalog, and its own CLI_SLOTS. A case
lives with the rail whose line it quotes, so this module ships exactly when recipe-book does;
cli_switch_cases/__init__.py says why.
"""
from __future__ import annotations

from . import CAT, FLAG

RB = "rails/recipe-book/src/recipe_book/api/app.py"

MUTATIONS: list[tuple[str, str, str, str, list[str]]] = [
    # The gateway's rules for an image slot, planted on recipe-book's icon row: it is the
    # catalog's one image slot.
    (
        "the catalog wires its image slot",
        CAT,
        '         "env": "RECIPE_BOOK_ICON_MODEL", "default": "flux-schnell",\n',
        '         "env": "RECIPE_BOOK_ICON_MODEL", "default": "flux-schnell", "cli": True,\n',
        ["test_the_flag_is_a_boolean_and_only_a_text_or_vision_slot_may_set_it"],
    ),
    # --- the refusal is per role: no role is an image slot's and a text or vision slot's --------
    (
        "an image slot shares a text slot's role",
        CAT,
        '{"slot": "icon", "label": "Recipe icon images", "role": "recipe-icon", "kind": "image",',
        '{"slot": "icon", "label": "Recipe icon images", "role": "recipe", "kind": "image",',
        ["test_no_role_backs_an_image_slot_and_a_text_or_vision_slot"],
    ),
    (
        "an image slot shares a vision slot's role",
        CAT,
        '{"slot": "icon", "label": "Recipe icon images", "role": "recipe-icon", "kind": "image",',
        '{"slot": "icon", "label": "Recipe icon images", "role": "recipe-vision", "kind": "image",',
        ["test_no_role_backs_an_image_slot_and_a_text_or_vision_slot"],
    ),
    (
        "the catalog drops recipe-book's vision flag, which its rail's CLI_SLOTS opts in",
        CAT,
        '         "env": "RECIPE_BOOK_VISION_MODEL", "default": "gemma4*:26b", "cli": True,\n',
        '         "env": "RECIPE_BOOK_VISION_MODEL", "default": "gemma4*:26b",\n',
        [FLAG],
    ),
    (
        "recipe-book drops vision from CLI_SLOTS while the catalog still calls it wired",
        RB,
        'CLI_SLOTS = ("assistant", "vision")',
        'CLI_SLOTS = ("assistant",)',
        [FLAG],
    ),
]
