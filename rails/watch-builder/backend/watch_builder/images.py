"""Uploaded images: store one source, emit one resource per platform.

WHY THIS IS NOT JUST "SAVE THE PNG"
-----------------------------------
`graphics_draw_bitmap_in_rect` does NOT scale. Measured on the real SDK: a rect smaller than
the bitmap CLIPS to the top-left, and a rect larger TILES the source to fill it. The rect is a
viewport, not a scale factor.

So an image has to be shipped at the exact pixel size it will be drawn. And because this rail
expresses boxes as percentages, that size differs per target platform: a box of "60%" is 120px
wide on emery and 86px on aplite. One upload therefore becomes one resource per platform,
resampled to that platform's resolved box.

Getting this wrong is not subtle. Drag a corner smaller and the image crops; drag it larger
and it repeats. A user would reasonably read both as a bug in the editor.

THE SECOND REASON: THE RESOURCE BUDGET
--------------------------------------
A published app gets 256 KB of resources total (1024 KB sideloaded). A phone photo is 3-6 MB.
Uploading one and finding out at build time produces a waf error about pack size that says
nothing about the photo, so the downscale happens at UPLOAD, immediately, and the stored
source is already bounded.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from watch_builder.config import settings
from watch_builder.design import PLATFORMS, Platform

# Nothing on a Pebble screen is bigger than gabbro's 260x260, so a stored source larger than
# that is wasted bytes. Kept a little above the largest screen so an image intended to be
# cropped or panned still has something to work with.
MAX_STORED = 320

# Hard ceiling on an upload before it is even decoded. A 25 MB file is a mistake or an
# attack, and PIL will happily try to allocate a decompression bomb. The route reads at most
# this much (api/app.py `_read_capped`), and the body ceiling in front of the form parser is
# this plus the multipart framing (api/body_limit.py), so neither ever holds the whole of an
# oversized upload.
MAX_UPLOAD_BYTES = 12 * 1024 * 1024

# PIL's own guard, lowered from its 89 Mpixel default. 320x320 is the most we keep, so
# anything past ~40 Mpixel is a bomb rather than a photo.
Image.MAX_IMAGE_PIXELS = 40_000_000

ACCEPTED = {"PNG", "JPEG", "GIF", "BMP", "WEBP"}
_ACCEPTED_WORDS = "PNG, JPEG, GIF, BMP or WEBP"

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


class ImageError(ValueError):
    """The upload could not be used. The message is shown to the user verbatim."""


def too_large() -> str:
    """The refusal for an upload over MAX_UPLOAD_BYTES when its full size is not known (it was
    never read whole). The figure is derived at call time, never restated."""
    return (f"that file is over the {MAX_UPLOAD_BYTES / (1024 * 1024):g} MB limit. A watchface "
            "screen is at most 260x260, so a smaller image loses nothing.")


@dataclass
class StoredImage:
    id: str
    name: str
    width: int
    height: int
    bytes: int
    uploaded: float


def _owner_dir(owner: str | None) -> Path:
    key = "_standalone" if not owner else _SAFE.sub("_", owner)[:64]
    return Path(settings.data_dir) / key / "images"


def _paths(owner: str | None, image_id: str) -> tuple[Path, Path]:
    d = _owner_dir(owner)
    safe = _SAFE.sub("_", image_id)
    return d / f"{safe}.png", d / f"{safe}.json"


# Windows refuses a rename onto a file that is open without delete sharing: another store's
# rename onto the same name at that moment, or something outside the process (a malware scanner,
# the indexer) reading a file just written. A POSIX rename is atomic and refuses neither. The
# rail's container is Linux, but its suite runs on Windows too, where the delta audit's thread
# repro (1,200 stores of one image, four at a time) had 903 renames refused with a temp per
# store, and none once a refused one was tried again for up to about half a second, as pip's own
# replace does. On POSIX a refusal is a real one, and raises at once.
_WINDOWS = os.name == "nt"
_RENAME_RETRY_S = (0.01, 0.02, 0.04, 0.08, 0.16, 0.32)


def _replace(tmp: str, path: Path) -> None:
    for wait in (*_RENAME_RETRY_S, None):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if not _WINDOWS or wait is None:
                raise
            time.sleep(wait)


def _write_whole(path: Path, data: bytes) -> None:
    """Write `data` to `path` through a temp file of this call's own, renamed over it.

    The temp was one fixed `<id>.png.tmp` until 2026-10-15. That was safe while the store ran on
    the event loop, one at a time; in the threadpool two uploads of the same bytes (a double
    drop, two tabs) wrote and renamed the same temp at once, so one of them found it gone or
    still open and the user got a 500 (617 errors in 1,200 calls of the delta audit's repro). The
    id is the content's hash, so both writes hold the same bytes, and whichever rename lands last
    leaves a whole file. A write that fails takes its temp with it."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=f"{path.suffix}.tmp")
    os.close(fd)
    try:
        Path(tmp).write_bytes(data)
        _replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def store(owner: str | None, raw: bytes, *, name: str) -> StoredImage:
    """Validate, downscale, normalise to PNG, and save.

    Everything is converted to PNG on the way in. The build path only ever reads PNG, and a
    single stored format means the per-platform resampling has one code path rather than one
    per upload type.
    """
    if not raw:
        raise ImageError("that file is empty")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ImageError(
            f"that file is {len(raw) // (1024 * 1024)} MB; the limit is "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB. A watchface screen is at most 260x260, "
            "so a smaller image loses nothing."
        )

    try:
        # `formats=`: only the allowlisted plugins may identify the file. Without it Pillow
        # offers the bytes to every decoder it has (TIFF, PSD, EPS, ...) and the allowlist
        # was checked only after one had opened and decoded them.
        im = Image.open(io.BytesIO(raw), formats=sorted(ACCEPTED))
    except UnidentifiedImageError as exc:
        raise ImageError(f"that is not a {_ACCEPTED_WORDS} image") from exc
    except Exception as exc:  # PIL raises a wide variety for a malformed file
        raise ImageError(f"that does not decode as an image ({exc})") from exc

    # Checked again, and before a pixel is decoded: `formats=` is what holds it, this is
    # what says so if a Pillow upgrade ever widens what a plugin reports.
    fmt = (im.format or "").upper()
    if fmt not in ACCEPTED:
        raise ImageError(f"{fmt or 'that format'} is not supported; use {_ACCEPTED_WORDS}")
    try:
        im.load()
    except Exception as exc:  # PIL raises a wide variety for a malformed file
        raise ImageError(f"that does not decode as an image ({exc})") from exc

    # EXIF orientation applied before anything else, or a phone photo arrives rotated and the
    # user's crop and position are all against the wrong axis.
    im = ImageOps.exif_transpose(im)
    im = im.convert("RGBA")

    if max(im.size) > MAX_STORED:
        im.thumbnail((MAX_STORED, MAX_STORED), Image.LANCZOS)

    out = io.BytesIO()
    im.save(out, format="PNG", optimize=True)
    data = out.getvalue()

    image_id = hashlib.sha256(data).hexdigest()[:16]
    png_path, meta_path = _paths(owner, image_id)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    _write_whole(png_path, data)

    rec = StoredImage(id=image_id, name=name[:80] or "image", width=im.width,
                      height=im.height, bytes=len(data), uploaded=time.time())
    # Whole too: two stores of the same bytes write this record at once as well, and two
    # truncating writes of different lengths leave JSON that `listing` skips as unreadable.
    _write_whole(meta_path, json.dumps(rec.__dict__, indent=2).encode("utf-8"))
    return rec


def load_source(owner: str | None, image_id: str) -> Image.Image | None:
    png_path, _ = _paths(owner, image_id)
    if not png_path.is_file():
        return None
    im = Image.open(png_path)
    im.load()
    return im.convert("RGBA")


def raw_png(owner: str | None, image_id: str) -> bytes | None:
    """The stored source, for the editor's live preview."""
    png_path, _ = _paths(owner, image_id)
    return png_path.read_bytes() if png_path.is_file() else None


def listing(owner: str | None) -> list[dict]:
    d = _owner_dir(owner)
    if not d.is_dir():
        return []
    rows = []
    for meta in d.glob("*.json"):
        try:
            rows.append(json.loads(meta.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(rows, key=lambda r: -float(r.get("uploaded") or 0))


def delete(owner: str | None, image_id: str) -> bool:
    png_path, meta_path = _paths(owner, image_id)
    if not png_path.is_file():
        return False
    png_path.unlink()
    meta_path.unlink(missing_ok=True)
    return True


# --------------------------------------------------------------------------------------
# Per-platform rendering
# --------------------------------------------------------------------------------------

FitMode = str  # "contain" | "cover" | "stretch"


def render_for_platform(src: Image.Image, box: tuple[int, int], plat: Platform,
                        *, fit: FitMode = "contain") -> Image.Image:
    """Resample and quantise one image for one platform's resolved box.

    `box` is the exact pixel size the C will draw into, so the result is that size and the
    SDK neither clips nor tiles.
    """
    w, h = max(1, box[0]), max(1, box[1])

    if fit == "stretch":
        out = src.resize((w, h), Image.LANCZOS)
    else:
        # contain: whole image inside the box, transparent padding.
        # cover: box fully covered, overflow centre-cropped.
        scale = (min if fit == "contain" else max)(w / src.width, h / src.height)
        interim = src.resize((max(1, round(src.width * scale)),
                              max(1, round(src.height * scale))), Image.LANCZOS)
        out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ox, oy = (w - interim.width) // 2, (h - interim.height) // 2
        out.paste(interim, (ox, oy), interim)
        if fit == "cover":
            out = out.crop((0, 0, w, h))

    return _quantise(out, plat)


def _quantise(im: Image.Image, plat: Platform) -> Image.Image:
    """Reduce to what the platform can actually display.

    Two-colour platforms get an ordered dither rather than a hard threshold: a photo
    thresholded at 50% loses every mid-tone at once, and aplite, diorite and flint are three
    of the seven platforms, so this is the common case rather than a corner.

    Colour platforms are snapped to the 4x4x4 cube. Snapped to NEAREST here, deliberately
    unlike the firmware's GColorFromRGB, which truncates: truncation is right for a colour a
    developer specified and wrong for resampled photographic pixels, where it would darken
    the whole image by up to a level per channel.
    """
    alpha = im.getchannel("A")
    rgb = im.convert("RGB")

    if plat.colors == 2:
        # Dither the luminance, then restore transparency. Pebble's 1-bit bitmaps support a
        # transparent index, so a padded "contain" fit still composites correctly.
        bw = rgb.convert("L").convert("1", dither=Image.FLOYDSTEINBERG).convert("RGB")
        out = bw
    else:
        levels = bytes(_nearest_level(i) for i in range(256))
        out = rgb.point(levels * 3)

    out = out.convert("RGBA")
    out.putalpha(alpha)
    return out


_LEVELS = (0, 85, 170, 255)


def _nearest_level(value: int) -> int:
    return min(_LEVELS, key=lambda lv: abs(lv - value))


def encode_png(im: Image.Image, plat: Platform) -> bytes:
    """PNG bytes for the SDK's resource pipeline.

    Palettised, because the images guide is explicit that a palettised PNG is what the
    `bitmap` resource type wants and an unpalettised 1-bit PNG is a build failure. The
    palette is built from what survived quantisation, so a two-colour platform gets a
    2-entry palette and pays almost nothing.
    """
    colors = 2 if plat.colors == 2 else 64
    has_alpha = im.getchannel("A").getextrema()[0] < 255

    if has_alpha:
        # PIL's RGBA -> P path allocates the transparency index itself and writes the tRNS
        # chunk. Doing it by hand means picking an index, and picking one that an adaptive
        # palette also used makes an opaque colour transparent somewhere in the image.
        pal = im.convert("P", palette=Image.ADAPTIVE, colors=colors)
    else:
        pal = im.convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=colors)

    out = io.BytesIO()
    pal.save(out, format="PNG", optimize=True)
    return out.getvalue()


def platform_box(el_box: tuple[int, int], platform_name: str) -> tuple[int, int]:
    """Clamp a resolved box to something drawable on that platform."""
    plat = PLATFORMS[platform_name]
    return (max(1, min(el_box[0], plat.width)), max(1, min(el_box[1], plat.height)))


def render_resources(design, owner: str | None) -> tuple[dict[str, bytes], list[dict]]:
    """Every image resource a build of `design` needs, plus a per-file size report.

    Returns ``({relative path: png bytes}, [{...}])``. The paths match what
    ``codegen.generate_package_json`` declared, and the byte sizes are handed back so the
    caller can show the resource budget rather than letting a build fail on pack size with
    an error that never mentions the images.

    Lives here rather than in codegen because it needs the owner's stored uploads, and
    codegen is deliberately pure: the same design must generate the same C for anyone.
    """
    # Imported here, not at module scope: codegen imports design, layout imports codegen, and
    # a module-level import in both directions is a cycle.
    from watch_builder.codegen import image_resource_name
    from watch_builder.layout import resolve_box

    files: dict[str, bytes] = {}
    report: list[dict] = []

    for idx, el in enumerate(design.elements):
        if el.type != "image":
            continue
        src = load_source(owner, el.image_id or "")
        if src is None:
            raise ImageError(f"elements[{idx}] references image {el.image_id!r}, "
                             "which is not in your uploads")

        for name in design.platforms:
            plat = PLATFORMS[name]
            box = resolve_box(el, plat, idx)
            size = platform_box((box.w, box.h), name)
            rendered = render_for_platform(src, size, plat, fit=el.fit)
            data = encode_png(rendered, plat)

            res = image_resource_name(idx, name)
            files[f"resources/images/{res.lower()}.png"] = data
            report.append({
                "element": idx,
                "platform": name,
                "resource": res,
                "pixels": [size[0], size[1]],
                "bytes": len(data),
                "colors": plat.colors,
            })

    return files, report


# The published ceiling. Sideloading allows 1024 KB, but a face that can only be installed
# over a developer connection is a different product, so the number worth warning about is
# the one that decides whether it can ship.
RESOURCE_BUDGET_BYTES = 256 * 1024


def budget_warnings(report: list[dict]) -> list[str]:
    """Per-platform totals that would not fit in a published app."""
    per_platform: dict[str, int] = {}
    for row in report:
        per_platform[row["platform"]] = per_platform.get(row["platform"], 0) + row["bytes"]

    out = []
    for name, total in sorted(per_platform.items()):
        if total > RESOURCE_BUDGET_BYTES:
            out.append(
                f"{name}: images total {total // 1024} KB, over the "
                f"{RESOURCE_BUDGET_BYTES // 1024} KB an app-store release allows. It will "
                f"still build and sideload (the sideload ceiling is 1024 KB), but it cannot "
                f"be published. Shrink the image boxes or use fewer images."
            )
    return out
