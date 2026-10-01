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
        util-linux \
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

# Wrapper scripts (cowfortune / bofh / bannershow / nms-demo) + assets, plus the two
# privilege-handling scripts under /usr/local/bin (fun-runas, terminal-fun-entrypoint).
COPY deploy/rootfs/ /
RUN mv /opt/hold-pipes.sh /opt/fun/bin/pipes.sh && chmod -R 0755 /opt/fun/bin \
    && chmod 0755 /usr/local/bin/fun-runas /usr/local/bin/terminal-fun-entrypoint

# The backend package.
COPY backend /app/backend
RUN pip install --no-cache-dir /app/backend

# ---------------------------------------------------------------------------
# 4) TWO runtime uids, not one.
#
# WHY. The games include roguelikes with a working `!` shell escape, and this image accepted
# that on the grounds that "the container holds no secrets". It held one. uvicorn's environment
# carries BROKER_AUTH_TOKEN — the single credential deploy/docker-compose.yml hands all nine
# broker-client rails — and while pty_session.py replaces the child environment wholesale, the
# PID namespace is shared and the game ran as the SAME uid, so
# `tr '\0' '\n' < /proc/1/environ` handed any player the platform's broker credential. The same
# shell could read and delete every other player's saves under /data. Both found by the
# 2026-09-10 rail-backend audit; see docs/BACKLOG.md.
#
#   funuser  (10001)  uvicorn, and everything that touches /data
#   gameuser (10002)  the PTY children: every game, toy and shell escape
#   funshare (10101)  the one group they share, and the only thing they share
#
# WHAT THE GAME ACTUALLY NEEDS — read out of the backend, not assumed:
#   * a session HOME. Created by the backend (terminal_fun_app/sandbox.make_session_home) as
#     02770:funshare, so the game can write and the backend can still seed the save before
#     launch, capture it after, and delete the tree.
#   * /var/games. Debian's nethack writes its saves into a SHARED system dir keyed by player
#     name (terminal_fun_app/saves.py), and bsdgames keeps score files there. Both uids need
#     it: the game plays into it, the backend seeds and harvests it.
#   * NOT /data. Every read and write of the save volume happens in the BACKEND process —
#     saves.restore() before launch, saves.capture() after the session ends, and the /api/saves
#     routes. settings.data_dir is never handed to a child. So /data can be shut completely.
#
# 0700 on /data is therefore the whole fix for the second finding: gameuser cannot list it,
# let alone read or delete another player's save. It costs the game nothing, because the game
# was never reaching it.
#
# A NOTE FOR WHOEVER WIRES BROKER_AUTH_TOKEN_FILE. The two halves of this fix only add up if
# the token file is unreadable by gameuser. Moving the secret out of the environment stops
# `/proc/1/environ` yielding it; it does not stop `cat` on the file. Plain (non-swarm) compose
# bind-mounts a `secrets:` entry with the HOST file's ownership and ignores the uid/gid/mode
# keys — those are swarm-only — so a 0444 mount hands the credential straight back to the
# process this uid split exists to contain. Mount it readable by uid 10001 only, and check it
# from inside the container as gameuser before believing the finding is closed.
#
# Notes on the three steps below, kept out of the RUN so no comment sits inside a line
# continuation:
#   * gameuser gets a REAL shell, not nologin. nologin would prevent nothing — the escape runs
#     whatever $SHELL pty_session sets, which is bash by design — while byobu/tmux and anything
#     else resolving a shell through getpwuid() would break. The split is the containment; the
#     shell is expected to be reachable.
#   * /data's ownership and mode are set here so the NAMED VOLUME initialises that way: Docker
#     copies the image directory's ownership and mode into a fresh volume.
#   * nethack's save dir is pre-created rather than left to saves.py's mkdir at runtime. Created
#     then, it would take the backend's umask and come out group-read-only, and nethack
#     (gameuser) could not write its own save into it. 2770: setgid so anything either uid
#     creates stays in funshare, and no world bits — replacing this image's previous blanket
#     `chmod -R a+rwX /var/games`, which is how one player could delete another's live game.
RUN groupadd --gid 10101 funshare \
    && useradd --create-home --uid 10001 --groups funshare funuser \
    && useradd --create-home --uid 10002 --groups funshare --shell /bin/bash gameuser \
    && mkdir -p /data && chown funuser:funuser /data && chmod 0700 /data \
    && mkdir -p /var/games/nethack/save \
    && chgrp -R funshare /var/games \
    && find /var/games -type d -exec chmod 2770 {} + \
    && find /var/games -type f -exec chmod 0660 {} +

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# The DEFAULT is unchanged and deliberately so: funuser, non-root, no capabilities, games on
# the backend's own uid. That is what today's compose gets, byte for byte, and it must keep
# working — an image that requires a compose change to boot breaks the stack the moment it is
# pulled ahead of one.
#
# The split is switched on by the deployment, because changing uid needs CAP_SETUID and
# compose is where capabilities live. Two keys on the service (with cap_drop: ALL and
# no-new-privileges both staying exactly as they are):
#
#     user: "0:0"                 # PID 1 starts as root ONLY so the entrypoint can drop
#     cap_add: [SETUID, SETGID]   # the two drop-privilege syscalls, nothing else
#
# `user:` is required because Docker never populates the ambient capability set, so caps
# granted to a container whose USER is non-root never reach the process. The entrypoint drops
# to funuser immediately, keeps those two caps ambient, and refuses to exec uvicorn as root if
# it cannot. sandbox.split_uid_active() then detects the capability and wraps each game in
# fun-runas; with no capability it does not, and the rail behaves as it always has.
USER funuser
WORKDIR /home/funuser

EXPOSE 8730
ENTRYPOINT ["/usr/local/bin/terminal-fun-entrypoint"]
CMD ["uvicorn", "terminal_fun_app.main:app", "--host", "0.0.0.0", "--port", "8730"]
