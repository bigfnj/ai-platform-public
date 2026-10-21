"""tools/platform-basepath.py: the patch that lets an upstream OpenMAIC checkout live under the
platform's basePath. It runs against a tree this repo does not hold, so every case here patches a
copy of fixtures/openmaic-v1.1.3/, which holds upstream v1.1.3's own touched files, verbatim
(fixtures/openmaic-v1.1.3/UPSTREAM.txt says where each came from).

What is pinned, and why each one matters:

* every edit lands exactly once, and PLATFORM_BASE_PATH goes INTO upstream's own `env` object
  (v1.1.x added one; a second `env` key would silently replace the first);
* the request shim and the key it reads are both written (without them the framed
  app's fetch('/api/...') calls left the prefix and 404ed at the gateway), and the shim reads
  exactly the key the config defines, because a rename on one side builds clean and leaves the
  shim doing nothing;
* defaultLocale is en-US unless asked otherwise, a re-run with another locale changes it in
  place, and only a locale upstream ships is ever written (upstream's zh-CN default put Chinese
  on screen for the first seconds of every load);
* a checkout patched by the FIRST version (basePath only) gets the new edits, rather than being
  reported "already patched" on the strength of the old marker;
* line endings are kept: the first version turned whole LF files into CRLF on Windows;
* refusals happen before anything is written, and --revert says when it cannot revert instead
  of printing "reverted" over an unchanged file;
* the build command the README documents is the one the script prints, Pi chat flag included,
  and it tags the image as compose finds it under either runtime (docker.io/library/...);
* the runtime stage creates /app/data owned by the runtime user before USER: upstream v1.1.3's
  own line is reported as done and nothing is written for it, a Dockerfile without it gets the
  line before USER (idempotent, --check, --revert byte-exact), a runtime stage without a
  single USER line is refused, and so is a marked line whose user that USER no longer names, or
  that no longer sits in the runtime stage before USER.
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from pathlib import Path

import pytest

RAIL = Path(__file__).resolve().parents[1]
TOOL = RAIL / "tools" / "platform-basepath.py"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "openmaic-v1.1.3"
UPSTREAM_FILES = ["next.config.ts", "Dockerfile", "lib/i18n/types.ts", "lib/i18n/locales.ts"]


def _load():
    spec = importlib.util.spec_from_file_location("platform_basepath", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


pb = _load()


def upstream(rel: str) -> str:
    """A fixture file as upstream has it: LF, whatever this repo's checkout turned it into."""
    return (FIXTURE / rel).read_bytes().decode("utf-8").replace("\r\n", "\n")


def checkout(tmp_path: Path, eol: str = "\n", edit=None) -> Path:
    """A stand-in checkout holding upstream's touched files. `edit(rel, text)` may change one."""
    for rel in UPSTREAM_FILES:
        text = upstream(rel)
        if edit:
            text = edit(rel, text)
        out = tmp_path / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(text.replace("\n", eol).encode("utf-8"))
    return tmp_path


def snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def read(root: Path, rel: str) -> str:
    return (root / rel).read_bytes().decode("utf-8").replace("\r\n", "\n")


def run(root: Path, *flags: str) -> int:
    return pb.main([*flags, str(root)])


def test_the_fixture_is_upstream_v1_1_3_unedited():
    # A fixture edited to make a test pass would test nothing about upstream. Each file's git
    # blob id is computed here and compared with the id UPSTREAM.txt records from upstream.
    listed = {rel: blob for blob, rel in
              re.findall(r"^([0-9a-f]{40})  (\S+)$", upstream("UPSTREAM.txt"), re.M)}
    assert sorted(listed) == sorted(UPSTREAM_FILES + ["LICENSE"])
    for rel, blob in listed.items():
        data = upstream(rel).encode("utf-8")
        got = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        assert got == blob, f"{rel} is not upstream v1.1.3's ({got} != {blob})"


def test_apply_writes_every_edit_and_the_shim(tmp_path):
    root = checkout(tmp_path)
    assert run(root) == 0
    cfg = read(root, "next.config.ts")
    for line in ("  basePath: process.env.NEXT_BASE_PATH || undefined,\n",
                 "  assetPrefix: process.env.NEXT_BASE_PATH || undefined,\n",
                 "    PLATFORM_BASE_PATH: process.env.NEXT_BASE_PATH || '',\n"):
        assert cfg.count(line) == 1, line
    # Still ONE env key, and ours is in it beside upstream's own.
    assert len(re.findall(r"^\s*env\s*:", cfg, re.M)) == 1
    start = cfg.index("  env: {\n")
    env = cfg[start:cfg.index("\n  },\n", start)]
    assert "PLATFORM_BASE_PATH:" in env and "NEXT_PUBLIC_PI_CHAT_ENABLED:" in env
    # The basePath block stays one contiguous edit, before the env object.
    assert cfg.index(pb.NEXT_CONFIG_PATCH) < start
    df = read(root, "Dockerfile")
    builder = df[df.index("FROM base AS builder\n"):df.index("FROM node:22-alpine AS runner")]
    assert df.count("ARG NEXT_BASE_PATH\n") == 1 and "ARG NEXT_BASE_PATH\n" in builder
    types = read(root, "lib/i18n/types.ts")
    assert types.count("export const defaultLocale") == 1
    assert "export const defaultLocale: Locale = 'en-US';\n" in types
    shim = (root / "instrumentation-client.ts").read_bytes()
    assert shim == pb.shim_text().encode()
    assert b"installApiPrefix" in shim and b"\r\n" not in shim


def test_the_shim_reads_the_key_the_config_defines():
    key = re.search(r"^\s+(\w+): process\.env\.NEXT_BASE_PATH \|\| '',$", pb.ENV_PATCH, re.M).group(1)
    shim = pb.shim_text()
    # The call that installs the shim, not a mention in a comment.
    assert re.search(rf"installApiPrefix\(window as unknown as PrefixTarget, process\.env\.{key}\)",
                     shim), key


def test_a_second_run_changes_nothing(tmp_path):
    root = checkout(tmp_path)
    run(root)
    before = snapshot(root)
    assert run(root) == 0
    assert snapshot(root) == before


@pytest.mark.parametrize("eol", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_line_endings_are_kept_and_revert_is_byte_exact(tmp_path, eol):
    root = checkout(tmp_path, eol)
    original = snapshot(root)
    assert run(root) == 0
    for name in ("next.config.ts", "Dockerfile", "lib/i18n/types.ts"):
        raw = (root / name).read_bytes()
        bare_lf = raw.count(b"\n") - raw.count(b"\r\n")
        if eol == "\n":
            assert b"\r\n" not in raw, name
        else:
            assert bare_lf == 0, f"{name}: {bare_lf} LF-only lines in a CRLF file"
    assert run(root, "--revert") == 0
    assert snapshot(root) == original
    assert not (root / "instrumentation-client.ts").exists()


def test_a_checkout_patched_by_the_first_version_gets_the_new_edits(tmp_path):
    # The first version's state: basePath and the Dockerfile arg, nothing else.
    def first(rel: str, text: str) -> str:
        if rel == "next.config.ts":
            return text.replace(pb.NEXT_CONFIG_ANCHOR, pb.NEXT_CONFIG_PATCH, 1)
        if rel == "Dockerfile":
            return text.replace(pb.DOCKER_ANCHOR, pb.DOCKER_PATCH, 1)
        return text
    root = checkout(tmp_path, "\r\n", edit=first)
    assert run(root) == 0
    raw = (root / "next.config.ts").read_bytes()
    assert b"PLATFORM_BASE_PATH" in raw
    # A CRLF checkout must get CRLF lines: the multi-line anchor matches only in the file's own
    # line ending, so this is also the check that it was found at all.
    assert raw.count(b"\n") == raw.count(b"\r\n"), "LF-only lines inserted into a CRLF file"
    assert b"'en-US'" in (root / "lib/i18n/types.ts").read_bytes()
    assert (root / "instrumentation-client.ts").is_file()


def test_an_older_shim_of_ours_is_replaced(tmp_path):
    root = checkout(tmp_path)
    run(root)
    shim = root / "instrumentation-client.ts"
    shim.write_text(f"// {pb.API_MARK}: an older version\n")
    assert run(root) == 0
    assert shim.read_bytes() == pb.shim_text().encode()


@pytest.mark.parametrize("name", ["instrumentation-client.ts", "instrumentation-client.js",
                                  "src/instrumentation-client.ts"])
def test_upstream_client_instrumentation_is_refused_and_nothing_is_written(tmp_path, name, capsys):
    root = checkout(tmp_path)
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).write_text("console.log('upstream')\n")
    before = snapshot(root)
    assert run(root) == 2
    assert snapshot(root) == before
    assert "upstream now ships its own client instrumentation" in capsys.readouterr().out


def test_a_second_env_key_is_refused_and_nothing_is_written(tmp_path, capsys):
    # A later `env` key in the same literal would silently replace the one ours went into.
    root = checkout(tmp_path, edit=lambda rel, t: t.replace(
        "  output: process.env.VERCEL", "  env: { FOO: 'bar' },\n  output: process.env.VERCEL", 1))
    before = snapshot(root)
    assert run(root) == 2
    assert snapshot(root) == before
    assert "more than one `env` key" in capsys.readouterr().out


def test_a_checkout_without_upstreams_env_object_is_refused(tmp_path, capsys):
    # v1.0.x had no env object. The key would have nowhere to go, so the run says so.
    def v10(rel: str, text: str) -> str:
        if rel != "next.config.ts":
            return text
        start = text.index("  env: {\n")
        return text[:start] + text[text.index("  },\n", start) + len("  },\n"):]
    root = checkout(tmp_path, edit=v10)
    before = snapshot(root)
    assert run(root) == 2
    assert snapshot(root) == before
    assert "ANCHOR MISSING in next.config.ts [platform-api-prefix]" in capsys.readouterr().out


def test_an_anchor_found_twice_is_refused(tmp_path, capsys):
    # Patching the first of two would land the edit in whichever stage happens to come first.
    root = checkout(tmp_path, edit=lambda rel, t: t + "\nFROM base AS builder\n" if rel == "Dockerfile" else t)
    before = snapshot(root)
    assert run(root) == 2
    assert snapshot(root) == before
    assert "ANCHOR FOUND 2 TIMES in Dockerfile [platform-basepath]" in capsys.readouterr().out


def test_revert_refuses_when_the_patched_text_was_edited(tmp_path, capsys):
    root = checkout(tmp_path)
    run(root)
    cfg = root / "next.config.ts"
    cfg.write_text(cfg.read_text().replace("process.env.NEXT_BASE_PATH || ''", "'/x'"))
    before = snapshot(root)
    assert run(root, "--revert") == 2
    assert snapshot(root) == before
    assert "CANNOT REVERT next.config.ts [platform-api-prefix]" in capsys.readouterr().out


def test_check_writes_nothing(tmp_path, capsys):
    root = checkout(tmp_path)
    before = snapshot(root)
    assert run(root, "--check") == 0
    assert snapshot(root) == before
    out = capsys.readouterr().out
    assert "WOULD patch    next.config.ts [platform-api-prefix]" in out
    assert "WOULD patch    lib/i18n/types.ts [platform-locale]" in out
    assert "WOULD write    instrumentation-client.ts" in out


def test_a_moved_anchor_is_refused(tmp_path, capsys):
    root = checkout(tmp_path, edit=lambda rel, t: t.replace("const nextConfig: NextConfig",
                                                            "const config: NextConfig"))
    before = snapshot(root)
    assert run(root) == 2
    assert snapshot(root) == before
    assert "ANCHOR MISSING in next.config.ts" in capsys.readouterr().out


# --- defaultLocale ---------------------------------------------------------------------------

def test_only_the_default_locale_line_changes(tmp_path):
    root = checkout(tmp_path)
    assert run(root) == 0
    before, after = upstream("lib/i18n/types.ts"), read(root, "lib/i18n/types.ts")
    assert after == before.replace(pb.LOCALE_ANCHOR, pb.locale_patch("en-US"), 1)
    assert after != before


def test_the_option_keeps_upstreams_locale(tmp_path):
    root = checkout(tmp_path)
    assert run(root, "--default-locale", "zh-CN") == 0
    assert "export const defaultLocale: Locale = 'zh-CN';\n" in read(root, "lib/i18n/types.ts")


def test_a_rerun_with_another_locale_updates_it_in_place(tmp_path, capsys):
    root = checkout(tmp_path)
    original = snapshot(root)
    run(root)
    assert run(root, "--default-locale", "ja-JP") == 0
    assert "update   lib/i18n/types.ts [platform-locale] (en-US -> ja-JP)" in capsys.readouterr().out
    types = read(root, "lib/i18n/types.ts")
    assert types.count(pb.LOCALE_MARK) == 1
    assert "export const defaultLocale: Locale = 'ja-JP';\n" in types
    # And --revert finds the block whatever locale it holds.
    assert run(root, "--revert") == 0
    assert snapshot(root) == original


@pytest.mark.parametrize("locale", ["xx-YY", "en", "en-US'; globalThis.x = 1; '"])
def test_a_locale_upstream_does_not_ship_is_refused_and_nothing_is_written(tmp_path, locale, capsys):
    root = checkout(tmp_path)
    before = snapshot(root)
    assert run(root, "--default-locale", locale) == 2
    assert snapshot(root) == before
    assert "REFUSED  --default-locale" in capsys.readouterr().out


def test_a_hand_edited_locale_block_is_refused(tmp_path, capsys):
    root = checkout(tmp_path)
    run(root)
    types = root / "lib/i18n/types.ts"
    types.write_bytes(types.read_bytes().replace(b"= 'en-US';", b"= \"en-US\";"))
    before = snapshot(root)
    assert run(root) == 2
    assert run(root, "--revert") == 2
    assert snapshot(root) == before
    out = capsys.readouterr().out
    assert "REFUSED  lib/i18n/types.ts [platform-locale]" in out
    assert "CANNOT REVERT lib/i18n/types.ts [platform-locale]" in out


# --- the image build ---------------------------------------------------------------------------

def test_the_readme_builds_what_the_patcher_prints(capsys, tmp_path):
    # Pi chat (default ON from upstream v1.1.0) needs tool calling, which this rail's shim does
    # not forward, so the build keeps the legacy classroom chat. A rebuild from the README must
    # carry the same flag as one from the script's own output, and both the rail's prefix.
    from openmaic_app.config import Settings
    prefix = Settings.model_fields["public_prefix"].default
    assert f"--build-arg NEXT_BASE_PATH={prefix} " in pb.BUILD_COMMAND
    assert "--build-arg NEXT_PUBLIC_PI_CHAT_ENABLED=false " in pb.BUILD_COMMAND
    readme = (RAIL / "README.md").read_text(encoding="utf-8")
    assert pb.BUILD_COMMAND in readme
    run(checkout(tmp_path))
    assert pb.BUILD_COMMAND in capsys.readouterr().out


def test_every_tag_the_readme_and_the_patcher_give_is_the_one_compose_finds():
    # Compose's `openmaic-app:latest` is docker.io/library/openmaic-app:latest to Docker and to
    # podman's Docker-compatible API, while `podman build -t openmaic-app:x` stores
    # localhost/openmaic-app:x, which compose's lookup misses. So every build or tag command the
    # rail documents spells the name in full (Docker shows that as plain openmaic-app:x).
    assert pb.BUILD_COMMAND.startswith("docker build -t docker.io/library/openmaic-app:latest ")
    readme = (RAIL / "README.md").read_text(encoding="utf-8")
    tags = re.findall(r"(?:-t|\btag)\s+`?(\S*openmaic-app:[^\s`]+)", readme)
    tags += re.findall(r"(?:-t|\btag)\s+`?\S*openmaic-app:[^\s`]+`?\s+`?(\S*openmaic-app:[^\s`]+)",
                       readme)
    assert len(tags) >= 4, tags            # the build, the candidate, both halves of the rollback
    assert all(t.startswith("docker.io/library/openmaic-app:") for t in tags), tags


# --- /app/data ---------------------------------------------------------------------------------

UPSTREAM_DATA_DIR = "RUN mkdir -p /app/data && chown -R nextjs:nodejs /app/data\n"


def without_data_dir(rel: str, text: str) -> str:
    """Upstream's Dockerfile as it was before THU-MAIC/OpenMAIC#1438: nothing in the runtime
    stage creates /app/data, so the image has none and a fresh volume is root's."""
    if rel != "Dockerfile":
        return text
    assert text.count(UPSTREAM_DATA_DIR) == 1
    start = text.index("# The app persists classrooms")
    return text[:start] + text[text.index(UPSTREAM_DATA_DIR) + len(UPSTREAM_DATA_DIR):]


def test_upstreams_own_data_dir_line_counts_as_done_and_nothing_is_written_for_it(tmp_path, capsys):
    root = checkout(tmp_path)
    assert run(root, "--check") == 0
    assert ("  ok       Dockerfile [platform-data-dir] (upstream's own, before USER nextjs: "
            "RUN mkdir -p /app/data && chown -R nextjs:nodejs /app/data)") in capsys.readouterr().out
    assert run(root) == 0
    # Only the builder's build arg: no second /app/data line beside upstream's.
    assert read(root, "Dockerfile") == upstream("Dockerfile").replace(pb.DOCKER_ANCHOR,
                                                                      pb.DOCKER_PATCH, 1)


@pytest.mark.parametrize("eol", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_a_dockerfile_without_it_gets_the_line_before_user(tmp_path, eol, capsys):
    root = checkout(tmp_path, eol, edit=without_data_dir)
    original = snapshot(root)
    assert run(root, "--check") == 0
    assert "  WOULD patch    Dockerfile [platform-data-dir]" in capsys.readouterr().out
    assert snapshot(root) == original
    assert run(root) == 0
    df = read(root, "Dockerfile")
    runner = df[df.index("FROM node:22-alpine AS runner"):]
    block = pb.data_dir_block("nextjs")
    assert df.count(pb.DATA_MARK) == 1 and block in runner
    # Straight before the USER line, owned by the user that line names.
    assert runner.index(block) + len(block) == runner.index("USER nextjs\n")
    assert "RUN mkdir -p /app/data && chown nextjs /app/data\n" in block
    raw = (root / "Dockerfile").read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n") if eol == "\r\n" else b"\r\n" not in raw
    patched = snapshot(root)
    assert run(root) == 0
    assert snapshot(root) == patched, "a second run changed the tree"
    assert run(root, "--revert") == 0
    assert snapshot(root) == original


@pytest.mark.parametrize("shape,what", [("no USER", "MISSING"), ("two USER lines", "FOUND 2 TIMES")])
def test_a_runtime_stage_without_one_user_line_is_refused(tmp_path, capsys, shape, what):
    # Upstream's own /app/data line is left in: with no single runtime user to own it, it says
    # nothing about who can write there.
    def edit(rel: str, text: str) -> str:
        if rel != "Dockerfile":
            return text
        return text.replace("USER nextjs\n", "" if shape == "no USER" else "USER nextjs\nUSER root\n")
    root = checkout(tmp_path, edit=edit)
    before = snapshot(root)
    assert run(root) == 2
    assert snapshot(root) == before
    assert f"ANCHOR {what} in Dockerfile [platform-data-dir]" in capsys.readouterr().out


def test_a_patched_line_whose_user_the_runtime_stage_no_longer_runs_as_is_refused(tmp_path, capsys):
    # Patched while the runtime user was nextjs; then upstream renamed it. "Already patched" would
    # leave /app/data owned by a user the app no longer runs as, so a fresh volume is unwritable.
    root = checkout(tmp_path, edit=without_data_dir)
    assert run(root) == 0
    df = root / "Dockerfile"
    assert df.read_bytes().count(b"\nUSER nextjs\n") == 1
    df.write_bytes(df.read_bytes().replace(b"\nUSER nextjs\n", b"\nUSER node\n"))
    before = snapshot(root)
    assert run(root) == 2
    assert run(root, "--check") == 2
    assert snapshot(root) == before
    out = capsys.readouterr().out
    assert ("REFUSED  Dockerfile [platform-data-dir]: the patched line chowns /app/data to "
            "'nextjs', but the runtime stage's USER is now 'node'") in out
    assert "already patched" not in out
    # The way out the refusal names: --revert takes the stale line away, a run writes the current one.
    assert run(root, "--revert") == 0
    assert run(root) == 0
    text = read(root, "Dockerfile")
    runner = text[text.index("FROM node:22-alpine AS runner"):]
    block = pb.data_dir_block("node")
    assert runner.count(pb.DATA_MARK) == 1
    assert runner.index(block) + len(block) == runner.index("USER node\n")


FINAL_STAGE = "\n# ---- Stage 5: Final ----\nFROM runner AS final\n\nUSER nextjs\n"


@pytest.mark.parametrize("shape", ["a stage after it", "USER moved above it"])
def test_a_patched_line_outside_the_runtime_stage_before_user_is_refused(tmp_path, capsys, shape):
    # Patched before USER nextjs in the runner stage; then upstream reorganised its stages. The
    # user still matches, so only WHERE the line sits shows it no longer does its job: in a stage
    # that is no longer the runtime one, or after USER, where the chown runs unprivileged.
    root = checkout(tmp_path, edit=without_data_dir)
    assert run(root) == 0
    df = root / "Dockerfile"
    block = pb.data_dir_block("nextjs")
    text = read(root, "Dockerfile")
    assert text.count(block + "USER nextjs\n") == 1
    if shape == "a stage after it":
        text += FINAL_STAGE
    else:
        text = text.replace(block + "USER nextjs\n", "USER nextjs\n\n" + block)
    df.write_bytes(text.encode("utf-8"))
    before = snapshot(root)
    assert run(root) == 2
    assert run(root, "--check") == 2
    assert snapshot(root) == before
    out = capsys.readouterr().out
    assert ("REFUSED  Dockerfile [platform-data-dir]: the patched line is not in the runtime "
            "stage before its USER nextjs line") in out
    assert "already patched" not in out and "\nnothing written" in out
    # The way out the refusal names: --revert takes the line away, a run writes it where it belongs.
    assert run(root, "--revert") == 0
    assert run(root) == 0
    text = read(root, "Dockerfile")
    runtime = text[[m.start() for m in re.finditer(r"^FROM\s", text, re.M)][-1]:]
    assert text.count(pb.DATA_MARK) == 1
    assert runtime.index(block) + len(block) == runtime.index("USER nextjs\n")


def test_a_hand_edited_data_dir_block_is_refused(tmp_path, capsys):
    root = checkout(tmp_path, edit=without_data_dir)
    run(root)
    df = root / "Dockerfile"
    df.write_bytes(df.read_bytes().replace(b"RUN mkdir -p /app/data && chown nextjs",
                                           b"RUN mkdir /app/data && chown nextjs"))
    before = snapshot(root)
    assert run(root) == 2
    assert run(root, "--revert") == 2
    assert snapshot(root) == before
    out = capsys.readouterr().out
    assert "REFUSED  Dockerfile [platform-data-dir]" in out
    assert "CANNOT REVERT Dockerfile [platform-data-dir]" in out
