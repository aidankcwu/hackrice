# syntax=docker/dockerfile:1
#
# Bryan dashboard — one container per tester, behind the reverse proxy.
#
# Build (context = this directory). NEXT_BASE_PATH is baked in by Next, so a
# tester mounted at /t/NAME/dashboard needs an image built with that path; the
# deps layer is shared across testers, only `next build` re-runs (~1 min):
#
#   docker build -t bryan-dashboard:alice --build-arg NEXT_BASE_PATH=/t/alice/dashboard dashboard/
#
# Run (everything else is runtime env, no rebuild):
#
#   docker run -d --name dash-alice -p 3000:3000 \
#     -e BACKEND_URL=http://backend-alice:8010 \   # the backend as THIS container reaches it
#     -e ACCESS_TOKEN=<alice's token> \            # sent as X-Access-Token; also gates the pages
#     -v dash-alice-data:/data \                   # adherence / PVT json (optional)
#     bryan-dashboard:alice
#
# The VC opens https://DOMAIN/t/alice/dashboard/?token=<alice's token>.
# Do not set NEXT_PUBLIC_API_BASE here: unset, the browser derives the backend
# from its own URL (https://DOMAIN/t/alice) — see src/lib/runtime.ts.

ARG NODE_VERSION=20

FROM node:${NODE_VERSION}-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:${NODE_VERSION}-alpine AS build
WORKDIR /app
ARG NEXT_BASE_PATH=""
ENV NEXT_BASE_PATH=${NEXT_BASE_PATH} \
    NEXT_OUTPUT=standalone \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN rm -rf .next && npm run build

FROM node:${NODE_VERSION}-alpine AS run
# The healthspan engine (lib/score/brian_score.py) is spawned per request and needs numpy.
RUN apk add --no-cache python3 py3-numpy && python3 -c "import numpy"
WORKDIR /app
ARG NEXT_BASE_PATH=""
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    NEXT_BASE_PATH=${NEXT_BASE_PATH} \
    HOSTNAME=0.0.0.0 \
    PORT=3000 \
    BRIAN_PYTHON=/usr/bin/python3 \
    BRIAN_DATA_DIR=/data
RUN mkdir -p /data && chown node:node /data
# Standalone server (server.js + traced node_modules), its static assets, and the
# python engine, which is spawned by path from process.cwd()/lib/score and so is
# not traced by Next.
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/lib/score ./lib/score
USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD wget -qO- "http://127.0.0.1:${PORT}${NEXT_BASE_PATH}/healthz" >/dev/null || exit 1
CMD ["node", "server.js"]
