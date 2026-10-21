#!/bin/sh
set -eu
umask 077
password_file="${CONSILITH_SIWC_KEYRING_PASSWORD_FILE:-/run/secrets/siwc-keyring-password}"
if [ ! -s "$password_file" ]; then
  echo 'consilith-siwc: a nonempty keyring unlock secret file is required' >&2
  exit 1
fi
# gnome-keyring reads its unlock password from stdin. No secret enters arguments or logs.
gnome-keyring-daemon --unlock --components=secrets < "$password_file" >/dev/null
exec "$@"
