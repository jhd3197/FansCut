## Download

The image is `{IMAGE}:{VERSION}` (amd64 and arm64). `:latest` always points at the newest release.

```bash
docker pull {IMAGE}:{VERSION}
```

### New install with Docker Compose

Download `docker-compose.yml` and `config.docker.example.yaml` from the files below. This compose file is pinned to {VERSION}.

```bash
mkdir -p config data output && chown 1000:1000 config data output
cp config.docker.example.yaml config/config.yaml   # edit it
# edit docker-compose.yml: point the /media volume at your library
docker compose up -d
```

Then open <http://localhost:6464>. For NVIDIA encoding, also download `docker-compose.gpu.yml` and run `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d`.

### Upgrade

```bash
docker compose pull && docker compose up -d
```

Your config, database and finished cuts stay in `config/`, `data/` and `output/`. Back up `data/` before a big jump.

### Check the image

```bash
cosign verify {IMAGE}:{VERSION} \
  --certificate-identity-regexp 'https://github.com/{REPO}/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

`sbom.cdx.json` below lists everything inside the image. More in the [Docker guide](https://github.com/{REPO}/blob/{TAG}/docs/docker.md).

## What changed
