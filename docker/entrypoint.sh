#!/bin/bash
set -e

# Privilege drop. The container starts as root so we can fix ownership of the
# mounted /data volume (which may pre-date this image and be owned by a stale
# uid), then re-exec ourselves as the unprivileged PUID:PGID — defaulting to
# nobody:users (99:100) to match Unraid. /git is left untouched: it's the
# user's bind-mounted repos, already owned correctly on the host.
if [ "$(id -u)" = "0" ]; then
  PUID="${PUID:-99}"
  PGID="${PGID:-100}"
  echo "Setting ownership of /data to ${PUID}:${PGID} and dropping privileges..."
  chown -R "${PUID}:${PGID}" /data 2>/dev/null || true
  chown "${PUID}:${PGID}" /app 2>/dev/null || true
  exec gosu "${PUID}:${PGID}" "$0" "$@"
fi

# We now run as the unprivileged user. Pin HOME explicitly: this uid has no
# /etc/passwd entry, so HOME would otherwise resolve to "/" (unwritable), which
# breaks anything that writes under $HOME (opencode auth, library caches).
export HOME=/app

# Configure opencode CLI auth non-interactively. opencode stores credentials
# in $HOME/.local/share/opencode/auth.json; in a container there is no
# /connect flow, so materialise the file from the OPENCODE_GO_API_KEY env var.
# Best-effort: a failure here must never take down the whole container.
if [ -n "${OPENCODE_GO_API_KEY:-}" ]; then
  OC_AUTH_DIR="${HOME}/.local/share/opencode"
  if mkdir -p "$OC_AUTH_DIR" 2>/dev/null; then
    printf '{"opencode-go":{"key":"%s","type":"api"}}' "$OPENCODE_GO_API_KEY" > "$OC_AUTH_DIR/auth.json"
    chmod 600 "$OC_AUTH_DIR/auth.json"
  else
    echo "WARNING: could not create ${OC_AUTH_DIR}; skipping opencode auth setup" >&2
  fi
fi

# Start the FastAPI backend
echo "Starting repowise API server on port ${PORT_BACKEND}..."
uvicorn repowise.server.app:create_app \
  --factory \
  --host 0.0.0.0 \
  --port "${PORT_BACKEND}" &

# Start the Next.js frontend
echo "Starting repowise Web UI on port ${PORT_FRONTEND}..."
cd /app/web/packages/web
REPOWISE_API_URL="http://localhost:${PORT_BACKEND}" \
HOSTNAME="0.0.0.0" \
PORT="${PORT_FRONTEND}" \
  node server.js &

# Wait for either process to exit
wait -n
exit $?
