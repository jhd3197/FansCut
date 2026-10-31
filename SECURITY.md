# Security

## Reporting a problem

Please report vulnerabilities privately, not in a public issue. Use GitHub's
private vulnerability reporting: on the repo go to Security, then
"Report a vulnerability" (Security advisories). I'll answer when I can; this is a
solo side project, so there's no SLA.

## What FansCut does and doesn't protect

- **There is no authentication, by design.** It's a one-user tool for a trusted
  network. Do not expose it to the internet. If you need remote access, put it
  behind a reverse proxy or VPN that does the authentication.
- **Secrets** (Sonarr API key, Plex token) can come from `config.yaml` or from
  `FANSCUT_*` environment variables (or `*_FILE` files, so Docker/Compose secrets
  and secret managers work). Values from the environment are never written to
  `config.yaml`.
- `config.yaml` is saved with mode 0600. Keep it out of backups and repos you
  share.
- The settings API masks secrets, and they are not logged or put in error
  messages. If you find a place where one leaks, that's a bug worth reporting.
- The library is read-only to FansCut; cuts are written to the output folder only.

See the Secrets section of the README for setup.
