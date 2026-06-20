#!/bin/bash
set -e

# Configure opencode CLI auth non-interactively. opencode stores credentials
# in $HOME/.local/share/opencode/auth.json; in a container there is no
# /connect flow, so materialise the file from the OPENCODE_GO_API_KEY env var.
if [ -n "${OPENCODE_GO_API_KEY:-}" ]; then
  OC_AUTH_DIR="${HOME:-/app}/.local/share/opencode"
  mkdir -p "$OC_AUTH_DIR"
  printf '{"opencode-go":{"key":"%s","type":"api"}}' "$OPENCODE_GO_API_KEY" > "$OC_AUTH_DIR/auth.json"
  chmod 600 "$OC_AUTH_DIR/auth.json"
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
