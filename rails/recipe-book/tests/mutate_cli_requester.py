"""Mutation harness for `requester` in recipe-book. NOT a pytest file: it rewrites the tree.

    .venv\\Scripts\\python.exe rails\\recipe-book\\tests\\mutate_cli_requester.py

Run this, not just pytest, before trusting a change to `broker._cli_requester`,
`deps.cli_requester`, `assistant._own_role_requester`, the capabilities chip, or any
assistant-role or vision-role call that passes the name.

Every guard here fails OPEN and SILENTLY. Drop the role check and a client naming `@iep` gets a
better answer from Claude on the owner's subscription with recipe-book's own switch off; drop
the view-as check and an admin filling another user's meal plan bills it to themselves. A guard
whose removal makes the product look BETTER is the one that most needs a test that fails. Its
sibling in another rail is rails/terminal-fun/backend/tests/mutate_cli_requester.py.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# The shared runner (tools/tests/mutation_runner.py): a timeout kills the whole process tree.
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / "tools" / "tests" / "mutation_runner.py").is_file())
                       / "tools" / "tests"))
import mutation_runner as mr  # noqa: E402

RAIL = Path(__file__).resolve().parents[1]              # rails/recipe-book
REPO = RAIL.parents[1]
PY = sys.executable
# THIS tree's sources first, so a worktree run tests the worktree.
PYTHONPATH = [str(RAIL / "src")]
TIMEOUT_S = 600

SUITE = [str(RAIL / "tests" / "test_cli_requester.py")]

PKG = RAIL / "src" / "recipe_book"
BROKER = PKG / "broker.py"
DEPS = PKG / "api" / "deps.py"
APP = PKG / "api" / "app.py"
ASSISTANT = PKG / "api" / "routers" / "assistant.py"
AUTHORING = PKG / "api" / "routers" / "authoring.py"
PLANNER = PKG / "api" / "routers" / "planner.py"
PLANNER_AI = PKG / "planner_ai.py"
ICON_PROMPTS = PKG / "icon_prompts.py"

MUTATIONS: list[tuple[str, Path, str, str, str]] = [
    # A photo drifting onto the TEXT role: it would reach Claude on @recipe's switch while
    # @recipe-vision is off, and the gateway's image check never applies to a text role.
    (
        "the owner's photo import runs on the assistant's text role",
        AUTHORING,
        "        model = broker.VISION_MODEL\n",
        "        model = broker.ASSISTANT_MODEL\n",
        "test_a_photo_import_names_the_caller_on_the_vision_role",
    ),
    # --- the guards: each one, removed, sends somebody else's request under an owner's name ---
    (
        "a client-picked foreign role rides the owner's name to the CLI",
        BROKER,
        "    return requester if requester and model in (ASSISTANT_MODEL, VISION_MODEL) else None",
        "    return requester",
        "test_a_foreign_role_never_carries_the_name",
    ),
    (
        "a client-picked vision role on /api/assistant rides the vision slot's switch",
        ASSISTANT,
        "    return requester if model == broker.ASSISTANT_MODEL else None",
        "    return requester",
        "test_a_client_picked_vision_role_on_the_assistant_stays_local",
    ),
    (
        "the photo import names the admin while they view as another user",
        AUTHORING,
        "                     requester: str | None = Depends(deps.cli_requester)) -> dict:\n"
        '    """Extract one recipe from one or more uploaded files/photos',
        "                     requester: str | None = Depends(\n"
        "                         lambda ident=Depends(deps.identity): ident.user)) -> dict:\n"
        '    """Extract one recipe from one or more uploaded files/photos',
        "test_a_photo_import_while_viewing_as_another_user_stays_local",
    ),
    (
        "the icon image batch starts carrying a name",
        BROKER,
        '"model": model or ICON_MODEL},',
        '"model": model or ICON_MODEL, "requester": "owner-one"},',
        "test_the_icon_image_batch_never_names_anyone",
    ),
    (
        "an admin acting as another user bills that user's plan to themselves",
        DEPS,
        "    return ident.user if acting_as == ident.user else None",
        "    return ident.user",
        "test_an_admin_acting_as_another_user_stays_local",
    ),
    (
        "the view-as match is loosened to a case-insensitive one",
        DEPS,
        "    return ident.user if acting_as == ident.user else None",
        "    return ident.user if acting_as.lower() == ident.user.lower() else None",
        "test_names_are_compared_byte_exact",
    ),
    (
        "the background icon-subject batch starts carrying a name",
        ICON_PROMPTS,
        '                options={"temperature": 0.7})',
        '                options={"temperature": 0.7}, requester="owner-one")',
        "test_the_icon_subject_batch_never_names_anyone",
    ),
    # --- wiring: each one makes the CLI silently inert for that call, which looks healthy ---
    (
        "the facade never puts requester on the wire",
        BROKER,
        '    if who:\n        payload["requester"] = who',
        '    if False:\n        payload["requester"] = who',
        "test_the_assistant_role_carries_the_requester",
    ),
    (
        "the facade sends an absent requester as null",
        BROKER,
        '    if who:\n        payload["requester"] = who',
        '    payload["requester"] = who',
        "test_no_requester_means_none_on_the_wire",
    ),
    (
        "chat_json stops forwarding it",
        BROKER,
        '    content = chat(model, messages, options=options, fmt="json", keep_alive=keep_alive,\n'
        "                   requester=requester)",
        '    content = chat(model, messages, options=options, fmt="json", keep_alive=keep_alive)',
        "test_chat_json_puts_the_requester_on_the_wire",
    ),
    (
        "the facade drops the vision role, so the photo import never reaches Claude",
        BROKER,
        "    return requester if requester and model in (ASSISTANT_MODEL, VISION_MODEL) else None",
        "    return requester if requester and model == ASSISTANT_MODEL else None",
        "test_the_vision_role_carries_the_requester",
    ),
    (
        "the photo path stops handing the caller to the vision call",
        AUTHORING,
        "        model = broker.VISION_MODEL",
        "        model, requester = broker.VISION_MODEL, None",
        "test_a_photo_import_names_the_caller_on_the_vision_role",
    ),
    (
        "the facade's transport stops waiting the rail's broker timeout",
        BROKER,
        "def _post(path: str, payload: dict, timeout: float = DEFAULT_TIMEOUT) -> dict:",
        "def _post(path: str, payload: dict, timeout: float = 120.0) -> dict:",
        "test_the_photo_import_waits_the_rails_full_broker_timeout",
    ),
    (
        "the assistant route drops it",
        ASSISTANT,
        '        ], options={"temperature": 0.5, "num_ctx": 8192},\n'
        "            requester=_own_role_requester(model, requester))",
        '        ], options={"temperature": 0.5, "num_ctx": 8192})',
        "test_the_assistant_route_names_the_caller",
    ),
    (
        "draft and import (distill) drop it",
        AUTHORING,
        '        d = broker.chat_json(model, [{"role": "system", "content": _DISTILL_SYS}, user_msg],\n'
        "                             requester=requester)",
        '        d = broker.chat_json(model, [{"role": "system", "content": _DISTILL_SYS}, user_msg])',
        "test_every_authoring_call_names_the_caller",
    ),
    (
        "refine drops it",
        AUTHORING,
        '                              {"role": "user", "content": user}],\n'
        "                             requester=requester)",
        '                              {"role": "user", "content": user}])',
        "test_every_authoring_call_names_the_caller",
    ),
    (
        "propose drops it",
        PLANNER_AI,
        '                            options={"temperature": 0.7}, requester=requester)',
        '                            options={"temperature": 0.7})',
        "test_the_planner_names_the_caller_on_their_own_plan",
    ),
    (
        "the wine swap drops it",
        PLANNER_AI,
        '                             options={"temperature": 0.9}, requester=requester)\n'
        '        wine = ',
        '                             options={"temperature": 0.9})\n'
        '        wine = ',
        "test_the_planner_names_the_caller_on_their_own_plan",
    ),
    (
        "the pair route stops handing it to swap",
        PLANNER,
        '                               context=" and ".join(dishes),\n'
        '                               requester=requester).get("item")',
        '                               context=" and ".join(dishes)).get("item")',
        "test_the_planner_names_the_caller_on_their_own_plan",
    ),
    (
        "the meal swap drops it",
        PLANNER_AI,
        '                         options={"temperature": 0.9}, requester=requester)\n'
        "    r = cat.get",
        '                         options={"temperature": 0.9})\n'
        "    r = cat.get",
        "test_a_meal_swap_names_the_caller_on_the_assistant_role",
    ),
    # --- the role: each one moves a named TEXT call onto the vision role. The facade keeps the
    # name there, so the owner's text rides the vision slot's switch to Claude while the
    # assistant chip reads Ollama. A name-only assertion passes all five; `calls` fails them ---
    (
        "the text import (distill) drifts onto the vision role",
        AUTHORING,
        "        model = broker.ASSISTANT_MODEL",
        "        model = broker.VISION_MODEL",
        "test_every_authoring_call_names_the_caller",
    ),
    (
        "refine drifts onto the vision role",
        AUTHORING,
        "        d = broker.chat_json(broker.ASSISTANT_MODEL,\n"
        '                             [{"role": "system", "content": _REFINE_SYS},',
        "        d = broker.chat_json(broker.VISION_MODEL,\n"
        '                             [{"role": "system", "content": _REFINE_SYS},',
        "test_every_authoring_call_names_the_caller",
    ),
    (
        "propose drifts onto the vision role",
        PLANNER_AI,
        "    data = broker.chat_json(broker.ASSISTANT_MODEL,",
        "    data = broker.chat_json(broker.VISION_MODEL,",
        "test_the_planner_names_the_caller_on_their_own_plan",
    ),
    (
        "the wine swap drifts onto the vision role",
        PLANNER_AI,
        "        d = broker.chat_json(broker.ASSISTANT_MODEL,",
        "        d = broker.chat_json(broker.VISION_MODEL,",
        "test_the_planner_names_the_caller_on_their_own_plan",
    ),
    (
        "the meal swap drifts onto the vision role",
        PLANNER_AI,
        "    d = broker.chat_json(broker.ASSISTANT_MODEL,\n"
        '                         [{"role": "system", "content": sys}',
        "    d = broker.chat_json(broker.VISION_MODEL,\n"
        '                         [{"role": "system", "content": sys}',
        "test_a_meal_swap_names_the_caller_on_the_assistant_role",
    ),
    (
        "the chip stops opting the assistant in, so an owner is told their work runs locally",
        APP,
        "        ], requester=requester, cli_slots=CLI_SLOTS)",
        "        ])",
        "test_an_owner_sees_the_assistant_and_vision_on_claude",
    ),
    (
        "the chip stops opting the vision slot in, so an owner's photo import looks local",
        APP,
        'CLI_SLOTS = ("assistant", "vision")',
        'CLI_SLOTS = ("assistant",)',
        "test_an_owner_sees_the_assistant_and_vision_on_claude",
    ),
    (
        "the chip judges the raw caller instead of the identity the calls carry",
        APP,
        "    def capabilities(requester: str | None = Depends(deps.cli_requester)) -> dict:",
        "    def capabilities(requester: str | None = Depends(\n"
        "            lambda ident=Depends(deps.identity): ident.user)) -> dict:",
        "test_an_admin_viewing_as_another_user_sees_ollama",
    ),
]


def _read(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def _write(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    if _read(path) != text:
        raise RuntimeError(f"{path} did not read back as written -- TREE IS DIRTY")


def _eol(pattern: str, text: str) -> str:
    """Patterns are written with \\n; match them in the file's own line ending."""
    return pattern.replace("\n", "\r\n") if "\r\n" in text else pattern


def witness() -> str | None:
    """Prove the suite imports THIS tree's recipe_book, or say which one it imports."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(PYTHONPATH)
    probe = ("import recipe_book.broker as b, recipe_book.api.deps as d\n"
             "for m in (b, d): print('WITNESS', m.__file__)")
    p = mr.run_tree([PY, "-c", probe], cwd=REPO, capture_output=True, text=True, env=env,
                    timeout=120)
    files = [Path(ln[len("WITNESS "):]).resolve() for ln in p.stdout.splitlines()
             if ln.startswith("WITNESS ")]
    if p.returncode != 0 or len(files) != 2:
        return f"import probe failed:\n{p.stdout}{p.stderr}"
    stray = [str(f) for f in files if REPO.resolve() not in f.parents]
    return f"imports resolve outside this tree: {stray}" if stray else None


def run_suite() -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(PYTHONPATH)
    env.pop("PLATFORM_STANDALONE", None)
    try:
        p = mr.run_tree([PY, "-m", "pytest", *SUITE, "-q", "--no-header",
                         "-p", "no:cacheprovider"],
                        cwd=RAIL, capture_output=True, text=True, env=env,
                        timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT: the suite ran past {TIMEOUT_S}s"
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    mr.parse_harness_args(MUTATIONS)
    if (why := witness()) is not None:
        print(f"WITNESS FAILED -- {why}")
        return 2
    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, path, old, new, expected in MUTATIONS:
        original = _read(path)
        old, new = _eol(old, original), _eol(new, original)
        n = original.count(old)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {path.name}, expected 1")
            results.append((label, "PATTERN"))
            continue
        try:
            _write(path, original.replace(old, new))
            code, out = run_suite()
            fired = code != 0
            named = mr.named_failed(out, expected)
            verdict = "FIRED" if fired and named else (
                "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            _write(path, original)

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
