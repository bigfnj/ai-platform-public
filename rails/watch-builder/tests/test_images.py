"""Uploaded images: bounded on the way in, resampled per platform on the way out.

The behaviour these tests pin exists because `graphics_draw_bitmap_in_rect` does NOT scale.
Measured on the real SDK: a rect smaller than the bitmap clips to the top-left, and a larger
one TILES the source to fill it. So an image must be shipped at the exact pixel size it will
be drawn, which differs per platform when the box is a percentage.

Get that wrong and dragging a corner smaller crops the picture while dragging it larger
repeats it, both of which a user would reasonably read as an editor bug.
"""
from __future__ import annotations

import asyncio
import io
import json

import pytest
from fastapi import HTTPException
from PIL import Image, ImageDraw

from watch_builder import config, images
from watch_builder.codegen import generate_package_json, generate_c, image_resource_name
from watch_builder.design import PLATFORMS, Design


@pytest.fixture
def store_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(images.settings, "data_dir", str(tmp_path))
    return tmp_path


def png_bytes(w: int, h: int, *, mode: str = "RGB", fmt: str = "PNG") -> bytes:
    im = Image.new(mode, (w, h), (255, 0, 0) if mode == "RGB" else (255, 0, 0, 255))
    buf = io.BytesIO()
    im.save(buf, format=fmt)
    return buf.getvalue()


# --- upload path ----------------------------------------------------------------------


def test_upload_is_downscaled_immediately(store_dir):
    """A phone photo against a 256 KB resource budget.

    Downscaled at UPLOAD, not at build: finding out at build time produces a waf error about
    resource pack size that never mentions the photo.
    """
    rec = images.store("alice", png_bytes(2000, 1500), name="huge.png")
    assert max(rec.width, rec.height) == images.MAX_STORED
    assert rec.width == 320 and rec.height == 240  # aspect preserved


def test_a_small_image_is_not_upscaled(store_dir):
    rec = images.store("alice", png_bytes(40, 30), name="small.png")
    assert (rec.width, rec.height) == (40, 30)


def test_id_is_the_content_hash_so_the_same_bytes_dedupe(store_dir):
    a = images.store("alice", png_bytes(60, 60), name="one.png")
    b = images.store("alice", png_bytes(60, 60), name="two.png")
    assert a.id == b.id
    # Which is also what makes /api/images/<id>/raw safe to cache immutably.


def test_oversized_upload_is_refused_before_decoding(store_dir):
    raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * (images.MAX_UPLOAD_BYTES + 1)
    with pytest.raises(images.ImageError) as exc:
        images.store("alice", raw, name="bomb.png")
    assert "limit" in str(exc.value)


def test_empty_and_undecodable_uploads_are_refused(store_dir):
    with pytest.raises(images.ImageError):
        images.store("alice", b"", name="empty.png")
    with pytest.raises(images.ImageError):
        images.store("alice", b"this is not an image at all", name="text.txt")


# --- bounded before it is read ----------------------------------------------------------
#
# The 12 MB cap used to be checked after the route had read the whole body into memory
# (`await file.read()`), and Pillow tried every decoder it has before the format allowlist was
# looked at, so a TIFF was opened and fully decoded only to be refused. The gateway sets no
# body limit and Caddy none, so these are the rail's own bounds: a ceiling on the request body
# before FastAPI parses the form, a stepped read in the route, and `formats=` on Image.open.


def test_a_format_off_the_allowlist_never_reaches_its_decoder(store_dir, monkeypatch):
    from PIL import TiffImagePlugin

    calls: list[int] = []
    real = TiffImagePlugin.TiffImageFile._open

    def spy(self):
        calls.append(1)
        return real(self)

    monkeypatch.setattr(TiffImagePlugin.TiffImageFile, "_open", spy)
    raw = png_bytes(40, 30, fmt="TIFF")
    # The spy is live: Pillow left to itself opens this file through it.
    assert Image.open(io.BytesIO(raw)).format == "TIFF" and calls
    calls.clear()
    with pytest.raises(images.ImageError) as exc:
        images.store("alice", raw, name="scan.tiff")
    assert calls == [], "the TIFF decoder was reached before the allowlist refused the file"
    assert "PNG, JPEG, GIF, BMP or WEBP" in str(exc.value)


class _Upload:
    """An UploadFile stand-in holding `size` bytes, counting how many the route takes."""

    filename = "huge.png"

    def __init__(self, size: int):
        self.size = size
        self.given = 0

    async def read(self, n: int = -1) -> bytes:
        left = self.size - self.given
        n = left if n is None or n < 0 else min(n, left)
        self.given += n
        return b"\0" * n


def _upload(file: _Upload):
    from watch_builder.api import app as app_mod
    from watch_builder.api.identity import Identity

    return asyncio.run(app_mod.upload_image(file=file, ident=Identity("alice", False)))


def test_the_route_stops_reading_an_upload_at_the_cap(store_dir):
    """A 50 MB upload: refused with a 413 having read at most the cap and one step of it,
    where the route used to read all 50 MB before anything looked at the size."""
    from watch_builder.api import app as app_mod

    big = _Upload(50 * 1024 * 1024)
    with pytest.raises(HTTPException) as exc:
        _upload(big)
    assert exc.value.status_code == 413, exc.value.detail
    assert big.given <= images.MAX_UPLOAD_BYTES + app_mod.READ_STEP, big.given
    assert "12 MB" in exc.value.detail


def test_an_upload_exactly_at_the_cap_reaches_the_decoder(store_dir):
    """The boundary, and the control: a read that refused everything would pass the test
    above. At exactly the cap the bytes reach images.store, which refuses these as no image."""
    exact = _Upload(images.MAX_UPLOAD_BYTES)
    with pytest.raises(HTTPException) as exc:
        _upload(exact)
    assert exc.value.status_code == 422 and exact.given == images.MAX_UPLOAD_BYTES


_BOUNDARY = b"wbtestboundary"
_PART_HEAD = (b"--" + _BOUNDARY + b'\r\nContent-Disposition: form-data; name="file"; '
              b'filename="huge.png"\r\nContent-Type: image/png\r\n\r\n')
_STEP = 64 * 1024


def _endless(total: int):
    """A multipart body that opens a file part and then streams filler, `total` bytes in all."""
    yield _PART_HEAD
    sent = len(_PART_HEAD)
    filler = b"\0" * _STEP
    while sent < total:
        yield filler
        sent += _STEP


def _asgi_post(path: str, headers: list[tuple[bytes, bytes]], chunks):
    """POST through the real app at the ASGI level, with a receive() that counts its calls and
    the body bytes it hands over. -> (status, body, receive calls, bytes delivered)."""
    from watch_builder.api.app import app

    seen = {"calls": 0, "bytes": 0}
    sent: list[dict] = []
    it = iter(chunks)

    async def receive():
        seen["calls"] += 1
        chunk = next(it, None)
        if chunk is None:
            return {"type": "http.request", "body": b"", "more_body": False}
        seen["bytes"] += len(chunk)
        return {"type": "http.request", "body": chunk, "more_body": True}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
             "root_path": "", "query_string": b"", "client": ("127.0.0.1", 50000),
             "server": ("testserver", 80),
             "headers": [(b"x-platform-user", b"alice"),
                         (b"content-type", b"multipart/form-data; boundary=" + _BOUNDARY),
                         *headers]}
    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], body, seen["calls"], seen["bytes"]


def test_a_declared_length_over_the_limit_is_refused_before_one_byte_is_read(store_dir):
    status, body, calls, _ = _asgi_post(
        "/api/images", [(b"content-length", str(50 * 1024 * 1024).encode())],
        _endless(50 * 1024 * 1024))
    assert (status, calls) == (413, 0), (status, body[:200], calls)
    assert "12 MB" in json.loads(body)["detail"]


def test_a_body_with_no_length_is_cut_off_at_the_limit(store_dir, monkeypatch):
    """Chunked, so nothing is declared: the running count stops it. At a lowered cap, which
    the ceiling reads at call time, so the form parser spools a few hundred KB, not 12 MB."""
    from watch_builder.api import app as app_mod

    monkeypatch.setattr(images, "MAX_UPLOAD_BYTES", 256 * 1024)
    status, body, _, delivered = _asgi_post(
        "/api/images", [(b"transfer-encoding", b"chunked")], _endless(50 * 1024 * 1024))
    assert status == 413, (status, body[:200])
    assert delivered <= app_mod.upload_body_limit() + _STEP, delivered
    assert "0.25 MB" in json.loads(body)["detail"]


def test_the_ceiling_leaves_a_real_upload_and_other_routes_alone(store_dir):
    """The control for the two above: a ceiling that refused every POST would pass them."""
    from fastapi.testclient import TestClient

    from watch_builder.api.app import app

    client = TestClient(app)
    r = client.post("/api/images", headers={"X-Platform-User": "alice"},
                    files={"file": ("ok.png", png_bytes(40, 30), "image/png")})
    assert r.status_code == 200, r.text
    # A body over the image limit on another route is that route's business, not this one's.
    status, _, calls, _ = _asgi_post(
        "/api/validate", [(b"content-length", str(50 * 1024 * 1024).encode())], iter([b"{}"]))
    assert status != 413 and calls >= 1


def test_jpeg_is_accepted_and_normalised_to_png(store_dir):
    rec = images.store("alice", png_bytes(100, 80, fmt="JPEG"), name="photo.jpg")
    raw = images.raw_png("alice", rec.id)
    assert raw is not None
    assert Image.open(io.BytesIO(raw)).format == "PNG"


def test_uploads_are_owner_scoped(store_dir):
    rec = images.store("alice", png_bytes(50, 50), name="mine.png")
    assert images.load_source("bob", rec.id) is None
    assert images.listing("bob") == []
    assert images.load_source("alice", rec.id) is not None


def test_delete_reports_whether_it_removed_anything(store_dir):
    rec = images.store("alice", png_bytes(50, 50), name="x.png")
    assert images.delete("alice", rec.id) is True
    assert images.delete("alice", rec.id) is False


# --- per-platform rendering -----------------------------------------------------------


def _design(elements, platforms):
    return Design.model_validate({
        "schema": 1, "name": "T", "background": "#000000",
        "platforms": list(platforms), "elements": elements,
    })


def test_each_platform_gets_its_own_pixel_size(store_dir):
    """The finding that drives this whole module.

    A box of "70%" is 140px on emery and 100px on aplite. One shared file would be clipped
    on one and tiled on the other.
    """
    rec = images.store("alice", png_bytes(300, 300), name="q.png")
    d = _design([{"type": "image", "image_id": rec.id, "x": 0, "y": 0,
                  "w": "70%", "h": "30%"}], ("emery", "aplite", "gabbro"))
    files, report = images.render_resources(d, "alice")

    sizes = {r["platform"]: tuple(r["pixels"]) for r in report}
    assert sizes["emery"] == (140, 68)      # 70% of 200, 30% of 228
    assert sizes["aplite"] == (101, 50)     # 70% of 144 = 100.8, rounded
    assert sizes["gabbro"] == (182, 78)     # 70% of 260, 30% of 260
    assert len({tuple(v) for v in sizes.values()}) == 3, "three platforms, three sizes"

    # And the emitted PNG really is that size, not merely reported as it.
    for r in report:
        data = files[f"resources/images/{r['resource'].lower()}.png"]
        assert Image.open(io.BytesIO(data)).size == tuple(r["pixels"])


def test_two_colour_platforms_get_a_two_colour_image(store_dir):
    """aplite, diorite and flint are three of the seven platforms, so this is the common case.

    Dithered rather than hard-thresholded: a photo thresholded at 50% loses every mid-tone
    at once.
    """
    grad = Image.linear_gradient("L").convert("RGB").resize((120, 120))
    buf = io.BytesIO(); grad.save(buf, format="PNG")
    rec = images.store("alice", buf.getvalue(), name="grad.png")

    d = _design([{"type": "image", "image_id": rec.id, "x": 0, "y": 0,
                  "w": "50%", "h": "50%"}], ("aplite", "emery"))
    files, report = images.render_resources(d, "alice")

    def distinct(platform):
        row = next(r for r in report if r["platform"] == platform)
        data = files[f"resources/images/{row['resource'].lower()}.png"]
        im = Image.open(io.BytesIO(data)).convert("RGB")
        return len({px for px in im.getdata()})

    assert distinct("aplite") <= 2
    assert distinct("emery") > 2


def test_colour_platforms_are_snapped_to_the_palette_cube(store_dir):
    """Every channel must be one of 0, 85, 170, 255 or the watch cannot show it."""
    im = Image.new("RGB", (40, 40))
    im.putdata([(i % 256, (i * 3) % 256, (i * 7) % 256) for i in range(40 * 40)])
    buf = io.BytesIO(); im.save(buf, format="PNG")
    rec = images.store("alice", buf.getvalue(), name="noise.png")

    d = _design([{"type": "image", "image_id": rec.id, "x": 0, "y": 0,
                  "w": "50%", "h": "50%"}], ("emery",))
    files, _ = images.render_resources(d, "alice")
    data = next(iter(files.values()))
    seen = {c for px in Image.open(io.BytesIO(data)).convert("RGB").getdata() for c in px}
    assert seen <= {0, 85, 170, 255}, sorted(seen)


def test_fit_modes_change_the_framing_not_the_size(store_dir):
    # Deliberately STRUCTURED and asymmetric on both axes. A solid-colour source cannot
    # distinguish `cover` from `stretch` -- both fill the box, and a flat colour is identical
    # whether it was cropped or distorted. An earlier version of this test used one and
    # passed two of three modes as "the same image".
    src = Image.new("RGB", (200, 100), (10, 10, 10))
    draw = ImageDraw.Draw(src)
    draw.rectangle([0, 0, 59, 39], fill=(255, 0, 0))        # top-left block
    draw.ellipse([150, 60, 195, 95], fill=(0, 170, 255))    # bottom-right blob
    draw.line([0, 99, 199, 0], fill=(255, 255, 0), width=5)  # diagonal
    buf = io.BytesIO(); src.save(buf, format="PNG")
    rec = images.store("alice", buf.getvalue(), name="wide.png")
    out = {}
    for fit in ("contain", "cover", "stretch"):
        d = _design([{"type": "image", "image_id": rec.id, "x": 0, "y": 0,
                      "w": "50%", "h": "50%", "fit": fit}], ("emery",))
        files, report = images.render_resources(d, "alice")
        out[fit] = (tuple(report[0]["pixels"]), next(iter(files.values())))
    sizes = {v[0] for v in out.values()}
    assert len(sizes) == 1, "fit must not change the shipped pixel size"
    # And they are genuinely different images.
    assert len({v[1] for v in out.values()}) == 3


def test_a_missing_image_is_a_clear_error_not_a_crash(store_dir):
    d = _design([{"type": "image", "image_id": "deadbeef", "x": 0, "y": 0,
                  "w": "50%", "h": "50%"}], ("emery",))
    with pytest.raises(images.ImageError) as exc:
        images.render_resources(d, "alice")
    assert "not in your uploads" in str(exc.value)
    assert "elements[0]" in str(exc.value)


def test_another_owners_image_is_not_reachable_through_a_design(store_dir):
    """The design document is user-supplied, so image_id is user-supplied.

    Without owner scoping in render_resources, pasting someone else's id into a design would
    read their upload.
    """
    rec = images.store("alice", png_bytes(60, 60), name="private.png")
    d = _design([{"type": "image", "image_id": rec.id, "x": 0, "y": 0,
                  "w": "50%", "h": "50%"}], ("emery",))
    with pytest.raises(images.ImageError):
        images.render_resources(d, "bob")


def test_budget_warning_fires_only_when_a_platform_is_over(store_dir):
    small = [{"element": 0, "platform": "emery", "resource": "IMG_0_EMERY",
              "pixels": [10, 10], "bytes": 500, "colors": 64}]
    assert images.budget_warnings(small) == []

    big = [{"element": 0, "platform": "emery", "resource": "IMG_0_EMERY",
            "pixels": [200, 228], "bytes": 200 * 1024, "colors": 64},
           {"element": 1, "platform": "emery", "resource": "IMG_1_EMERY",
            "pixels": [200, 228], "bytes": 100 * 1024, "colors": 64}]
    warn = images.budget_warnings(big)
    assert len(warn) == 1 and "emery" in warn[0] and "cannot" in warn[0]


# --- codegen integration --------------------------------------------------------------


def test_one_resource_per_platform_each_with_its_own_target(store_dir):
    d = _design([{"type": "image", "image_id": "x", "x": 0, "y": 0,
                  "w": "50%", "h": "50%"}], ("emery", "gabbro"))
    import json
    media = json.loads(generate_package_json(d))["pebble"]["resources"]["media"]
    assert [m["name"] for m in media] == ["IMG_0_EMERY", "IMG_0_GABBRO"]
    # targetPlatforms per entry is what keeps a platform's pack free of the others' variants.
    for m in media:
        assert len(m["targetPlatforms"]) == 1
        assert m["name"].endswith(m["targetPlatforms"][0].upper())


def test_generated_c_loads_the_platform_specific_resource(store_dir):
    d = _design([{"type": "image", "image_id": "x", "x": 0, "y": 0,
                  "w": "50%", "h": "50%"}], ("emery", "aplite"))
    c = generate_c(d)
    assert "static GBitmap *s_img_0;" in c
    assert "RESOURCE_ID_IMG_0_EMERY" in c
    assert "RESOURCE_ID_IMG_0_APLITE" in c
    # Transparency: without GCompOpSet a "contain" fit paints its padding black.
    assert "GCompOpSet" in c
    # And the bitmap's lifetime is the layer's.
    assert "load_images();" in c
    assert "unload_images();" in c
    assert "gbitmap_destroy(s_img_0)" in c


def test_no_image_means_no_image_machinery(store_dir):
    c = generate_c(_design([{"type": "time", "x": 0, "y": "40%", "w": "100%"}], ("emery",)))
    for needle in ("GBitmap", "load_images", "GCompOpSet", "gbitmap_"):
        assert needle not in c, needle


def test_resource_name_is_unique_per_element_and_platform():
    seen = {image_resource_name(i, p) for i in range(3) for p in PLATFORMS}
    assert len(seen) == 3 * len(PLATFORMS)
