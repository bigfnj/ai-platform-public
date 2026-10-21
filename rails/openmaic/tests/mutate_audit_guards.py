"""Mutation harness for this rail's credential boundary. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe rails\\openmaic\\tests\\mutate_audit_guards.py

Run this, not just pytest, before trusting a change to api/proxy.py's header and cookie
filtering (what openmaic-app is sent, and which of its Set-Cookies reach the browser) or to
broker.py's token resolver.

Both fail QUIETLY. Relay the platform session again and the wrapped third-party app holds the
owner's super-admin cookie while every page still loads; drop the file form of the broker token
and the rail goes out tokenless on a switched stack while RC005, which passes on either name,
stays green. Each guard is broken on purpose below to prove a test notices.

Each mutation names the test(s) that must fail for it. A red run that fails some OTHER test is
FIRED-WRONG-TEST, not FIRED: it proves the tree broke, not that the guard is held.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# The shared runner (tools/tests/mutation_runner.py): a timeout kills the whole process tree,
# and a restore is byte for byte and verified. Found by walking up, so it runs from any tree.
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / "tools" / "tests" / "mutation_runner.py").is_file())
                       / "tools" / "tests"))
import mutation_runner as mr  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
RAIL = REPO / "rails" / "openmaic"
RAIL_SRC = RAIL / "backend"
PY = sys.executable
SUITE = ["tests"]
RUN_TIMEOUT = 300

P = "rails/openmaic/backend/openmaic_app/api/proxy.py"
B = "rails/openmaic/backend/openmaic_app/broker.py"
FV = "rails/openmaic/frontend/src/frameView.ts"
NODE = "test_frame_view_passes_under_node"
VP = "rails/openmaic/frontend/src/visiblePoll.ts"
VP_NODE = "test_visible_poll_passes_under_node"
FT = "rails/openmaic/frontend/src/frameTheme.ts"
FT_NODE = "test_frame_theme_passes_under_node"
SHIM = "rails/openmaic/tools/platform-api-prefix.ts"
SHIM_NODE = "test_api_prefix_passes_under_node"
TOOL = "rails/openmaic/tools/platform-basepath.py"
README = "rails/openmaic/README.md"
SMOKE = "rails/openmaic/tools/smoke-app-image.py"
FIXTURE_TYPES = "rails/openmaic/tests/fixtures/openmaic-v1.1.3/lib/i18n/types.ts"

# (label, [(file, old, new), ...], tests that must fail). Every `old` must occur exactly once.
MUTATIONS: list[tuple[str, list[tuple[str, str, str]], list[str]]] = [
    (
        "the platform session cookie is relayed to the app again (the audit's finding)",
        [(P, '    return "; ".join(p for p in pairs if _cookie_name(p) not in _PLATFORM_COOKIES)',
             '    return "; ".join(p for p in pairs)')],
        ["test_the_platform_session_cookie_never_reaches_the_app",
         "test_a_request_carrying_only_the_session_sends_no_cookie_header",
         "test_case_variants_and_cloudflare_access_cookies_are_stripped_too"],
    ),
    (
        "the raw Cookie header is copied and only replaced when something survives",
        [(P, '    "authorization", "cookie", "cf-access-jwt-assertion",',
             '    "authorization", "cf-access-jwt-assertion",')],
        ["test_a_request_carrying_only_the_session_sends_no_cookie_header"],
    ),
    (
        "Authorization reaches the third-party app",
        [(P, '    "authorization", "cookie", "cf-access-jwt-assertion",',
             '    "cookie", "cf-access-jwt-assertion",')],
        ["test_no_credential_header_reaches_the_app"],
    ),
    (
        "Cloudflare Access's own assertion reaches the app",
        [(P, '    "authorization", "cookie", "cf-access-jwt-assertion",',
             '    "authorization", "cookie",')],
        ["test_no_credential_header_reaches_the_app"],
    ),
    (
        "the gateway's x-platform-admin / -peers / -rooms are relayed",
        [(P, '               and not k.lower().startswith("x-platform-")}',
             "               }")],
        ["test_only_the_resolved_user_crosses_of_the_platforms_headers"],
    ),
    (
        "cookie names are compared case-sensitively, so a case variant slips through",
        [(P, '    return pair.split("=", 1)[0].strip().lower()',
             '    return pair.split("=", 1)[0].strip()')],
        ["test_case_variants_and_cloudflare_access_cookies_are_stripped_too",
         "test_the_app_cannot_set_the_platform_session"],
    ),
    (
        "split Cookie headers: only the first is kept",
        [(P, '    pairs = [p.strip() for v in values for p in v.split(";") if p.strip()]',
             '    pairs = [p.strip() for v in values[:1] for p in v.split(";") if p.strip()]')],
        ["test_app_cookies_joins_split_cookie_headers"],
    ),
    (
        "the app's Set-Cookie of platform_session reaches the browser again",
        [(P, "        if k.lower() not in _HOP_BY_HOP and not _sets_a_platform_cookie(k, v)\n",
             "        if k.lower() not in _HOP_BY_HOP\n")],
        ["test_the_app_cannot_set_the_platform_session", "test_the_refusal_is_logged"],
    ),
    (
        "over-correction: every Set-Cookie is dropped, so the app cannot keep its own session",
        [(P, '    if name.lower() != "set-cookie" or _cookie_name(value) not in _PLATFORM_COOKIES:',
             '    if name.lower() != "set-cookie":')],
        ["test_the_app_cannot_set_the_platform_session"],
    ),
    (
        "over-correction: ordinary request headers are stripped with the credentials",
        [(P, "               if k.lower() not in _HOP_BY_HOP and k.lower() != \"host\"\n",
             "               if k.lower() in (\"x-forwarded-prefix\",)\n")],
        ["test_ordinary_headers_are_still_relayed"],
    ),
    # --- the broker token's file form ---------------------------------------------------
    (
        "BROKER_AUTH_TOKEN_FILE is ignored (the audit's finding)",
        [(B, '    path = os.environ.get("BROKER_AUTH_TOKEN_FILE", "").strip()\n',
             '    path = ""\n')],
        ["test_reads_the_file", "test_file_wins_over_the_variable",
         "test_missing_file_does_not_fall_back_to_the_variable"],
    ),
    (
        "an unreadable token file falls back to the environment the stack moved off",
        [(B, '        except OSError:\n            return ""\n',
             '        except OSError:\n            return os.environ.get("BROKER_AUTH_TOKEN", "").strip()\n')],
        ["test_missing_file_does_not_fall_back_to_the_variable"],
    ),
    # --- one failed status probe must not unmount a class in progress (frontend, node) -----
    (
        "a failed probe unmounts the frame again, whatever was open in it",
        [(FV, "  if (reachable === false) return everReachable ? 'frame-warning' : 'down'\n",
              "  if (reachable === false) return 'down'\n")],
        [NODE],
    ),
    (
        "over-correction: an app that never answered shows a warning over a dead frame",
        [(FV, "  if (reachable === false) return everReachable ? 'frame-warning' : 'down'\n",
              "  if (reachable === false) return 'frame-warning'\n")],
        [NODE],
    ),
    # --- the header poll skips a hidden page (frontend, under node) --------------------------
    (
        "a hidden page's ticks still poll (the audit's finding)",
        [(VP, "  const stopTimer = every(() => { if (!env.hidden()) load() })\n",
              "  const stopTimer = every(() => { load() })\n")],
        [VP_NODE],
    ),
    (
        "no catch-up on return, so the chips and the frame stay stale until the next tick",
        [(VP, "  const stopListening = env.onVisibilityChange(() => { if (!env.hidden()) load() })\n",
              "  const stopListening = env.onVisibilityChange(() => {})\n")],
        [VP_NODE],
    ),
    (
        "the visibility listener outlives the component",
        [(VP, "    stopTimer()\n    stopListening()\n", "    stopTimer()\n")],
        [VP_NODE],
    ),
    # --- the request shim the patch tool writes into the app (node) --------------------------
    (
        "fetch passes a root /api/ string through, so it 404s at the gateway",
        [(SHIM, "      next = rewrite(input)\n", "      next = input\n")],
        [SHIM_NODE],
    ),
    (
        "over-correction: another origin's /api/ is moved under the prefix too",
        [(SHIM, "  if (target.origin !== page.origin || !target.pathname.startsWith(API)) return url\n",
                "  if (!target.pathname.startsWith(API)) return url\n")],
        [SHIM_NODE],
    ),
    (
        "the shim installs on a page that is not under the basePath (the shell's own document)",
        [(SHIM, "  if (path !== base && !path.startsWith(base + '/')) return false\n", "")],
        [SHIM_NODE],
    ),
    (
        "EventSource is left unwrapped",
        [(SHIM, "        super(rewrite(String(url)), init)\n", "        super(String(url), init)\n")],
        [SHIM_NODE],
    ),
    # --- the patch tool that installs it ------------------------------------------------------
    (
        "the env entry the shim reads is not written",
        [(TOOL, '        Edit("next.config.ts", API_MARK, ENV_ANCHOR, ENV_PATCH, _one_env_key),\n', "")],
        ["test_apply_writes_every_edit_and_the_shim",
         "test_a_checkout_patched_by_the_first_version_gets_the_new_edits"],
    ),
    (
        "the config defines a key the shim does not read, so the shim builds clean and does nothing",
        [(TOOL, "    PLATFORM_BASE_PATH: process.env.NEXT_BASE_PATH || '',\n",
                "    PLATFORM_BASEPATH: process.env.NEXT_BASE_PATH || '',\n")],
        ["test_the_shim_reads_the_key_the_config_defines", "test_apply_writes_every_edit_and_the_shim"],
    ),
    (
        # The v1.0.x shape of the edit: its own `env` key after assetPrefix. On v1.1.x that is a
        # second key beside upstream's, and the later of the two silently wins.
        "the key is added as a second env object instead of into upstream's",
        [(TOOL, '  env: {{\n    // {API_MARK}: the same prefix',
                '  env: {{ PLATFORM_BASE_PATH: process.env.NEXT_BASE_PATH }},\n  env: {{\n    // {API_MARK}: the same prefix')],
        ["test_apply_writes_every_edit_and_the_shim"],
    ),
    (
        "an anchor that occurs twice is patched at its first occurrence",
        [(TOOL, "        if text.count(anchor) != 1:\n", "        if anchor not in text:\n")],
        ["test_an_anchor_found_twice_is_refused"],
    ),
    (
        "the new edits share the old marker, so a checkout patched by the first version skips them",
        [(TOOL, 'API_MARK = "platform-api-prefix"\n', 'API_MARK = MARK\n')],
        ["test_a_checkout_patched_by_the_first_version_gets_the_new_edits"],
    ),
    (
        "anchors are matched in LF only, so a CRLF checkout is refused",
        [(TOOL, '    return s.replace("\\n", _eol(text)) if _eol(text) == "\\r\\n" else s\n', "    return s\n")],
        ["test_line_endings_are_kept_and_revert_is_byte_exact",
         "test_a_checkout_patched_by_the_first_version_gets_the_new_edits"],
    ),
    (
        # The first version's write path. Fires on Windows, where text mode writes CRLF.
        "files are written in text mode again, turning an LF file into CRLF",
        [(TOOL, "                path.write_bytes(data)\n",
                '                path.write_text(data.decode("utf-8"), encoding="utf-8")\n')],
        ["test_line_endings_are_kept_and_revert_is_byte_exact"],
    ),
    (
        "revert prints 'revert' over a patch it cannot find",
        [(TOOL, "            elif block not in text:\n", "            elif False:\n")],
        ["test_revert_refuses_when_the_patched_text_was_edited", "test_a_hand_edited_locale_block_is_refused"],
    ),
    (
        "upstream's own client instrumentation is written over",
        [(TOOL, "    foreign = [n for n in present if not (n == SHIM_TARGET and ours)]\n",
                "    foreign = [n for n in present if n != SHIM_TARGET]\n")],
        ["test_upstream_client_instrumentation_is_refused_and_nothing_is_written"],
    ),
    (
        "a second env key upstream adds is let through, so one of the two is silently dropped",
        [(TOOL, '    if len(re.findall(r"^\\s*env\\s*:", text, re.M)) > 1:\n', "    if False:\n")],
        ["test_a_second_env_key_is_refused_and_nothing_is_written"],
    ),
    # --- the patch tool's defaultLocale edit ---------------------------------------------------
    (
        "defaultLocale is left at upstream's zh-CN",
        [(TOOL, "        Edit(LOCALE_FILE, LOCALE_MARK, LOCALE_ANCHOR, locale_patch(locale),\n"
                "             form=locale_patch(_SLOT), value=locale),\n", "")],
        ["test_apply_writes_every_edit_and_the_shim", "test_only_the_default_locale_line_changes"],
    ),
    (
        "the default is upstream's locale, not en-US",
        [(TOOL, 'DEFAULT_LOCALE = "en-US"\n', 'DEFAULT_LOCALE = UPSTREAM_LOCALE\n')],
        ["test_apply_writes_every_edit_and_the_shim", "test_only_the_default_locale_line_changes"],
    ),
    (
        "a re-run with another locale reports 'already patched' and keeps the old one",
        [(TOOL, "            if held != e.value:\n", "            if False:\n")],
        ["test_a_rerun_with_another_locale_updates_it_in_place"],
    ),
    (
        "the block is looked for with the requested locale only, so one applied with another is lost",
        [(TOOL, "                block, held = m.group(0), m.group(1)\n", "                held = m.group(1)\n")],
        ["test_a_rerun_with_another_locale_updates_it_in_place"],
    ),
    (
        "any --default-locale is written into the source, a quote included",
        [(TOOL, "        if locale not in known:\n", "        if False:\n")],
        ["test_a_locale_upstream_does_not_ship_is_refused_and_nothing_is_written"],
    ),
    (
        "a hand-edited locale block is reported 'already patched'",
        [(TOOL, "            if e.form is not None and block not in text:\n", "            if False:\n")],
        ["test_a_hand_edited_locale_block_is_refused"],
    ),
    # --- the image build the patch is for ----------------------------------------------------
    (
        "the printed build command drops the Pi chat flag, so a rebuild needs tool calling",
        [(TOOL, '                 " --build-arg NEXT_PUBLIC_PI_CHAT_ENABLED=false .")\n',
                '                 " .")\n')],
        ["test_the_readme_builds_what_the_patcher_prints"],
    ),
    (
        "the README's build command drifts from the script's",
        [(README, "--build-arg NEXT_BASE_PATH=/openmaic/api/app --build-arg NEXT_PUBLIC_PI_CHAT_ENABLED=false .\n",
                  "--build-arg NEXT_BASE_PATH=/openmaic/api/app .\n")],
        ["test_the_readme_builds_what_the_patcher_prints"],
    ),
    (
        "the build tags the bare name, which podman stores as localhost/ and compose misses",
        [(TOOL, 'IMAGE = "docker.io/library/openmaic-app"\n', 'IMAGE = "openmaic-app"\n')],
        ["test_every_tag_the_readme_and_the_patcher_give_is_the_one_compose_finds"],
    ),
    (
        "the README's rollback retags the bare name",
        [(README, "To roll back: `docker tag docker.io/library/openmaic-app:pre-<date>\n",
                  "To roll back: `docker tag openmaic-app:pre-<date>\n")],
        ["test_every_tag_the_readme_and_the_patcher_give_is_the_one_compose_finds"],
    ),
    # --- the patch tool's /app/data check (2026-10-14) ----------------------------------------
    (
        "upstream's own /app/data line is not recognised, so a second one is added beside it",
        [(TOOL, "    if found:\n", "    if False:\n")],
        ["test_upstreams_own_data_dir_line_counts_as_done_and_nothing_is_written_for_it"],
    ),
    (
        "the line lands after USER, where a non-root chown fails the build",
        [(TOOL, "    return text[:at] + _in(text, data_dir_block(user)) + text[at:]",
                "    return text + _in(text, data_dir_block(user))")],
        ["test_a_dockerfile_without_it_gets_the_line_before_user"],
    ),
    (
        "a runtime stage with two USER lines is patched at the first",
        [(TOOL, "    if len(users) != 1:\n", "    if not users:\n")],
        ["test_a_runtime_stage_without_one_user_line_is_refused"],
    ),
    (
        "--revert leaves the /app/data line in",
        [(TOOL, "    if revert:\n        data_dir()\n", "    if False:\n        data_dir()\n")],
        ["test_a_dockerfile_without_it_gets_the_line_before_user"],
    ),
    (
        "a hand-edited /app/data block reads as already patched",
        [(TOOL, "    if DATA_MARK in text:\n        if m is None:\n",
                "    if DATA_MARK in text:\n        if False:\n")],
        ["test_a_hand_edited_data_dir_block_is_refused"],
    ),
    (
        "a patched line whose user USER no longer names reads as already patched (the BACKLOG item)",
        [(TOOL, "        if m.group(1) != user:\n", "        if False:\n")],
        ["test_a_patched_line_whose_user_the_runtime_stage_no_longer_runs_as_is_refused"],
    ),
    (
        "a patched line outside the runtime stage before USER reads as already patched (the BACKLOG item)",
        [(TOOL, "        if not stage_at <= m.start() < at:\n", "        if False:\n")],
        ["test_a_patched_line_outside_the_runtime_stage_before_user_is_refused"],
    ),
    (
        "only the stage is checked, so a patched line after USER reads as already patched",
        [(TOOL, "        if not stage_at <= m.start() < at:\n", "        if not stage_at <= m.start():\n")],
        ["test_a_patched_line_outside_the_runtime_stage_before_user_is_refused"],
    ),
    (
        "only USER is checked, so a patched line in an earlier stage reads as already patched",
        [(TOOL, "        if not stage_at <= m.start() < at:\n", "        if not m.start() < at:\n")],
        ["test_a_patched_line_outside_the_runtime_stage_before_user_is_refused"],
    ),
    # --- the smoke tool's volume and image name (2026-10-14) ----------------------------------
    (
        "podman's volume is prepared too, spending its first mount (the data-dir failure)",
        [(SMOKE, "        if not self.podman:\n", "        if True:\n")],
        ["test_podman_gets_a_fresh_volume_so_the_apps_mount_is_its_first"],
    ),
    (
        "podman named by a full path reads as docker",
        [(SMOKE, '    return Path(cli).name.lower().startswith("podman")\n',
                 '    return cli == "podman"\n')],
        ["test_podman_gets_a_fresh_volume_so_the_apps_mount_is_its_first"],
    ),
    (
        "the image is looked for only under the name given",
        [(SMOKE, '    return list(dict.fromkeys([name, *qualified])) if "/" in name else qualified\n',
                 "    return [name]\n")],
        ["test_a_registry_less_name_is_found_under_either_spelling",
         "test_an_image_under_neither_spelling_is_a_clear_refusal"],
    ),
    (
        "the containers run the name asked for, not the one found",
        [(SMOKE, "    s = Smoke(image, args.skip_handoff, cli, podman)\n",
                 "    s = Smoke(args.image, args.skip_handoff, cli, podman)\n")],
        ["test_a_registry_less_name_is_found_under_either_spelling"],
    ),
    # --- the smoke tool's short names and engine (delta audit, 2026-10-15) --------------------
    (
        "a bare name is looked up as given first, so podman finds a stale localhost build",
        [(SMOKE, '    return list(dict.fromkeys([name, *qualified])) if "/" in name else qualified\n',
                 "    return list(dict.fromkeys([name, *qualified]))\n")],
        ["test_a_bare_name_smokes_composes_image_not_a_stale_localhost_build"],
    ),
    (
        "localhost's spelling is tried before compose's",
        [(SMOKE, '    qualified = [f"docker.io/library/{bare}", f"localhost/{bare}"]\n',
                 '    qualified = [f"localhost/{bare}", f"docker.io/library/{bare}"]\n')],
        ["test_a_bare_name_smokes_composes_image_not_a_stale_localhost_build"],
    ),
    (
        "an image id is qualified like a name",
        [(SMOKE, '        if "/" in name or _IMAGE_ID.fullmatch(name):\n',
                 '        if "/" in name:\n')],
        ["test_a_name_with_another_registry_or_a_namespace_is_taken_as_given"],
    ),
    (
        "podman is decided by the CLI's file name again (the BACKLOG item)",
        [(SMOKE, "    podman = is_podman(cli)\n",
                 '    podman = Path(cli).name.lower().startswith("podman")\n')],
        ["test_the_engine_not_the_cli_name_decides_podman"],
    ),
    (
        "the engine's own answer that it is podman is ignored",
        [(SMOKE, '    if re.search(r"\\bpodman\\b", said, re.I):\n        return True\n',
                 "    if False:\n        return True\n")],
        ["test_the_engine_not_the_cli_name_decides_podman"],
    ),
    (
        "data-dir reads the volume alone, so an image with no /app/data passes on podman",
        [(SMOKE, '        self.record("data-dir", bool(user) and owner == user\n',
                 '        self.record("data-dir", True\n')],
        ["test_an_image_without_its_own_data_dir_fails_even_on_a_writable_volume"],
    ),
    (
        "the fixture is edited to suit the patch, so it no longer tests upstream",
        [(FIXTURE_TYPES, "export const defaultLocale: Locale = 'zh-CN';\n",
                         "export const defaultLocale: Locale = 'en-US';\n")],
        ["test_the_fixture_is_upstream_v1_1_3_unedited"],
    ),
    # --- the shell's mode handed to the frame (node) -----------------------------------------
    (
        "the frame's storage is not written, so it starts from the OS again",
        [(FT, "    storage?.setItem(FRAME_THEME_KEY, mode)\n", "    void storage\n")],
        [FT_NODE],
    ),
    (
        "over-correction: System is pinned to today's OS answer instead of passed through",
        [(FT, "    storage?.setItem(FRAME_THEME_KEY, mode)\n",
              "    storage?.setItem(FRAME_THEME_KEY, resolvedMode(mode, prefersDark))\n")],
        [FT_NODE],
    ),
    (
        "an explicit shell mode loses to the OS",
        [(FT, "  return mode === 'system' ? (prefersDark ? 'dark' : 'light') : mode\n",
              "  return prefersDark ? 'dark' : 'light'\n")],
        [FT_NODE],
    ),
    # --- the frame painted in the shell's mode from its first frame, and the app's own state
    # following a live switch -----------------------------------------------------------------
    (
        "the hold corrects before it observes, so it reads the app's first removal as its own",
        [(FT, "    observer = env.observe(root, (count) => {",
              "    correct()\n    observer = env.observe(root, (count) => {"),
         (FT, "    })\n    correct()\n    timer = env.setTimeout(release, HOLD_MS)\n",
              "    })\n    timer = env.setTimeout(release, HOLD_MS)\n")],
        [FT_NODE],
    ),
    (
        "the hold never lets go of the class",
        [(FT, "      if (root.classList.contains('dark') === wantDark()) release()\n      else correct()\n",
              "      if (root.classList.contains('dark') !== wantDark()) correct()\n")],
        [FT_NODE],
    ),
    (
        "the document watch stops after its first document, so a navigation paints light again",
        [(FT, "      try { doc.defaultView?.addEventListener('pagehide', arm, { once: true }) } catch { /* gone */ }\n",
              "")],
        [FT_NODE],
    ),
    (
        "findAppTheme gives up after the first element React rendered, a portal included",
        [(FT, "      tried++\n", "      tried = maxElements\n")],
        [FT_NODE],
    ),
    (
        "a live switch no longer calls the app's own setter",
        [(FT, "    app?.setTheme(mode)\n", "    void app\n")],
        [FT_NODE],
    ),
    # --- the suite's own fixture (2026-10-16) ---------------------------------------------------
    # test_proxy's client hands the proxy an app client that cannot connect. Without it, the
    # proxy's requests look openmaic-app up, which the offline guard refuses: both tests still
    # PASS (502 and 200, as before) and ERROR at their teardown, so this case is also the proof
    # that the verdict reads pytest's ERROR lines, which the FAILED-only reader here never did.
    (
        "the proxy's tests reach for openmaic-app by name again",
        [("rails/openmaic/tests/test_proxy.py",
          '    monkeypatch.setattr(proxy, "get_client", lambda: app_down)\n', "")],
        ["test_root_assets_reach_the_proxy", "test_the_catch_all_does_not_shadow_the_rails_own_api"],
    ),
]


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # THIS tree first, and nothing else: the repo venv's editable installs point at one
    # checkout, and a worktree run without this would test somebody else's code.
    env["PYTHONPATH"] = str(RAIL_SRC)
    # No .pyc: a mutation and its restore can share an mtime and a size, and the import system
    # would then trust a stale compile of the other one.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    for var in ("PLATFORM_STANDALONE", "BROKER_AUTH_TOKEN", "BROKER_AUTH_TOKEN_FILE"):
        env.pop(var, None)
    return env


def witness() -> str | None:
    """Prove the modules under test resolve to THIS tree before any verdict means anything."""
    p = mr.run_tree(
        [PY, "-B", "-c",
         "import openmaic_app.api.proxy as p, openmaic_app.broker as b;"
         "print(p.__file__); print(b.__file__)"],
        cwd=REPO, capture_output=True, text=True, env=_env(), timeout=RUN_TIMEOUT)
    if p.returncode != 0:
        return f"import failed:\n{p.stdout}{p.stderr}"
    for f in p.stdout.split():
        if not Path(f).resolve().is_relative_to(RAIL_SRC):
            return f"imported {f}, which is not under {RAIL_SRC}"
    return None


def run_suite(extra: tuple[str, ...] = ()) -> tuple[int, str]:
    try:
        p = mr.run_tree([PY, "-B", "-m", "pytest", *SUITE, "-q", "--no-header",
                         "-p", "no:cacheprovider", *extra],
                        cwd=RAIL, capture_output=True, text=True, encoding="utf-8",
                        errors="replace", env=_env(), timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return -1, out + f"\nTIMEOUT after {RUN_TIMEOUT} s"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    mr.parse_harness_args(MUTATIONS)
    bad_import = witness()
    if bad_import:
        print(f"WRONG TREE -- {bad_import}")
        return 2
    # Every named test must EXIST before its absence from pytest's summary of failures and
    # errors can mean anything: a renamed test would otherwise turn every mutation naming it
    # into a silent SURVIVED.
    _, collected = run_suite(("--collect-only",))
    named = {t for _, _, tests in MUTATIONS for t in tests}
    missing = sorted(t for t in named if t not in collected)
    if missing:
        print("NAMED TESTS NOT COLLECTED -- " + ", ".join(missing))
        return 2
    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    # The witness that the node-backed tests RAN: skipped (no node on PATH), every mutation of a
    # frontend helper would read as SURVIVED, or worse, a guard would never be exercised at all.
    summary = out.strip().splitlines()[-1]
    if "skipped" in summary:
        print("BASELINE SKIPPED TESTS -- " + summary)
        return 2
    print(f"baseline green: {summary}\n")

    results: list[tuple[str, str]] = []
    for label, edits, expected in MUTATIONS:
        original_bytes: dict[Path, bytes] = {}
        try:
            pattern_ok = True
            for rel, old, new in edits:
                path = REPO / rel
                original_bytes.setdefault(path, path.read_bytes())
                current = path.read_text(encoding="utf-8")
                n = current.count(old)
                if n != 1:
                    print(f"  ERROR  {label}: pattern found {n} times in {rel}, expected 1")
                    pattern_ok = False
                    break
                mr.write_like(path, current.replace(old, new), original_bytes[path])
            if not pattern_ok:
                results.append((label, "PATTERN"))
                continue
            code, out = run_suite()
            missing = [t for t in expected if not mr.named_failed(out, t)]
            fired = code not in (0, -1)
            verdict = ("TIMEOUT" if code == -1 else
                       "FIRED" if fired and not missing else
                       "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                if missing:
                    print(f"      expected to fail but did not: {', '.join(missing)}")
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            for path, raw in original_bytes.items():
                mr.restore_exact(path, raw)

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
