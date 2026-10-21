"""Mutation harness for the style checks: RC012's scope rule and the shipped stylesheets it
reads beside theme.css, RC032's read of the built bundle, the --rail/--rule name check,
RC005's per-module token inventory, rail_conformance's read() cache and quiet ast.parse, RC030,
RC031, RC016's per-rail built/unbuilt/rotted/broken judgement, RC033's read of the gateway's own
compose block, what the checker's one namespace asks of the rule files it runs, and the text
scan's `??` / `?.` reading, per-rail allow file, build-output pruning and shorthand `{ color }` in
a style object. NOT a pytest file.

    python tools/tests/mutate_style_checks.py

Each mutation breaks one property; the named test must then FAIL (pytest -rA), and only after
the baseline reported every named test PASSED, which is the witness that the suite ran this
tree's code at all. On the shared runner (tools/tests/mutation_runner.py): bounded runs that
kill the process tree, mutation in the file's own line endings, a PROVEN byte-for-byte restore.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / "tools" / "tests" / "mutation_runner.py").is_file())
                       / "tools" / "tests"))
import mutation_runner as mr  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
RC = "tools/rail_conformance.py"
# The rule files the checker runs (its "where the rules live"), each named for the ids it holds.
RC_001_004 = "tools/rail_conformance_rules/rc001_rc004.py"
RC_005_008 = "tools/rail_conformance_rules/rc005_rc008.py"
RC_012 = "tools/rail_conformance_rules/rc012.py"
RC_013_020 = "tools/rail_conformance_rules/rc013_rc020.py"
RC_021_024 = "tools/rail_conformance_rules/rc021_rc024.py"
RC_028_035 = "tools/rail_conformance_rules/rc028_rc035.py"
FILES = "TestRuleFiles::"
SCAN = "tools/tests/test_status_text_scan.py"
T_RC = "tools/tests/test_rail_conformance.py"
SCOPE = "TestRc012Scope::"
FILTER = "TestFilterNamesMustExist::"
TOKEN = "TestBrokerTokenNames::"
CLS = "test_the_classifier_on_each_form["
RC016 = "TestRC016BuiltVsUnbuilt::"
RC033 = "TestRc033GatewayBlock::"

MUTATIONS = [
    # (label, file, old, new, test file, expected failing test: its whole name, as
    # mutation_runner.named_failed reads it; a parametrized one with its whole id)
    ("RC012 never runs the scope check", RC_012,
     "    leaks = unscoped_selectors(m, _all)\n", "    leaks = []\n",
     T_RC, SCOPE + "test_rc012_fails_with_a_count_and_the_selectors"),
    ("any dashed class counts as the rail's own prefix", RC_012,
     '        if any("-" in c and owners.get(_class_prefix(c)) == {m.id} for c in classes):\n',
     '        if any("-" in c for c in classes):\n',
     T_RC, SCOPE + "test_a_prefix_shared_with_another_rail_is_not_this_rails_own"),
    ("@media bodies are not read", RC,
     '            inert = any(s.startswith(("@keyframes", ',
     '            inert = any(s.startswith(("@media", "@keyframes", ',
     T_RC, SCOPE + "test_unscoped_forms_fail_by_name"
                   "[@media (max-width: 9px) { .chip { x: 1; } }-.chip-also used by web-core]"),
    ("a :root / html qualifier excuses any selector", RC,
     '    return bool(wrapper and re.match(r"(?::root|html)\\b", first)\n'
     '                and re.search(rf"\\.{re.escape(wrapper)}(?![\\w-])", sel))\n',
     '    return bool(re.match(r"(?::root|html)\\b", first))\n',
     T_RC, SCOPE + "test_unscoped_forms_fail_by_name[:root { --zq-a: 1; }-:root-no class]"),
    ("a module.tsx is read whole, code and comments as CSS", RC_012,
     '    if not where.endswith((".tsx", ".ts", ".jsx", ".js")):\n        return text\n',
     '    if True:\n        return text\n',
     T_RC, SCOPE + "test_a_module_tsx_is_read_for_its_injected_css_only"),
    ("a --rail name that matches nothing is accepted", RC,
     "    if bad_rails:\n        ap.error(", "    if False:\n        ap.error(",
     T_RC, FILTER + "test_a_name_that_matches_nothing_exits_2[argv0-no rail named no-such-rail]"),
    ("a --rule name that matches nothing is accepted", RC,
     "    if bad_rules:\n        ap.error(", "    if False:\n        ap.error(",
     T_RC, FILTER + "test_a_name_that_matches_nothing_exits_2[argv1-no rule named RC999]"),
    ("RC005 inventories the rail, not each module", RC_005_008,
     '        if names == {"BROKER_AUTH_TOKEN"}:\n', '        if False:\n',
     T_RC, TOKEN + "test_rc005_fails_a_reader_left_behind_in_a_rail_that_moved"),
    ("RC005 demands the file form of a rail that never moved", RC_005_008,
     "        if not file_aware:\n            return []\n", "",
     T_RC, TOKEN + "test_rc005_passes_a_rail_whose_readers_all_read_the_value"),
    ("read() serves a rewritten file from its cache", RC,
     "    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:\n", "    if hit:\n",
     T_RC, "TestRelAndRead::test_read_is_cached_but_never_stale"),
    ("ast.parse prints a rail's invalid escapes again", RC,
     '        warnings.simplefilter("ignore", SyntaxWarning)\n'
     '        warnings.simplefilter("ignore", DeprecationWarning)\n',
     '        pass\n',
     T_RC, "TestRelAndRead::test_parsing_a_rail_with_an_invalid_escape_prints_nothing"),
    ("`??` is read as a ternary again (its left side dropped)", SCAN,
     '            if nxt == "?":\n                i += 2\n                continue\n', "",
     SCAN, CLS + "const T = { a: { on: \"var(--critical)\" } };\\n"
                 "<i style={{ color: x.on ?? \"var(--good-text)\" }} />-fixed --critical]"),
    ("`?.` is read as a ternary again", SCAN,
     '            if nxt == "." and not expr[i + 2:i + 3].isdigit():\n                i += 1\n                continue\n', "",
     SCAN, CLS + "const T = { a: { on: \"var(--critical)\" } };\\n"
                 "<i style={{ color: x?.on }} />-fixed --critical]"),
    ("a rail allow entry with a blank why is accepted", SCAN,
     '            if not all(isinstance(e[k], str) and e[k].strip() for k in ("file", "line", "why")):\n',
     '            if False:\n',
     SCAN, "test_a_rail_allow_file_that_says_less_than_why_is_refused[allow0-must all be non-empty]"),
    ("a rail allow entry may point outside its frontend", SCAN,
     "            if not target.is_relative_to(f.parent.resolve()):\n", "            if False:\n",
     SCAN, "test_a_rail_allow_file_that_says_less_than_why_is_refused[allow2-outside this frontend]"),
    ("a rail allow entry naming a missing file is not stale", SCAN,
     "        if present or file_must_exist:\n", "        if present:\n",
     SCAN, "test_a_rail_allow_file_suppresses_its_line_and_its_stale_entries_fail"),
    # RC012 reads every stylesheet the build ships, not only theme.css (D8: bouquet's dev page
    # sheet came in through main.tsx), and RC032 reads the built bundle itself.
    ("RC012 reads theme.css alone again", RC_012,
     "    for sheet_text, sheet_where, via in shipped_stylesheets(m):\n",
     "    for sheet_text, sheet_where, via in []:\n",
     T_RC, "TestRc012ShippedSheets::test_a_sheet_main_imports_is_judged_and_fails"),
    ("a commented-out import is read as an import", RC_012,
     "            for spec in _CSS_IMPORT.findall(_strip_js_comments(read(mod))):\n",
     "            for spec in _CSS_IMPORT.findall(read(mod)):\n",
     T_RC, "TestRc012ShippedSheets::test_a_sheet_no_module_imports_is_not_shipped"),
    ("index.html is not read", RC_012,
     '    if html.is_file():\n', '    if False:\n',
     T_RC, "TestRc012ShippedSheets::test_index_html_links_and_inline_styles_count"),
    ("a dynamic import is not an import", RC_012,
     r'''_CSS_IMPORT = re.compile(r"""(?:\bimport\s*\(?|\bfrom)''',
     r'''_CSS_IMPORT = re.compile(r"""(?:\bimport\s*|\bfrom)''',
     T_RC, "TestRc012ShippedSheets::test_every_import_form_counts"),
    ("RC012 judges theme.css twice", RC_012,
     "            if path in seen or not path.is_file() or path == own:\n",
     "            if path in seen or not path.is_file():\n",
     T_RC, "TestRc012ShippedSheets::test_theme_css_and_package_css_are_not_judged_here"),
    ("a selector list is split inside its parentheses again", RC,
     "_selector_list(head) if p.strip()]", 'head.split(",") if p.strip()]',
     T_RC, SCOPE + "test_a_selector_list_is_split_at_its_top_level_commas_only"),
    ("RC032 finds no global rule in the bundle", RC_028_035,
     "        hits = global_selectors(read(css), wrapper)\n", "        hits = []\n",
     T_RC, "TestRc032BuiltBundle::test_rc032_fails_a_bundle_with_global_rules"),
    ("RC032 calls a classless selector scoped", RC,
     '        if not re.findall(r"\\.(-?[A-Za-z_][\\w-]*)", re.sub(r":not\\([^)]*\\)", "", first)):\n'
     "            out.append(sel)\n",
     '        if not first:\n            out.append(sel)\n',
     T_RC, "TestRc032BuiltBundle::test_each_global_form_fails_by_name[button{x:1}-button]"),
    ("RC032's wrapper qualifier excuses nothing", RC,
     "        if _root_led_to_wrapper(first, sel, wrapper):\n            continue\n        if not re.findall(",
     "        if False:\n            continue\n        if not re.findall(",
     T_RC, "TestRc032BuiltBundle::test_scoped_and_class_led_rules_pass"
           "[:root[data-theme=dark] .zq{x:1}html[data-x] .zq .b{x:1}]"),
    ("RC032 passes an unbuilt rail as clean", RC_028_035,
     '    if not (assets / "remoteEntry.js").is_file():\n        return [S("RC032"',
     '    if not (assets / "remoteEntry.js").is_file():\n        return []\n        return [S("RC032"',
     T_RC, "TestRc032BuiltBundle::test_an_unbuilt_rail_is_a_skip_under_the_unbuilt_tree_allowance"),
    # RC030: a route hidden behind an earlier, more general one
    ("RC030 never finds a hidden route", RC_028_035,
     "                if owner2 == owner and meth2 == meth and _route_matches(segs, path2.strip(\"/\").split(\"/\")):\n",
     "                if False:\n",
     T_RC, "TestRc030ShadowedRoutes::test_the_job_aid_shape_fails_naming_both_routes"),
    ("RC030 calls a route hidden by itself", RC_028_035,
     "    return widened and len(general) == len(specific)\n", "    return len(general) == len(specific)\n",
     T_RC, "TestRc030ShadowedRoutes::test_which_routes_hide_which[/api/jobs/{job_id}-/api/jobs/{job_id}-False]"),
    ("RC030 lets {x:int} take a name", RC_028_035,
     '        if conv == "int" and not other.isdigit():\n', '        if False:\n',
     T_RC, "TestRc030ShadowedRoutes::test_which_routes_hide_which[/api/jobs/{job_id:int}-/api/jobs/export.xlsx-False]"),
    # RC031: models read outside every slot
    ("RC031 calls an @role default a pinned model", RC_028_035,
     '            if not value or value.startswith("@") or name.value in slot_envs:\n',
     '            if not value or name.value in slot_envs:\n',
     T_RC, "TestRc031UnslottedModels::test_what_is_not_an_unslotted_model_passes[at-role]"),
    ("RC031 counts a slot's own env as unslotted", RC_028_035,
     '            if not value or value.startswith("@") or name.value in slot_envs:\n',
     '            if not value or value.startswith("@"):\n',
     T_RC, "TestRc031UnslottedModels::test_what_is_not_an_unslotted_model_passes[slot-env]"),
    ("RC031 accepts an exemption with no reason", RC_028_035,
     "        if not env or not why:\n", "        if not env:\n",
     T_RC, "TestRc031UnslottedModels::test_a_declared_exemption_passes_and_a_reasonless_or_stale_one_fails"),
    ("RC031 never reports a stale exemption", RC_028_035,
     "    for env in sorted(set(declared) - {e for e, _v, _w in reads}):\n", "    for env in []:\n",
     T_RC, "TestRc031UnslottedModels::test_a_declared_exemption_passes_and_a_reasonless_or_stale_one_fails"),
    # RC016: each rail's dist default judged on its own shape, never against its siblings
    ("RC016 is comparative again: an unbuilt rail fails once a sibling is built", RC_013_020,
     '            unbuilt.append(f"{ident}_dist")\n',
     '            unbuilt.append(f"{ident}_dist")\n'
     '            if any(Path(str(v)).is_dir() for v in defaults.values() if v):\n'
     '                out.append(F("RC016", m, "missing while a sibling is built", rel(cfg_path)))\n',
     T_RC, RC016 + "test_a_PARTIAL_build_skips_the_unbuilt_rail_instead_of_failing_it"),
    ("RC016 accepts a default outside the rail's own directory", RC_013_020,
     "        if not inside or not dist.parent.is_dir():\n", "        if not dist.parent.is_dir():\n",
     T_RC, RC016 + "test_a_ROTTED_default_FAILS_whatever_else_is_built[tree-unbuilt-outside-the-rail]"),
    ("RC016 calls a default whose parent is gone merely unbuilt", RC_013_020,
     "        if not inside or not dist.parent.is_dir():\n", "        if not inside:\n",
     T_RC, RC016 + "test_a_ROTTED_default_FAILS_whatever_else_is_built[tree-unbuilt-parent-gone]"),
    ("RC016 passes a dist directory with no remoteEntry.js", RC_013_020,
     '        elif not (dist / "assets" / "remoteEntry.js").is_file():\n', "        elif False:\n",
     T_RC, RC016 + "test_a_BROKEN_build_FAILS"),
    ("an unbuilt tile hides a rotted tile on the same rail", RC_013_020,
     "    if unbuilt and not out:\n", "    if unbuilt:\n",
     T_RC, RC016 + "test_two_unbuilt_tiles_on_one_rail_are_ONE_skip_and_a_rotted_one_is_not_hidden"),
    ("RC016 skips once per tile, not once per rail", RC_013_020,
     '        return [S("RC016", m, f"{\', \'.join(unbuilt)} names this rail\'s own build output, which "\n'
     '                              f"has not been built in this checkout: unbuilt, not rotted",\n'
     '                  allowance="unbuilt-tree")]\n',
     '        return [S("RC016", m, f"{u} names this rail\'s own build output, which "\n'
     '                              f"has not been built in this checkout: unbuilt, not rotted",\n'
     '                  allowance="unbuilt-tree") for u in unbuilt]\n',
     T_RC, RC016 + "test_two_unbuilt_tiles_on_one_rail_are_ONE_skip_and_a_rotted_one_is_not_hidden"),
    # the text scan never reads a build output dir as source
    ("the scan's walk prunes `dist` alone again", SCAN,
     "        dirnames[:] = sorted(d for d in dirnames if not not_source(d))\n",
     '        dirnames[:] = sorted(d for d in dirnames if d not in ("node_modules", "dist", ".venv", ".git", "__pycache__"))\n',
     SCAN, "test_a_build_output_dir_is_never_read_as_source[dist-iep]"),
    ("a file is skipped by the old '/dist/' substring again", SCAN,
     "                    and not any(not_source(s) for s in p.relative_to(r).parts[:-1]):\n",
     '                    and not any(s in p.as_posix() for s in ("node_modules", "/dist/")):\n',
     SCAN, "test_a_build_output_dir_is_never_read_as_source[dist-iep]"),
    ("dist-* is not a build output", SCAN,
     '    return name in ("dist", "build", "coverage", ".vite") or name.startswith("dist-")\n',
     '    return name in ("dist", "build", "coverage", ".vite")\n',
     SCAN, "test_a_build_output_dir_is_never_read_as_source[dist-iep]"),
    # the text scan resolves a custom property only through what the use's rail can see
    ("custom properties resolve through every rail's definitions again", SCAN,
     "    for s in (scope, *SHARED_SCOPES):\n", "    for s in by_scope:\n",
     SCAN, "test_a_custom_property_resolves_only_through_what_its_rail_can_see"),
    ("a rail no longer sees its own definitions", SCAN,
     "    for s in (scope, *SHARED_SCOPES):\n", "    for s in SHARED_SCOPES:\n",
     SCAN, "test_a_custom_property_resolves_only_through_what_its_rail_can_see"),
    ("a rail no longer sees web-core's or the shell's definitions", SCAN,
     "    for s in (scope, *SHARED_SCOPES):\n", "    for s in (scope,):\n",
     SCAN, "test_a_custom_property_resolves_only_through_what_its_rail_can_see"),
    ("verdicts_for judges a use with no scope again", SCAN,
     "            verdicts = [css_verdict(v, scope, by_scope)]\n",
     "            verdicts = [css_verdict(v, None, by_scope)]\n",
     SCAN, "test_the_scan_judges_each_use_in_its_own_rails_scope"),
    # a shorthand `color` is a use in a style object, and only there
    ("a shorthand `{ color }` in a style object is not read", SCAN,
     '        out += [(line_of(k), "js", "color") for k in shorthand_colors(text)]\n', "",
     SCAN, CLS + 'const color = "var(--warning)";\\n<i style={{ color }} />-fixed --warning]'),
    ("any object holding a shorthand `color` is a style (patterns too)", SCAN,
     '    """Does the code around the object literal opening at `brace` mark it as a style?"""\n',
     '    """Does the code around the object literal opening at `brace` mark it as a style?"""\n'
     '    return True\n',
     SCAN, CLS + "const { color } = props;-None]"),
    ("a destructuring pattern under a `style:` key is a style object", SCAN,
     "        return not _in_pattern(code, brace)  # style: { ... }\n", "        return True\n",
     SCAN, CLS + "const { style: { color } } = props;-None]"),
    ("a parameter list is not a pattern", SCAN,
     "        return bool(_PARAMS_END.match(code, _balanced(code, up)))\n", "        return False\n",
     SCAN, CLS + "function paint(el, { style: { color } }) { el.title = color; }-None]"),
    ("a call's argument list is read as a parameter list", SCAN,
     "        return bool(_PARAMS_END.match(code, _balanced(code, up)))\n", "        return True\n",
     SCAN, CLS + 'const color = "var(--good)";\\n'
                 'React.createElement("i", { style: { color } });-fixed --good]'),
    ("an object typed CSSProperties is not a style", SCAN,
     "    if _CSS_TYPED.search(code, lo, brace):\n", "    if False:\n",
     SCAN, CLS + 'const color = "var(--warning)";\\n'
                 'const s: React.CSSProperties = { color };-fixed --warning]'),
    ("an object cast to CSSProperties is not a style", SCAN,
     "    if _CSS_CAST.match(code, _balanced(code, brace)):\n", "    if False:\n",
     SCAN, CLS + 'const color = "var(--warning)";\\n'
                 'const s = { color, padding: 4 } as CSSProperties;-fixed --warning]'),
    ("a template substitution's `${` is left in the code (the brackets stop balancing)", SCAN,
     '                chars[s] = chars[s + 1] = " "\n', "                pass\n",
     SCAN, CLS + 'const color = "var(--critical)";\\n'
                 '<i style={{ width: `${w}px`, color }} />-fixed --critical]'),
    # RC033: the gateway's own compose block restates every enabled rail
    ("RC033 never reports a missing URL", RC_028_035,
     "    if _compose_env_value(block, url_key) is None:\n", "    if False:\n",
     T_RC, RC033 + "test_each_missing_restatement_fails_by_name[url]"),
    ("RC033 reads the URL as a substring again (a commented-out line passes)", RC_028_035,
     "    if _compose_env_value(block, url_key) is None:\n", "    if url_key not in block:\n",
     T_RC, RC033 + "test_a_commented_out_or_blank_restatement_fails[url-commented]"),
    ("RC033 never checks the dist mount", RC_028_035,
     "    elif not _compose_ro_mount(block, want):\n", "    elif False:\n",
     T_RC, RC033 + "test_each_missing_restatement_fails_by_name[mount]"),
    ("a commented-out mount delivers the dist path again", RC_028_035,
     r'''rf"^[ \t]+-[ \t]+(['\"]?)[^\s#'\"]+:{re.escape(target)}:ro\1"''',
     r'''rf"(['\"]?):{re.escape(target)}:ro\1"''',
     T_RC, RC033 + "test_a_commented_out_or_blank_restatement_fails[mount-commented]"),
    ("a quoted dist value keeps its quotes", RC_028_035,
     r'''    if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"":''' "\n",
     "    if False:\n",
     T_RC, RC033 + "test_what_the_block_may_legitimately_vary_passes[quoted-and-commented]"),
    ("RC033's depends_on is a substring search again", RC_028_035,
     r'''    if not re.search(rf"^[ \t]+-[ \t]+{re.escape(m.compose_service())}[ \t]*(?:#[^\n]*)?$",'''
     "\n                     block, re.M):\n",
     "    if m.compose_service() not in block:\n",
     T_RC, RC033 + "test_each_missing_restatement_fails_by_name[depends_on]"),
    ("RC033 checks a rail the gateway does not serve", RC_028_035,
     "        elif m.id not in enabled:\n", "        elif False:\n",
     T_RC, RC033 + "test_a_rail_the_gateway_does_not_serve_is_a_counted_skip"),
    ("RC033's disabled-rail exemption is a bare [] again, invisible to the inert check", RC_028_035,
     '            return [S("RC033", m, "not in the gateway\'s enabled_apps',
     '            return []\n            return [S("RC033", m, "not in the gateway\'s enabled_apps',
     T_RC, RC033 + "test_a_rail_the_gateway_does_not_serve_is_a_counted_skip"),
    ("an enabled_apps RC033 cannot read exempts the rail unseen", RC_028_035,
     "        if enabled is None:\n            out.append(",
     "        if enabled is None:\n            return []\n            out.append(",
     T_RC, RC033 + "test_an_enabled_apps_it_cannot_read_warns_and_still_checks[not-a-literal]"),
    ("the enabled_apps reader accepts a non-string id", RC_028_035,
     "        if isinstance(value, (tuple, list)) and all(isinstance(v, str) for v in value):\n",
     "        if isinstance(value, (tuple, list)):\n",
     T_RC, RC033 + "test_the_enabled_apps_reader[non-string]"),
    ("RC033 with no gateway service passes in silence", RC_028_035,
     "        return [F(\"RC033\", m, \"no compose service named 'gateway'",
     "        return []\n        return [F(\"RC033\", m, \"no compose service named 'gateway'",
     T_RC, RC033 + "test_no_gateway_service_warns_that_nothing_was_inspected"),
    # the rule files: tools/rail_conformance.py runs each into its own namespace ("where the rules
    # live"), and TestRuleFiles holds what one namespace asks of them
    ("a rule file never runs, so its rules check nothing", RC,
     '"rc012", "rc013_rc020", "rc021_rc024",\n', '"rc012", "rc013_rc020",\n',
     T_RC, FILES + "test_every_rule_file_runs_once_in_the_order_of_its_name"),
    ("a rule file runs twice, so its rules register twice", RC,
     '           "rc025_rc027", "rc028_rc035")\n',
     '           "rc025_rc027", "rc028_rc035", "rc028_rc035")\n',
     T_RC, FILES + "test_every_rule_file_runs_once_in_the_order_of_its_name"),
    ("a rule file binds a helper of the checker's, which replaces it for every file", RC_021_024,
     "\n\ndef _depends_name(node: ast.expr) -> str:\n",
     '\n\ndef _callee(node: ast.expr) -> str:\n    return node.id if isinstance(node, ast.Name) else ""\n'
     "\n\ndef _depends_name(node: ast.expr) -> str:\n",
     T_RC, FILES + "test_no_two_files_bind_one_name"),
    ("a rule file calls a helper only another rule file defines", RC_005_008,
     '                       and _callee(n.value) == "modelstate" for n in ast.walk(fn)):\n',
     '                       and _depends_name(n.value) == "modelstate" for n in ast.walk(fn)):\n',
     T_RC, FILES + "test_no_file_uses_a_name_only_another_rule_file_binds"),
    ("a rule file's docstring becomes the checker's --help text", RC_001_004,
     "# RC001-RC004: the manifest itself, the ports it claims, and the gateway's catalog and routing.\n",
     '"""RC001-RC004."""\n'
     "# RC001-RC004: the manifest itself, the ports it claims, and the gateway's catalog and routing.\n",
     T_RC, FILES + "test_the_checkers_docstring_is_its_own"),
]


def run(test: str):
    r = mr.run_bounded([PY, "-m", "pytest", "-rA", "-q", "-p", "no:cacheprovider", test],
                       cwd=REPO, timeout=300)
    passed = {m.group(1) for m in re.finditer(r"^PASSED \S+?\.py::(.+?)\s*$", r.out, re.M)}
    return ("TIMEOUT" if r.timed_out else r.code), r.out, passed, mr.failed_ids(r.out)


def named(expect: str, ids: set[str]) -> bool:
    """The baseline's witness that `expect` ran and passed, read as named_failed reads a failure:
    the whole name, and a bare one is any parametrization of itself."""
    return any(i == expect or i.split("[", 1)[0] == expect for i in ids)


def main() -> int:
    mr.parse_harness_args(MUTATIONS)
    for test in sorted({m[4] for m in MUTATIONS}):
        code, out, passed, failed = run(test)
        missing = [m[5] for m in MUTATIONS if m[4] == test and not named(m[5], passed)]
        if code != 0 or failed or missing:
            print(f"BASELINE NOT GREEN OR WITNESS MISSING in {test}: exit {code}, "
                  f"failed {sorted(failed)[:3]}, never passed {missing}")
            print(out[-1500:])
            return 2
        print(f"baseline {test}: green, {len(passed)} passed")
    fired = 0
    for label, rel, old, new, test, expect in MUTATIONS:
        path = REPO / rel
        original = path.read_bytes()
        n, mutated = mr.mutate_bytes(original, old, new)
        if n != 1:
            print(f"  PATTERN  {label}: found {n} times, expected 1")
            continue
        try:
            path.write_bytes(mutated)
            code, out, _passed, _failed = run(test)
        finally:
            mr.restore_exact(path, original)
        hit = mr.named_failed(out, expect)
        verdict = "TIMEOUT" if code == "TIMEOUT" else ("FIRED" if code != 0 and hit else "SURVIVED")
        fired += verdict == "FIRED"
        print(f"  {verdict:9}{label}\n           expected red: {expect[:90]} -> {'red' if hit else 'NOT red'}")
        if verdict != "FIRED":
            print(out[-1200:])
    ok = all(run(t)[0] == 0 for t in sorted({m[4] for m in MUTATIONS}))
    print(f"\nrestored tree: {'green' if ok else 'STILL RED'}")
    print(f"{fired}/{len(MUTATIONS)} mutations fired the right test")
    return 0 if fired == len(MUTATIONS) and ok else 1


if __name__ == "__main__":
    sys.exit(main())
