# Fun in the Term — backend + the games/toys it runs, all self-hosted in this image.
# Base matches the other rails (python:3.11-slim = Debian bookworm; already cached on the host).
#
# Build (from the terminal-fun repo root):  docker build -f deploy/Dockerfile -t terminal-fun-backend .
# In the platform stack it's built for you by platform/deploy/docker-compose.yml.
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------------------
# 1) Games + toys from Debian, plus build/download deps (used by layer 2, trimmed there).
#    Kept as its own layer so this slow step stays cached across download tweaks.
# ---------------------------------------------------------------------------
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl git jq \
        build-essential libncurses-dev cpanminus \
        ncurses-base ncurses-bin ncurses-term bsdmainutils procps bash \
        nethack-console crawl bsdgames \
        bastet ninvaders moon-buggy \
        cmatrix sl nyancat cbonsai \
        libcurses-perl \
        cowsay fortune-mod fortunes-min lolcat figlet toilet \
        byobu ccze htop pv; \
    chmod -R a+rwX /var/games 2>/dev/null || true

# ---------------------------------------------------------------------------
# 2) Extras not packaged in Debian, then trim the build toolchain. Release assets
#    may be a tarball OR a raw binary, so each download probes with `tar -tzf`.
# ---------------------------------------------------------------------------
RUN set -eux; \
    # genact — fake activity (Rust, static musl).
    url="$(curl -fsSL https://api.github.com/repos/svenstaro/genact/releases/latest \
        | jq -r '.assets[].browser_download_url' | grep -E 'x86_64-unknown-linux-musl' | grep -v '\.sha' | head -n1)"; \
    curl -fsSL "$url" -o /tmp/genact.dl; \
    if tar -tzf /tmp/genact.dl >/dev/null 2>&1; then tar -xzf /tmp/genact.dl -C /tmp; \
        install -m0755 "$(find /tmp -type f -name genact | head -n1)" /usr/local/bin/genact; \
    else install -m0755 /tmp/genact.dl /usr/local/bin/genact; fi; \
    # ascii-movie — Star Wars ASCIImation, self-hosted (Go).
    url="$(curl -fsSL https://api.github.com/repos/gabe565/ascii-movie/releases/latest \
        | jq -r '.assets[].browser_download_url' | grep -E 'linux_amd64\.tar\.gz$' | head -n1)"; \
    curl -fsSL "$url" -o /tmp/ascii-movie.dl; \
    if tar -tzf /tmp/ascii-movie.dl >/dev/null 2>&1; then tar -xzf /tmp/ascii-movie.dl -C /tmp; \
        install -m0755 "$(find /tmp -type f -name ascii-movie | head -n1)" /usr/local/bin/ascii-movie; \
    else install -m0755 /tmp/ascii-movie.dl /usr/local/bin/ascii-movie; fi; \
    # no-more-secrets (Sneakers decrypt FX) — tiny C build.
    git clone --depth 1 https://github.com/bartobri/no-more-secrets /tmp/nms; \
    make -C /tmp/nms nms sneakers; make -C /tmp/nms install; \
    # 2048 — single-file C.
    curl -fsSL https://raw.githubusercontent.com/mevdschee/2048.c/master/2048.c -o /tmp/2048.c; \
    gcc -o /usr/local/bin/2048 /tmp/2048.c; \
    # hollywood (fake movie-hacker screen). No Makefile install target; place the script in
    # /usr/games and its widgets in /usr/lib/hollywood (bin/hollywood resolves ../lib/hollywood).
    git clone --depth 1 https://github.com/dustinkirkland/hollywood /tmp/hollywood; \
    install -m0755 /tmp/hollywood/bin/hollywood /usr/games/hollywood; \
    cp -a /tmp/hollywood/lib/hollywood /usr/lib/hollywood; \
    { [ -d /tmp/hollywood/share/hollywood ] && cp -a /tmp/hollywood/share/hollywood /usr/share/hollywood; } || true; \
    chmod -R a+rX /usr/lib/hollywood; \
    # pipes.sh screensaver script.
    curl -fsSL https://raw.githubusercontent.com/pipeseroni/pipes.sh/master/pipes.sh -o /opt/hold-pipes.sh; \
    # asciiquarium (not in Debian): Perl script + Term::Animation from CPAN — best-effort.
    (cpanm --notest --quiet Term::Animation \
        && curl -fsSL https://raw.githubusercontent.com/cmatsuoka/asciiquarium/master/asciiquarium -o /usr/local/bin/asciiquarium \
        && chmod 0755 /usr/local/bin/asciiquarium) || echo "asciiquarium skipped"; \
    # Trim the build toolchain to keep the image lean.
    apt-get purge -y build-essential libncurses-dev git jq cpanminus; \
    apt-get autoremove -y; apt-get clean; \
    rm -rf /var/lib/apt/lists/* /tmp/*

# ---------------------------------------------------------------------------
# 3) Python runtime + the backend package + wrapper scripts.
# ---------------------------------------------------------------------------
RUN pip install --no-cache-dir "fastapi>=0.111" "uvicorn[standard]>=0.30" \
        "pydantic>=2.7" "pydantic-settings>=2.2" "httpx>=0.27"

# Wrapper scripts (cowfortune / bofh / bannershow / nms-demo) + assets.
COPY deploy/rootfs/ /
RUN mv /opt/hold-pipes.sh /opt/fun/bin/pipes.sh && chmod -R 0755 /opt/fun/bin

# The backend package.
COPY backend /app/backend
RUN pip install --no-cache-dir /app/backend

# Non-root runtime user. Games run under this uid inside the container (no host access).
# /data is the mount point for the per-user save/resume volume; own it so the named volume
# initialises writable by funuser (Docker copies the image dir's ownership into a fresh volume).
RUN useradd --create-home --uid 10001 funuser \
    && mkdir -p /data && chown funuser:funuser /data

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER funuser
WORKDIR /home/funuser

EXPOSE 8730
CMD ["uvicorn", "terminal_fun_app.main:app", "--host", "0.0.0.0", "--port", "8730"]
