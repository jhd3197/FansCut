#!/bin/sh
# PUID / PGID / UMASK for NAS hosts (Unraid runs as 99:100, Synology as
# 1026:100, ...). The image starts as uid 1000 and this only sets the umask.
# Started as root (user: "0:0" in compose) it moves the fanscut user to
# PUID:PGID, hands it the writable folders, then drops root for good.
set -eu

umask "${UMASK:-022}"

if [ "$(id -u)" = 0 ]; then
  PUID="${PUID:-1000}"
  PGID="${PGID:-1000}"
  [ "$(id -g fanscut)" = "$PGID" ] || groupmod -o -g "$PGID" fanscut
  [ "$(id -u fanscut)" = "$PUID" ] || usermod -o -u "$PUID" fanscut
  for d in /config /data /output; do
    if [ "$(stat -c %u:%g "$d")" != "$PUID:$PGID" ]; then
      # Top folder only: a big render cache is never walked on every start.
      chown "$PUID:$PGID" "$d" || echo "fanscut: cannot chown $d; make it writable by $PUID:$PGID" >&2
    fi
  done
  exec setpriv --reuid="$PUID" --regid="$PGID" --init-groups "$@"
fi

exec "$@"
