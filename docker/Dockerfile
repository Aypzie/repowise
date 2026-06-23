# =============================================================================
# repowise — multi-stage Docker build (backend + frontend)
# =============================================================================
FROM node:20-bookworm-slim AS frontend-builder

WORKDIR /app

COPY package.json package-lock.json ./
COPY packages/types/package.json packages/types/
COPY packages/ui/package.json packages/ui/
COPY packages/web/package.json packages/web/
RUN npm install --production=false

COPY packages/types/ packages/types/
COPY packages/ui/ packages/ui/
COPY packages/web/ packages/web/

ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends     git     curl     wget     ca-certificates     gnupg     gosu     && curl -fsSL https://deb.nodesource.com/setup_20.x | bash -     && apt-get install -y --no-install-recommends nodejs     && rm -rf /var/lib/apt/lists/*

# Pin the opencode version so the installer skips its "latest release" lookup
# against the GitHub API, which is rate-limited (60 req/hr per IP) on shared
# build hosts and intermittently fails the build with "Failed to fetch version
# information". Passing VERSION makes the script download the release directly.
ARG OPENCODE_VERSION=1.17.9
RUN curl -fsSL https://opencode.ai/install | VERSION="${OPENCODE_VERSION}" bash \
    && cp /root/.opencode/bin/opencode /usr/local/bin/opencode \
    && opencode --version

WORKDIR /app

COPY pyproject.toml README.md LICENSE MANIFEST.in ./
COPY packages/core/ packages/core/
COPY packages/cli/ packages/cli/
COPY packages/server/ packages/server/
RUN pip install --no-cache-dir ".[all]"

COPY --from=frontend-builder /app/packages/web/.next/standalone /app/web
COPY --from=frontend-builder /app/packages/web/.next/static /app/web/packages/web/.next/static
COPY --from=frontend-builder /app/packages/web/public /app/web/packages/web/public

VOLUME /data

ENV REPOWISE_DB_URL=sqlite+aiosqlite:////data/wiki.db
ENV REPOWISE_EMBEDDER=mock
ENV PORT_BACKEND=7337
ENV PORT_FRONTEND=3000
ENV HOSTNAME=0.0.0.0

# Run as nobody:users (uid 99 / gid 100) by default — matches Unraid's native
# share ownership so bind-mounted repos under /mnt are writable without host
# chowns. Override with PUID/PGID (LinuxServer-style) for other hosts. HOME is
# pinned so libraries that call Path.home() work for this passwd-less uid.
ENV HOME=/app
ENV PUID=99
ENV PGID=100

EXPOSE 7337 3000

COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Pre-own the app + data dir for the default uid/gid. The entrypoint re-chowns
# /data at startup (as root) to cover an existing persistent volume, then drops
# privileges via gosu — so we intentionally do NOT set a static USER here.
RUN mkdir -p /data && chown -R 99:100 /app /data

ENTRYPOINT ["/app/entrypoint.sh"]
