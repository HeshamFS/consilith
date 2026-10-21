#!/bin/sh
set -eu
umask 077
if [ "$(id -u)" = 0 ]; then
  # File-backed Compose secrets keep host ownership. Copy into private tmpfs before
  # dropping privileges, rather than widening permissions on the host's secret files.
  if ! awk '$2 == "/run/consilith-secrets" && $3 == "tmpfs" { found=1 } END { exit !found }' /proc/mounts; then
    echo 'consilith-siwc: /run/consilith-secrets must be a private tmpfs mount' >&2
    exit 1
  fi
  mkdir -p /run/consilith-secrets
  chmod 700 /run/consilith-secrets
  install -m 600 "${CONSILITH_SIWC_KEYRING_PASSWORD_FILE:-/run/secrets/siwc-keyring-password}" /run/consilith-secrets/keyring-password
  install -m 600 "${CONSILITH_SIWC_SECRET_FILE:-/run/secrets/siwc-shared-secret}" /run/consilith-secrets/shared-secret
  chown node:node /run/consilith-secrets/keyring-password /run/consilith-secrets/shared-secret
  chown node:node /run/consilith-secrets
  export CONSILITH_SIWC_KEYRING_PASSWORD_FILE=/run/consilith-secrets/keyring-password
  export CONSILITH_SIWC_SECRET_FILE=/run/consilith-secrets/shared-secret
  exec gosu node "$0" "$@"
fi
mkdir -p "$HOME" "$XDG_DATA_HOME" "$XDG_RUNTIME_DIR" "$CONSILITH_SIWC_HOME"
chmod 700 "$HOME" "$XDG_DATA_HOME" "$XDG_RUNTIME_DIR" "$CONSILITH_SIWC_HOME"
exec dbus-run-session -- /bin/sh /app/container-session.sh "$@"
