# =============================================================================
# repowise — multi-stage Docker build (backend + frontend)
# =============================================================================
FROM node:20-alpine AS frontend-builder

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
ENV NEXT_PUBLIC_REPOWISE_API_URL=http://localhost:7337
RUN npm run build

FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends     git     && rm -rf /var/lib/apt/lists/*

COPY --from=frontend-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend-builder /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm

WORKDIR /app

COPY pyproject.toml README.md LICENSE MANIFEST.in ./
COPY packages/core/ packages/core/
COPY packages/cli/ packages/cli/
COPY packages/server/ packages/server/
RUN pip install --no-cache-dir ".[all]"

COPY --from=frontend-builder /app/packages/web/.next/standalone /app/web
COPY --from=frontend-builder /app/packages/web/.next/static /app/web/.next/static
COPY --from=frontend-builder /app/packages/web/public /app/web/public

VOLUME /data

ENV REPOWISE_DB_URL=sqlite+aiosqlite:////data/wiki.db
ENV REPOWISE_EMBEDDER=mock
ENV PORT_BACKEND=7337
ENV PORT_FRONTEND=3000
ENV HOSTNAME=0.0.0.0

EXPOSE 7337 3000

COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

RUN mkdir -p /data && groupadd -r repowise && useradd -r -g repowise -d /app -s /sbin/nologin repowise     && chown -R repowise:repowise /app /data
USER repowise

ENTRYPOINT ["/app/entrypoint.sh"]
