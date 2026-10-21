"""`requester` on the wire: who a broker call is FOR.

The broker serves a call from the platform owner's own Claude subscription instead of the
local GPU only when this field names an owner. Everything about that decision lives in the
broker; the only job here is to put the value on the request, and to leave it off when there
isn't one.

Both halves matter and neither is visible at runtime. Dropped, the CLI backend is inert in
the one direction that looks like "it just never gets chosen". Sent as `null` rather than
omitted, a broker predating the field rejects a payload that used to work -- so the omission
is asserted as strictly as the inclusion.
"""
from __future__ import annotations

import ast
import base64
import importlib
import inspect
import io
import logging
import random
import sys
from pathlib import Path

import pytest

from edu_media_core import broker_media

OWNER = "owner-one"


def _load_real_pillow() -> dict:
    """The real Pillow's modules, with every image plugin registered, whatever a sibling test
    left in sys.modules.

    test_core.py and test_resource_safety.py put an empty stand-in for PIL into sys.modules
    when nothing has imported Pillow yet, and they collect first in a whole-directory run, so a
    plain `from PIL import ...` here would get their stub (no ImageDraw, no open). With a stub
    present, every PIL module is set aside, the real one is imported and initialised (so its
    PNG and JPEG plugins register with THIS PIL.Image, not an older one: a fresh PIL.Image
    next to plugins left by an earlier import saves no PNG at all), and sys.modules is then put
    back exactly as it was. The fixture below lends the real set to each test here, which
    fit_image_for_cli's own call-time import then finds. tools/tests/test_rail_smoke.py's
    _use_real_pil does the same for the same reason. With no stub, Pillow is used as loaded."""
    def pil_names():
        return [n for n in sys.modules if n == "PIL" or n.startswith("PIL.")]

    def stubbed():
        return any(getattr(sys.modules[n], "__file__", None) is None for n in pil_names())

    if not stubbed():
        importlib.import_module("PIL.ImageDraw")
        sys.modules["PIL.Image"].init()
        return {n: sys.modules[n] for n in pil_names()}
    saved = {n: sys.modules[n] for n in pil_names()}
    for n in saved:
        del sys.modules[n]
    try:
        importlib.import_module("PIL.Image")
        importlib.import_module("PIL.ImageDraw")
        sys.modules["PIL.Image"].init()
        real = {n: sys.modules[n] for n in pil_names()}
    finally:
        for n in pil_names():
            del sys.modules[n]
        sys.modules.update(saved)
    return real


_PILLOW = _load_real_pillow()
Image = _PILLOW["PIL.Image"]
ImageDraw = _PILLOW["PIL.ImageDraw"]


@pytest.fixture(autouse=True)
def _real_pillow(monkeypatch):
    for name, mod in _PILLOW.items():
        monkeypatch.setitem(sys.modules, name, mod)


def _payload(monkeypatch, **kw) -> dict:
    seen: dict = {}

    def fake_post(path, payload, **_kw):
        seen["path"] = path
        seen["payload"] = payload
        seen["kw"] = _kw
        return {"message": {"content": "{}"}}

    monkeypatch.setattr(broker_media, "_post", fake_post)
    broker_media.chat_json("sys", "user", **kw)
    return seen["payload"]


def _b64(img: Image.Image, fmt: str = "PNG") -> str:
    buf = io.BytesIO()
    img.save(buf, fmt)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _page(w: int, h: int, mode: str = "RGB") -> str:
    """A synthetic worksheet-shaped page: white paper, a title bar and numbered answer rows.
    Flat colour, so it compresses like the renderer's real output (a Letter page at 1600 px
    is ~170,000 base64 characters). In RGBA the paper is transparent BLACK, which is how a
    real transparent PNG stores it, so a fit that drops the alpha shows black paper."""
    img = Image.new(mode, (w, h), (0, 0, 0, 0) if mode == "RGBA" else (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle((40, 40, w - 40, 120), fill=(30, 60, 140, 255) if mode == "RGBA" else (30, 60, 140))
    for y in range(180, h - 80, 90):
        d.rectangle((60, y, w - 60, y + 40), outline=(0, 0, 0, 255) if mode == "RGBA" else (0, 0, 0),
                    width=3)
    return _b64(img)


def _scan(w: int = 1300, h: int = 1300) -> str:
    """A page of random colour noise: the shape of a 300 dpi colour scan that PNG cannot
    compress, and over the broker's 5,000,000-character cap at this size."""
    rnd = random.Random(7)
    return _b64(Image.frombytes("RGB", (w, h), rnd.randbytes(w * h * 3)))


def _size(b64: str) -> tuple[tuple[int, int], str]:
    with Image.open(io.BytesIO(base64.b64decode(b64))) as im:
        return im.size, im.format


def test_requester_is_sent_when_known(monkeypatch):
    assert _payload(monkeypatch, requester="owner-one")["requester"] == "owner-one"


def test_requester_is_omitted_when_not_given(monkeypatch):
    """Byte-identical to the old payload, so an older broker is unaffected."""
    assert "requester" not in _payload(monkeypatch)


def test_requester_is_omitted_when_empty(monkeypatch):
    """A blank owner is not a user. Sending "" would be a name the broker has to reject
    rather than an absence it can read as background work."""
    assert "requester" not in _payload(monkeypatch, requester="")


def test_requester_is_omitted_when_none(monkeypatch):
    assert "requester" not in _payload(monkeypatch, requester=None)


def test_the_rest_of_the_payload_is_unchanged(monkeypatch):
    p = _payload(monkeypatch, requester="owner-one")
    assert p["format"] == "json"
    assert p["keep_alive"] == "30m"
    assert p["messages"][0] == {"role": "system", "content": "sys"}
    assert p["messages"][1]["content"] == "user"


def test_an_image_call_carries_its_callers_requester(monkeypatch):
    """An image call names exactly who its caller named, so the broker may send the page of an
    owner's own job to Claude. Before, the name was dropped here, so every page call stayed local
    under a chip that said Claude."""
    page = _page(1600, 2071)
    p = _payload(monkeypatch, requester=OWNER, images=[page])
    assert p["requester"] == OWNER
    assert p["messages"][1]["images"] == [page], "the page itself must be sent, as rendered"


def test_a_nameless_image_call_names_nobody_and_sends_the_page_as_rendered(monkeypatch):
    """Background or nameless work stays nameless (so local), images or not, and the local
    model reads the page byte for byte as rendered: even one the CLI path would refuse is not
    fitted, because no Claude answer can come of it."""
    scan = _scan()
    assert len(scan) > broker_media._CLI_IMAGE_MAX_B64, "the fixture must be one that fitting changes"
    p = _payload(monkeypatch, images=[scan])
    assert "requester" not in p
    assert p["messages"][1]["images"][0] is scan


def test_an_empty_image_list_is_a_text_call(monkeypatch):
    """`images=[]` (a render that produced nothing) carries no image, and the name as usual."""
    assert _payload(monkeypatch, requester="owner-one", images=[])["requester"] == "owner-one"


# --- fitting an owner's page to the CLI path -------------------------------------------------

def test_a_page_inside_every_limit_goes_byte_identical(monkeypatch):
    """A Letter page as the renderer draws it (1600x2071) is inside the broker's cap, the
    API's 8000 px and Claude's own resolution limit, so nothing touches it."""
    page = _page(1600, 2071)
    p = _payload(monkeypatch, requester=OWNER, images=[page])
    assert p["messages"][1]["images"][0] is page


def test_a_page_claude_downsizes_itself_goes_as_rendered(monkeypatch):
    """A Legal page (1600x2636: over the 2576 px long edge and 4784 visual tokens) is NOT
    refused anywhere: Claude resizes it to claude_image_size itself. It goes as rendered, so a
    local fallback that reads more pixels (the @iep model reads up to 16.8 MP) keeps them."""
    page = _page(1600, 2636)
    assert broker_media.claude_image_size(1600, 2636) != (1600, 2636), "fixture must exceed"
    p = _payload(monkeypatch, requester=OWNER, images=[page])
    assert p["messages"][1]["images"][0] is page


def test_a_page_over_the_broker_cap_is_fitted_under_it(monkeypatch):
    """A colour scan the renderer turns into 6.8 million base64 characters is refused by the
    broker's 5,000,000 cap and answered locally. Fitted, it reaches Claude: same aspect, never
    larger, under the cap, in a format the broker's sniff accepts. Recompressed BEFORE any
    shrinking: 1300x1300 is inside Claude's own size, so light JPEG gets it under the cap
    with every pixel kept (measured: 6,771,352 -> 2,036,460 characters)."""
    scan = _scan()
    assert len(scan) > broker_media._CLI_IMAGE_MAX_B64
    p = _payload(monkeypatch, requester=OWNER, images=[scan])
    out = p["messages"][1]["images"][0]
    assert len(out) <= broker_media._CLI_IMAGE_MAX_B64
    assert _size(out) == ((1300, 1300), "JPEG")
    # Quality 90, the light compression the guide asks for; a heavier one passes every other
    # assertion here and blurs the text Claude reads. The literal, not the constant, so the
    # two cannot move together.
    ref = io.BytesIO()
    Image.new("RGB", (8, 8)).save(ref, "JPEG", quality=90)
    with Image.open(io.BytesIO(base64.b64decode(out))) as im, \
            Image.open(io.BytesIO(ref.getvalue())) as want:
        assert im.quantization == want.quantization


def test_the_fit_cap_is_the_brokers_own():
    """The cap pages are fitted to is a copy of the broker's (services/broker/app/
    cli_backend.py, _IMAGE_MAX_B64). Lowered there alone, every page fitted between the two
    would be refused into a local answer on every call while the chip says Claude."""
    src = Path(__file__).resolve().parents[3] / "services" / "broker" / "app" / "cli_backend.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    found = [n.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == "_IMAGE_MAX_B64" for t in n.targets)]
    assert len(found) == 1, f"_IMAGE_MAX_B64 assigned {len(found)} times in {src}"
    assert ast.literal_eval(found[0]) == broker_media._CLI_IMAGE_MAX_B64


def test_a_page_over_the_api_side_limit_is_resized_to_claudes_own_size(monkeypatch):
    """A 400x9000 strip is small in bytes but over the API's 8000 px, which the broker does
    not check: the CLI's call would fail into a fallback. It is resized to exactly the size
    Claude would use, kept PNG (it fits), and a transparent background comes out white."""
    strip = _page(400, 9000, mode="RGBA")
    assert len(strip) <= broker_media._CLI_IMAGE_MAX_B64, "fixture must be over the side limit only"
    p = _payload(monkeypatch, requester=OWNER, images=[strip])
    out = p["messages"][1]["images"][0]
    size, fmt = _size(out)
    assert size == broker_media.claude_image_size(400, 9000)
    assert max(size) <= broker_media._CLAUDE_MAX_EDGE and fmt == "PNG"
    with Image.open(io.BytesIO(base64.b64decode(out))) as im:
        assert im.mode == "RGB" and im.getpixel((2, size[1] - 2)) == (255, 255, 255)


def test_claude_image_size_matches_the_guides_examples():
    """The Vision guide's own figures (build-with-claude/vision, "Resolution and token cost",
    and vision-coordinates' reference implementation), standard tier and high-resolution."""
    std = {"max_edge": 1568, "max_tokens": 1568}
    assert broker_media.claude_image_size(1075, 1520, **std) == (924, 1307)
    assert broker_media.claude_image_size(1920, 1080, **std) == (1456, 819)
    assert broker_media.claude_image_size(1920, 1080) == (1920, 1080)
    assert broker_media.claude_image_size(2000, 1500) == (2000, 1500)
    assert broker_media.claude_image_size(3840, 2160) == (2576, 1449)
    assert broker_media.claude_image_tokens(1000, 1000) == 1296
    assert broker_media.claude_image_tokens(2576, 1449) == 4784


def test_an_unreadable_image_is_left_for_the_broker_to_refuse(monkeypatch):
    """Not base64, or base64 of something that is not a picture: sent as it came, so the
    broker's sniff refuses it into a local answer exactly as before."""
    junk = ["not base64 at all!", base64.b64encode(b"plain text").decode("ascii")]
    p = _payload(monkeypatch, requester=OWNER, images=list(junk))
    assert p["messages"][1]["images"] == junk


def test_without_pillow_the_page_goes_as_rendered_and_says_so(monkeypatch, caplog):
    """Degraded, never silent: a warning on every call that needed the fitting."""
    monkeypatch.setitem(sys.modules, "PIL", None)
    scan = "x" * (broker_media._CLI_IMAGE_MAX_B64 + 4)
    with caplog.at_level(logging.WARNING, logger=broker_media.log.name):
        p = _payload(monkeypatch, requester=OWNER, images=[scan])
    assert p["messages"][1]["images"][0] is scan
    assert "Pillow is not installed" in caplog.text


def test_a_page_nothing_can_fit_goes_as_rendered_and_says_so(monkeypatch, caplog):
    """Unreachable at the real cap (random noise at Claude's largest size is 4,470,852
    characters as JPEG q90), so reached here by lowering it: the page goes as rendered, with a
    warning, and the broker refuses it into a local answer. Never an over-cap re-encode."""
    monkeypatch.setattr(broker_media, "_CLI_IMAGE_MAX_B64", 1000)
    page = _page(1600, 2071)
    with caplog.at_level(logging.WARNING, logger=broker_media.log.name):
        p = _payload(monkeypatch, requester=OWNER, images=[page])
    assert p["messages"][1]["images"][0] is page
    assert "does not fit the Claude CLI path" in caplog.text


def test_the_page_is_fitted_once_and_resent_identically_on_the_json_retry(monkeypatch):
    posted: list[dict] = []
    fitted: list[str] = []
    real = broker_media.fit_image_for_cli

    def counting(b64):
        fitted.append(b64)
        return real(b64)

    def fake_post(path, payload, **_kw):
        posted.append(payload)
        return {"message": {"content": "{not json" if len(posted) == 1 else "{}"}}

    monkeypatch.setattr(broker_media, "fit_image_for_cli", counting)
    monkeypatch.setattr(broker_media, "_post", fake_post)
    broker_media.chat_json("sys", "user", images=[_scan()], requester=OWNER)
    assert len(posted) == 2 and len(fitted) == 1
    assert posted[0]["messages"][1]["images"] == posted[1]["messages"][1]["images"]


def test_an_owners_image_call_waits_the_full_chat_ceiling(monkeypatch):
    """A call Claude may answer can wait BROKER_CLI_TIMEOUT and then one local vision answer,
    inside the same broker request. So it rides _post's defaults: the chat ceiling
    (BROKER_TIMEOUT, 1320 s in compose for both edu-suite instances, which
    tools/rail_smoke.py's CLI_RAIL_TIMEOUTS holds) and no client retry, never a media
    timeout or a retry that would queue a second copy behind the first."""
    seen: dict = {}
    params = inspect.signature(broker_media._post).parameters

    def fake_post(path, payload, **kw):
        seen.update(kw)
        return {"message": {"content": "{}"}}

    monkeypatch.setattr(broker_media, "_post", fake_post)
    broker_media.chat_json("sys", "user", images=[_page(1600, 2071)], requester=OWNER)
    assert seen == {}
    assert params["timeout"].default == broker_media._TIMEOUT
    assert params["retries"].default == 0


def _translate(monkeypatch, tmp_path, **kw) -> list[dict]:
    """Run translate_cached against a throwaway cache; return every payload posted."""
    posted: list[dict] = []

    def fake_post(path, payload, **_kw):
        posted.append(payload)
        return {"message": {"content": '{"es": "hola"}'}}

    monkeypatch.setattr(broker_media, "_post", fake_post)
    out = broker_media.translate_cached(cache_path=tmp_path / "t.json", system_prompt="sys",
                                        user_message="hello", required_keys=("es",), **kw)
    assert out == {"es": "hola"}
    return posted


def test_translate_cached_passes_the_requester_on_a_miss(monkeypatch, tmp_path):
    posted = _translate(monkeypatch, tmp_path, requester="owner-one")
    assert [p.get("requester") for p in posted] == ["owner-one"]


def test_translate_cached_sends_none_by_default(monkeypatch, tmp_path):
    posted = _translate(monkeypatch, tmp_path)
    assert len(posted) == 1 and "requester" not in posted[0]


def test_a_cache_hit_sends_nothing_at_all(monkeypatch, tmp_path):
    """The requester is not in the cache key, which is only safe because a hit makes no
    call: nobody's material leaves the box on a hit, whoever produced the cached answer."""
    _translate(monkeypatch, tmp_path, requester="owner-one")
    assert _translate(monkeypatch, tmp_path, requester="owner-two") == []
