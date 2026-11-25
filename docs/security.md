# Security

FansCut has no accounts. It's a one-user tool for a trusted network. Don't
expose it to the internet; if you need remote access, put it behind a reverse
proxy or VPN that does the authentication.

How to report a problem is in
[SECURITY.md](https://github.com/jhd3197/FansCut/blob/dev/SECURITY.md).

## What guards the API

- **Host check.** By default FansCut binds to `127.0.0.1` and only answers to
  loopback host names, so a web page cannot DNS-rebind its own domain onto
  `127.0.0.1` and drive the API. Add other names to `server.allowed_hosts`.
- **Cross-site write refusal.** A state-changing request a browser marks as
  coming from another site (`Sec-Fetch-Site` / `Origin`) is refused, so a page
  you visit cannot POST to your FansCut. Tools that send no `Origin` (curl,
  Sonarr) are unaffected.
- **API key.** Generated on first start (`server.api_key`). Webhooks always
  need it. With `server.auth: api_key`, every `/api` call (and the UI) needs it
  too: as an `X-Api-Key` header, or the cookie that `POST /api/auth/login`
  sets. On a shared network, turn this on.
- **Viewer key.** `server.viewer_key` is a second key that may only read. Any
  write made with it is refused.
- **Rate limits.** Per-client limits on the endpoints that start real work or
  check keys: login and key regeneration, installing a community cut, the
  library check, previews, Plex sign-in and the connection tests on the
  Settings page.
- **Fixed programs.** The settings API never changes which programs FansCut
  runs (`ffmpeg.binary`, `ffmpeg.probe_binary`, `render.effectcraft_cli`).
  Those are set in `config.yaml` only.

Behind a reverse proxy, list the proxy in `server.trusted_proxies` so client
addresses (and the rate limits) are read from `X-Forwarded-For`.
[Docker](docker.md#network-and-access) has more on proxies.

## Secrets

- Secrets (Sonarr API key, Plex token, AI key) can come from `config.yaml` or
  from `FANSCUT_*` environment variables (or `*_FILE` files, so Docker/Compose
  secrets and secret managers work). Values from the environment are never
  written to `config.yaml`. See [Secrets](configuration.md#secrets).
- `config.yaml` is saved with mode 0600. Keep it out of backups and repos you
  share.
- The settings API masks secrets, and they are not logged or put in error
  messages. If you find a place where one leaks, that's a bug worth reporting.

## Your files

The library is read-only to FansCut; cuts are written to the output folder
only. It never writes next to your originals.
