# Watch Builder rail backend, with the Pebble SDK baked in.
#
# Build (from the watch-builder rail root):
#   docker build -f deploy/Dockerfile -t watch-builder-backend .
# In the platform stack the compose file builds it.
#
# WHY THIS IMAGE IS THE LARGEST IN THE TREE (~2 GB)
# -------------------------------------------------
# It carries the whole Pebble SDK: the ARM cross-toolchain, seven platform SDKs, and QEMU
# builds of seven watch emulators. The alternative was mounting the host's Docker socket and
# driving a sibling SDK image, which was rejected for two reasons: a rail that can drive the
# host's Docker is a privilege-escalation path, and a rail that cannot run without the host's
# daemon is no longer an independent deployable (the rule docs/RAIL_CONTRACT.md opens with).
#
# The size is paid once at build. A warm seven-platform compile measures ~4 s.
#
# Base is debian bookworm via python:3.11-slim, matching the other rails, because the SDK's
# prebuilt toolchain and QEMU are glibc binaries and will not run on Alpine.
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

# Two dependency groups, kept separate because they fail differently.
#
# The first is what the SDK's own install page lists for Ubuntu (node, SDL2, glib, pixman,
# zlib, sndio) plus libpng/freetype for the resource pipeline. Without these the emulator
# will not start and the failure names a missing shared object.
#
# build-essential is the second, and it is the one that is easy to miss: `pebble build`
# shells out to `mcrun` for any Moddable/Alloy work, which runs `make` and a host compiler.
# Without it the only error is "No such file or directory: .../mc.xsa", naming the missing
# OUTPUT rather than the missing toolchain. C-only builds do not need it, so it can be absent
# and everything looks fine until the first JS project.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git \
        nodejs npm \
        libsdl2-2.0-0 libglib2.0-0 libpixman-1-0 zlib1g libsndio7.0 \
        libpng16-16 libfreetype6 \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    "fastapi>=0.111" \
    "uvicorn[standard]>=0.30" \
    "pydantic>=2.7" \
    "pydantic-settings>=2.2" \
    "pillow>=10.3" \
    "python-multipart>=0.0.9"

# uv, the installer the Pebble SDK docs specify for pebble-tool.
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"
RUN uv tool install pebble-tool

# Pre-seed the SDK so the first build in a fresh container does not pay for the download.
#
# NOT `|| true`. Masking the failure here is how an image ships that reports a pebble
# version, passes a health check, and fails every single build with an error naming
# arm-none-eabi-gcc rather than the absent SDK.
RUN pebble sdk install latest

# The SDK lands in the XDG data dir, NOT ~/.pebble-sdk. Recorded because mounting a volume at
# the latter makes the tool treat that empty directory as its SDK home, re-download into it,
# and then fail on a toolchain it cannot execute.
ENV WATCH_BUILDER_SDK_ROOT=/root/.local/share/pebble-sdk

COPY backend /app/backend
RUN pip install --no-cache-dir /app/backend

# Runs as root, unlike the other rails, and this is a deliberate exception rather than an
# oversight. `pebble` writes to the SDK tree it owns under /root and spawns QEMU processes;
# the SDK is installed by root at build time and the tool does not support a split. A uid
# split here would mean re-installing the entire 2 GB SDK under a second user.
#
# The containment that matters is elsewhere: the only thing it executes is C that its OWN
# deterministic generator produced from a schema-validated document, and it never runs a
# design from another user. Its one untrusted file is an image upload (POST /api/images),
# which Pillow decodes in this root process. images.store bounds it: MAX_UPLOAD_BYTES before
# decoding, a lowered Image.MAX_IMAGE_PIXELS against decompression bombs, PNG/JPEG/GIF/BMP/WEBP
# only, and a re-encode to a downscaled PNG, so the SDK only ever reads PNGs this rail wrote.
# The format list is checked after decoding, so keeping Pillow current is part of this
# exception. If it ever accepts a hand-written .c or a third-party project, that changes and
# the uid split becomes mandatory. See docs/DESIGN.md.
RUN mkdir -p /data/designs /tmp/watch-builder

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

EXPOSE 8920
CMD ["uvicorn", "watch_builder.api.app:app", "--host", "0.0.0.0", "--port", "8920"]
