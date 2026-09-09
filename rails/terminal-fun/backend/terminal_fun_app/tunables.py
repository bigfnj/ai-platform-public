"""Per-toy tunable parameters + a validated launch builder.

SECURITY MODEL: the AI never emits CLI flags. It only chooses VALUES for the named
parameters below; `build_launch` validates each value against its schema (enum
whitelist / int clamp / printable-string cap) and maps it to flags/env itself. So a
chat prompt can never inject an arbitrary argument into a subprocess.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    kind: str  # "bool" | "int" | "enum" | "multienum" | "string"
    help: str = ""
    choices: tuple[str, ...] = ()
    min: int | None = None
    max: int | None = None
    default: object = None


@dataclass(frozen=True)
class Tunable:
    base: list[str]
    params: list[Param]
    render: Callable[[dict], tuple[list[str], dict[str, str]]]  # validated values -> (extra_argv, env)
    intro: str = ""  # a short human summary of what's changeable


def _validate(p: Param, value: object) -> object:
    if p.kind == "bool":
        if isinstance(value, bool):
            return value
        if value is None:
            return bool(p.default)
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if p.kind == "enum":
        return value if value in p.choices else p.default
    if p.kind == "multienum":
        if not isinstance(value, list):
            value = [x.strip() for x in str(value or "").split(",") if x.strip()]
        return [x for x in value if x in p.choices]
    if p.kind == "int":
        try:
            iv = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return p.default
        if p.min is not None:
            iv = max(p.min, iv)
        if p.max is not None:
            iv = min(p.max, iv)
        return iv
    if p.kind == "string":
        s = "".join(ch for ch in str(value or "") if ch.isprintable())[:60]
        return s or (p.default or "")
    return p.default


# --- render functions (receive already-validated values) --------------------

def _cmatrix(v: dict) -> tuple[list[str], dict[str, str]]:
    a: list[str] = []
    if v["async"]:
        a.append("-a")
    a.append({"off": "-n", "on": "-b", "all": "-B"}[v["bold"]])
    if v["rainbow"]:
        a.append("-r")
    if v["lambda"]:
        a.append("-m")
    a += ["-u", str(v["speed"])]
    if v["color"] != "green":
        a += ["-C", v["color"]]
    return a, {}


def _cbonsai(v: dict) -> tuple[list[str], dict[str, str]]:
    a = ["-M", str(v["density"]), "-L", str(v["size"]),
         "-b", {"none": "0", "pot": "1", "large-pot": "2"}[v["base"]],
         "-w", str(v["wait"]),
         "-t", {"slow": "0.06", "normal": "0.03", "fast": "0.01"}[v["speed"]]]
    if v["message"]:
        a += ["-m", v["message"]]
    return a, {}


def _pipes(v: dict) -> tuple[list[str], dict[str, str]]:
    return ["-p", str(v["pipes"]), "-f", str(v["fps"])], {}


def _genact(v: dict) -> tuple[list[str], dict[str, str]]:
    a = ["-s", {"slow": "0.5", "normal": "1", "fast": "2"}[v["speed"]]]
    for m in v["modules"]:
        a += ["-m", m]
    return a, {}


def _sl(v: dict) -> tuple[list[str], dict[str, str]]:
    a: list[str] = []
    fl = {"classic": None, "little": "-l", "fly": "-F", "c51": "-c"}[v["variant"]]
    if fl:
        a.append(fl)
    if v["accident"]:
        a.append("-a")
    return a, {}


def _cowsay(v: dict) -> tuple[list[str], dict[str, str]]:
    env = {"COW_RAINBOW": "1" if v["rainbow"] else "0"}
    if v["cow"] != "default":
        env["COW_FILE"] = v["cow"]
    mood = {"normal": "", "dead": "-d", "greedy": "-g", "paranoid": "-p",
            "stoned": "-s", "tired": "-t", "wired": "-w", "youthful": "-y"}[v["mood"]]
    if mood:
        env["COW_MOOD"] = mood
    return [], env


def _starwars(v: dict) -> tuple[list[str], dict[str, str]]:
    return ["--speed", {"half": "0.5", "normal": "1", "fast": "2", "ludicrous": "4"}[v["speed"]]], {}


_GENACT_MODULES = (
    "ansible", "bootlog", "botnet", "bruteforce", "cargo", "cc", "composer", "cryptomining",
    "docker_build", "download", "kernel_compile", "memdump", "simcity", "terraform", "weblog",
)

TUNABLES: dict[str, Tunable] = {
    "cmatrix": Tunable(
        base=["cmatrix"],
        params=[
            Param("color", "Color", "enum", "rain color",
                  choices=("green", "red", "blue", "white", "yellow", "cyan", "magenta"), default="green"),
            Param("bold", "Bold", "enum", "character weight", choices=("off", "on", "all"), default="on"),
            Param("rainbow", "Rainbow", "bool", "cycle colors", default=False),
            Param("lambda", "Lambda mode", "bool", "rain lambdas (λ)", default=False),
            Param("async", "Async scroll", "bool", "columns scroll independently", default=True),
            Param("speed", "Speed", "int", "update delay 0 (fastest) – 10 (slowest)", min=0, max=10, default=4),
        ],
        render=_cmatrix,
        intro="color, bold, rainbow, lambda-mode, async scroll, and speed (0–10)",
    ),
    "cbonsai": Tunable(
        base=["cbonsai", "-l", "-i"],
        params=[
            Param("density", "Branch density", "int", "higher = bushier (0–20)", min=0, max=20, default=5),
            Param("size", "Size", "int", "life; higher = bigger tree (0–200)", min=0, max=200, default=32),
            Param("base", "Pot", "enum", "the base drawing", choices=("none", "pot", "large-pot"), default="pot"),
            Param("speed", "Growth speed", "enum", choices=("slow", "normal", "fast"), default="normal"),
            Param("wait", "Wait between trees", "int", "seconds (1–15)", min=1, max=15, default=4),
            Param("message", "Message", "string", "text shown next to the tree", default=""),
        ],
        render=_cbonsai,
        intro="branch density (0–20), size (0–200), pot style, growth speed, wait-between-trees, and a message",
    ),
    "pipes": Tunable(
        base=["pipes.sh"],
        params=[
            Param("pipes", "Pipes", "int", "how many pipes (1–30)", min=1, max=30, default=2),
            Param("fps", "Speed (fps)", "int", "frames per second (20–100)", min=20, max=100, default=75),
        ],
        render=_pipes,
        intro="number of pipes (1–30) and speed in fps (20–100)",
    ),
    "genact": Tunable(
        base=["genact"],
        params=[
            Param("speed", "Speed", "enum", choices=("slow", "normal", "fast"), default="normal"),
            Param("modules", "Modules", "multienum",
                  "which fake tasks to show (empty = all)", choices=_GENACT_MODULES, default=[]),
        ],
        render=_genact,
        intro="speed and which modules run (e.g. cargo, docker_build, bruteforce, cryptomining, weblog…)",
    ),
    "sl": Tunable(
        base=["sl", "-e"],
        params=[
            Param("variant", "Train", "enum", choices=("classic", "little", "fly", "c51"), default="classic"),
            Param("accident", "Accident", "bool", "people call for help from the windows", default=False),
        ],
        render=_sl,
        intro="which train (classic / little / flying / C51) and whether it's an accident",
    ),
    "cowsay": Tunable(
        base=["cowfortune"],
        params=[
            Param("cow", "Character", "enum", "who says it",
                  choices=("default", "tux", "dragon", "moose", "sheep", "stegosaurus", "vader",
                           "koala", "turtle", "skeleton"), default="default"),
            Param("mood", "Mood", "enum",
                  choices=("normal", "dead", "greedy", "paranoid", "stoned", "tired", "wired", "youthful"),
                  default="normal"),
            Param("rainbow", "Rainbow", "bool", "colorize with lolcat", default=True),
        ],
        render=_cowsay,
        intro="the character (tux, dragon, vader…), its mood, and rainbow on/off",
    ),
    "starwars": Tunable(
        base=["ascii-movie", "play"],
        params=[
            Param("speed", "Playback speed", "enum",
                  choices=("half", "normal", "fast", "ludicrous"), default="normal"),
        ],
        render=_starwars,
        intro="playback speed (half / normal / fast / ludicrous)",
    ),
}


def is_tunable(item_id: str) -> bool:
    return item_id in TUNABLES


def defaults(item_id: str) -> dict:
    spec = TUNABLES.get(item_id)
    return {p.key: p.default for p in spec.params} if spec else {}


def schema(item_id: str) -> dict | None:
    spec = TUNABLES.get(item_id)
    if not spec:
        return None
    return {
        "intro": spec.intro,
        "params": [
            {"key": p.key, "label": p.label, "kind": p.kind, "help": p.help,
             "choices": list(p.choices), "min": p.min, "max": p.max, "default": p.default}
            for p in spec.params
        ],
    }


def validate_params(item_id: str, values: dict | None) -> dict:
    """Clamp/whitelist a (partial) value dict into a full, safe param set."""
    spec = TUNABLES.get(item_id)
    if not spec:
        return {}
    values = values or {}
    return {p.key: _validate(p, values.get(p.key, p.default)) for p in spec.params}


def build_launch(item_id: str, values: dict | None) -> tuple[list[str], dict[str, str]] | None:
    """Return (argv, env) for a tunable item from validated values, or None if not tunable."""
    spec = TUNABLES.get(item_id)
    if not spec:
        return None
    clean = validate_params(item_id, values)
    extra_argv, env = spec.render(clean)
    return list(spec.base) + extra_argv, env
