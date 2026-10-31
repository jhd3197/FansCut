# FansCut: UI build stage, then a slim Python image with ffmpeg.
FROM node:25-slim AS ui
WORKDIR /src/ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui ./
RUN npm run build

FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md ./
COPY fanscut ./fanscut
RUN pip install --no-cache-dir --no-deps .
COPY --from=ui /src/ui/dist ./ui/dist
# Fail the build, not the first start, when something is missing.
RUN python -c "import fanscut.server" && ffmpeg -version >/dev/null && ffprobe -version >/dev/null

# Non-root (uid/gid 1000). Bind-mounted /config, /data and /output must be
# writable by uid 1000 on the host, e.g.: mkdir -p config data output && chown 1000:1000 config data output
RUN groupadd --gid 1000 fanscut \
    && useradd --uid 1000 --gid fanscut --no-create-home --shell /usr/sbin/nologin fanscut \
    && mkdir -p /config /data /output /media \
    && chown fanscut:fanscut /config /data /output
USER fanscut
# Relative paths in a missing config.yaml (./data, ./output) then land somewhere writable.
WORKDIR /data

ENV PYTHONUNBUFFERED=1 \
    FANSCUT_CONFIG=/config/config.yaml \
    FANSCUT_UI_DIR=/app/ui/dist
EXPOSE 6464

# No curl in python:slim, so use urllib.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:6464/healthz', timeout=8)" || exit 1

CMD ["python", "-m", "fanscut", "serve", "--host", "0.0.0.0", "--port", "6464"]
