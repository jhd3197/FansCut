# syntax=docker/dockerfile:1
# FansCut: UI build stage, then a slim Python image with ffmpeg.
# Base images are pinned by digest so a rebuild of one commit gets the same
# bytes; Dependabot's docker ecosystem proposes digest bumps.
FROM node:25-slim@sha256:81db02c4b671288a03915da9534dbd54f96d0e7c24d80ccc54f5b36b2e684370 AS ui
WORKDIR /src/ui
COPY ui/package.json ui/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY ui ./
RUN npm run build && find dist -name '*.map' -delete

FROM python:3.11-slim@sha256:61e01f72680f86a5279ad510818ab7b2ee9f049822589d4e34e33e044360356c

LABEL org.opencontainers.image.title=fanscut \
      org.opencontainers.image.description="Fan cuts of the movies and shows you own, built locally" \
      org.opencontainers.image.source=https://github.com/jhd3197/FansCut \
      org.opencontainers.image.licenses=MIT

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements.lock pins every package (and its hashes) that requirements.txt
# pulls in, so two builds of one commit install the same bytes.
COPY requirements.lock .
RUN --mount=type=cache,target=/root/.cache/pip pip install --require-hashes -r requirements.lock

COPY pyproject.toml README.md ./
COPY fanscut ./fanscut
RUN pip install --no-cache-dir --no-deps .
COPY --from=ui /src/ui/dist ./ui/dist
# Fail the build, not the first start, when something is missing.
RUN python -c "import fanscut.server" && ffmpeg -version >/dev/null && ffprobe -version >/dev/null

# Non-root (uid/gid 1000). Bind-mounted /config, /data and /output must be
# writable by uid 1000 on the host, e.g.: mkdir -p config data output && chown 1000:1000 config data output
# Other ids: compose `user: "99:100"`, or PUID/PGID (docs/docker.md).
RUN groupadd --gid 1000 fanscut \
    && useradd --uid 1000 --gid fanscut --no-create-home --shell /usr/sbin/nologin fanscut \
    && mkdir -p /config /data /output /media \
    && chown fanscut:fanscut /config /data /output
# PUID/PGID/UMASK support; see docker-entrypoint.sh and docs/docker.md.
COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
USER fanscut
# Relative paths in a missing config.yaml (./data, ./output) then land somewhere writable.
WORKDIR /data

# The commit the image was built from, shown in /healthz, /api/status and bug reports.
ARG FANSCUT_COMMIT=""
ENV PYTHONUNBUFFERED=1 \
    FANSCUT_CONFIG=/config/config.yaml \
    FANSCUT_UI_DIR=/app/ui/dist \
    FANSCUT_COMMIT=${FANSCUT_COMMIT}
EXPOSE 6464

# No curl in python:slim, so use urllib.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:6464/healthz', timeout=8)" || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "fanscut", "serve", "--host", "0.0.0.0", "--port", "6464"]
