"""Enrich teachtown's data.json with Mexican-Spanish text + EN/ES audio.

Reusable pieces (also used by the dashboard's TeachTown workflow):
  - ``translate_data(data, unit, progress)`` -> enrichment dict (text; shared cache)
  - ``add_audio(data, enr, audio_dir, unit, progress)``  -> fills audio paths
  - ``run(...)`` -> the CLI: reads interactive-html/data.json, writes enrichment.json

Usage (from apps/teachtown/):
    uv run python enrich.py                 # translate + audio, all units
    uv run python enrich.py --no-audio      # text only (no GPU/TTS needed)
    uv run python enrich.py --unit malala   # one unit only
    uv run python enrich.py --dry-run       # print translations, write nothing
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Callable

# Make edu_media_core importable without installing (mirrors the apps' bootstrap).
_ROOT = Path(__file__).resolve().parents[2]  # apps/teachtown/enrich.py -> edu-suite
sys.path.insert(0, str(_ROOT / "packages" / "edu-media-core" / "src"))

from edu_media_core import broker_media as core_translate, profiles  # noqa: E402 (translate via broker)

# --- app default paths (CLI) ---
HERE = Path(__file__).resolve().parent
HTML_DIR = HERE / "interactive-html"
DATA_JSON = HTML_DIR / "data.json"
ENRICH_JSON = HTML_DIR / "enrichment.json"
AUDIO_DIR = HTML_DIR / "public" / "audio"

_OPTIONS = {"temperature": 0.2, "num_ctx": 2048}
_Progress = Callable[[str], None]

VOCAB_PROMPT = """\
You translate ONE English vocabulary term into Mexican Spanish (es_MX) for a bilingual \
special-education worksheet. The learner is young and reads at a low level.

Return ONLY valid JSON: {"word_es": "<the Spanish term>", "def_es": "<short definition>"}

RULES:
- word_es = the actual Mexican-Spanish WORD or short term for this vocabulary word — what a
  Spanish dictionary gives (e.g. "context" -> "contexto", "cycle" -> "ciclo"). It is the TERM
  ITSELF: one word or a short noun phrase, NEVER a description or definition. A true cognate
  (contexto, ciclo, ecosistema) IS the right answer — use it; only avoid FALSE friends
  (e.g. "library" is "biblioteca", not "librería").
- def_es = a short, concrete, literal definition of the term (max ~10 words), informal tú,
  no idioms. The meaning goes HERE, not in word_es.
- Mexican Spanish only.

Example — term "context": {"word_es": "contexto", "def_es": "las palabras alrededor de una palabra"}
"""

TEXT_PROMPT = """\
You translate short English classroom text into Mexican Spanish (es_MX) for a \
special-education learner.

RULES:
- Mexican Spanish only, informal tú register.
- Keep it simple, concrete, and literal. Short sentences. No idioms.
- Preserve the meaning; do not add or remove information.

Return ONLY valid JSON: {"es": "<the Spanish text>"}
"""

VOCAB_PROFILE = profiles.register(profiles.Profile(
    key="teachtown_vocab", label="TeachTown vocab (young low-level reader)",
    system_prompt=VOCAB_PROMPT, options=_OPTIONS, required_keys=("word_es", "def_es")))
TEXT_PROFILE = profiles.register(profiles.Profile(
    key="teachtown_text", label="TeachTown classroom text (special-ed)",
    system_prompt=TEXT_PROMPT, options=_OPTIONS, required_keys=("es",)))


def _tr_vocab(en_word: str, en_def: str) -> dict:
    # Shared, content-addressed cache (the vocab profile's prompt keys it apart from text).
    return core_translate.translate_cached(
        system_prompt=VOCAB_PROFILE.system_prompt,
        user_message=f"Word: {en_word}\nMeaning: {en_def}",
        options=VOCAB_PROFILE.options, required_keys=VOCAB_PROFILE.required_keys,
    )


def _tr_text(en_text: str) -> str:
    return core_translate.translate_cached(
        system_prompt=TEXT_PROFILE.system_prompt, user_message=en_text,
        options=TEXT_PROFILE.options, required_keys=TEXT_PROFILE.required_keys,
    )["es"]


def translate_data(data: dict, unit: str | None,
                   progress: _Progress | None = None) -> dict:
    """Translate vocab, weekly summaries, and mission prompts to es_MX (text only).
    Uses the shared suite translation cache."""
    p = progress or (lambda m: None)
    enr = {"vocab": {}, "learn": {}, "missions": {}}
    for u, U in data["units"].items():
        if unit and u != unit:
            continue
        for week, info in U["weekInfo"].items():
            enr["learn"][f"{u}|{week}"] = {"es": _tr_text(info["learn"])}
            p(f"{u} learn wk{week}")
            for v in info["v"]:
                en_w, en_d = v[0], v[1]
                k = f"{u}|{en_w}"
                if k in enr["vocab"]:
                    continue
                tr = _tr_vocab(en_w, en_d)
                enr["vocab"][k] = {"word_es": tr["word_es"], "def_es": tr["def_es"]}
                p(f"{u} vocab {en_w}")
        for m in U["missions"]:
            title, prompt = m[2], m[3]
            k = f"{u}|{title}"
            if k in enr["missions"]:
                continue
            enr["missions"][k] = {"prompt_es": _tr_text(prompt)}
            p(f"{u} mission {title}")
    return enr


def add_audio(data: dict, enr: dict, audio_dir: Path, unit: str | None,
              progress: _Progress | None = None) -> None:
    """Synthesize EN/ES audio for vocab words, summaries, and prompts; fill paths."""
    from edu_media_core import broker_media  # audio via the platform broker
    p = progress or (lambda m: None)
    audio_dir.mkdir(parents=True, exist_ok=True)

    # Collect every clip first, then synthesize the whole set in one broker call
    # (XTTS loads once for the batch, not once per clip). au() returns the stable
    # hash-named path immediately and queues synthesis only for missing files.
    plan_items: list[dict] = []
    plan_paths: list[Path] = []

    def au(text: str, lang: str, u: str, kind: str, key: str) -> str:
        h = hashlib.sha1(f"{u}|{kind}|{key}|{lang}".encode("utf-8")).hexdigest()[:16]
        out = audio_dir / f"{h}.wav"
        if text and not out.exists():
            plan_items.append({"lang": lang, "text": text})
            plan_paths.append(out)
        return f"public/audio/{h}.wav"

    for u, U in data["units"].items():
        if unit and u != unit:
            continue
        for week, info in U["weekInfo"].items():
            lk = f"{u}|{week}"
            if lk in enr["learn"]:
                enr["learn"][lk]["audio_en"] = au(info["learn"], "en", u, "learn", week)
                enr["learn"][lk]["audio_es"] = au(enr["learn"][lk]["es"], "es", u, "learn", week)
                p(f"{u} audio learn wk{week}")
            for v in info["v"]:
                en_w, en_d = v[0], (v[1] if len(v) > 1 else "")
                vk = f"{u}|{en_w}"
                if vk in enr["vocab"] and "audio_en" not in enr["vocab"][vk]:
                    ev = enr["vocab"][vk]
                    # Read the word, then the definition; the period gives XTTS a
                    # natural pause between them (requested: word … then definition).
                    en_clip = f"{en_w}. {en_d}".strip().rstrip(".") + "."
                    es_clip = f"{ev['word_es']}. {ev.get('def_es', '')}".strip().rstrip(".") + "."
                    ev["audio_en"] = au(en_clip, "en", u, "vocab", en_w)
                    ev["audio_es"] = au(es_clip, "es", u, "vocab", en_w)
                    p(f"{u} audio vocab {en_w}")
        for m in U["missions"]:
            title, prompt = m[2], m[3]
            mk = f"{u}|{title}"
            if mk in enr["missions"] and "audio_en" not in enr["missions"][mk]:
                enr["missions"][mk]["audio_en"] = au(prompt, "en", u, "mission", title)
                enr["missions"][mk]["audio_es"] = au(enr["missions"][mk]["prompt_es"], "es", u, "mission", title)
                p(f"{u} audio mission {title}")

    if plan_items:
        broker_media.synthesize_wavs(plan_items, plan_paths)


def run(unit_filter: str | None, do_audio: bool, dry_run: bool, verbose: bool) -> None:
    data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    prog = (lambda m: print("  " + m)) if verbose else None
    enr = translate_data(data, unit_filter, prog)
    if do_audio and not dry_run:
        add_audio(data, enr, AUDIO_DIR, unit_filter, prog)
    if dry_run:
        print("\nDry run — nothing written.")
        print(json.dumps(enr, ensure_ascii=False, indent=2)[:2000])
        return
    ENRICH_JSON.write_text(json.dumps(enr, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {ENRICH_JSON.relative_to(_ROOT)} — "
          f"{len(enr['vocab'])} vocab, {len(enr['learn'])} summaries, {len(enr['missions'])} missions"
          + ("" if do_audio else " (text only; drop --no-audio for audio)"))


def main() -> None:
    ap = argparse.ArgumentParser(description="Enrich teachtown with es_MX text + audio")
    ap.add_argument("--unit", help="Only this unit (a key from data.json)")
    ap.add_argument("--no-audio", action="store_true", help="Text only (no GPU/TTS)")
    ap.add_argument("--dry-run", action="store_true", help="Print, write nothing")
    ap.add_argument("--quiet", action="store_true", help="Less output")
    args = ap.parse_args()
    run(unit_filter=args.unit, do_audio=not args.no_audio,
        dry_run=args.dry_run, verbose=not args.quiet)


if __name__ == "__main__":
    main()
