# syntax=docker/dockerfile:1
#
# Bryan phone web app — one container per tester, behind the reverse proxy.
#
# Build (context = this directory). NEXT_BASE_PATH is baked in by Next, so a
# tester mounted at /t/NAME/app needs an image built with that path; the deps
# layer is shared across testers, only `next build` re-runs (~1 min):
#
#   docker build -t hackrice-phone:alice --build-arg NEXT_BASE_PATH=/t/alice/app phone/
#
# Run (everything else is runtime env, no rebuild):
#
#   docker run -d --name phone-alice -p 3000:3000 \
#     -e ACCESS_TOKEN=<alice's token> \   # gates the pages; unset = open (dev)
#     -e BACKEND_URL=http://backend-alice:8010 \   # informational: the browser, not
#     hackrice-phone:alice                         # this server, calls the backend
#
# The VC opens https://DOMAIN/t/alice/app/?token=<alice's token>.
# Do not set NEXT_PUBLIC_API_BASE / NEXT_PUBLIC_API_TOKEN / NEXT_PUBLIC_FIXTURES
# here: unset, the browser derives the backend from its own URL
# (https://DOMAIN/t/alice) and takes the token from the link — src/lib/runtime.ts.

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
WORKDIR /app
ARG NEXT_BASE_PATH=""
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    NEXT_BASE_PATH=${NEXT_BASE_PATH} \
    HOSTNAME=0.0.0.0 \
    PORT=3000
# Standalone server (server.js + traced node_modules), its static assets, public/.
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD wget -qO- "http://127.0.0.1:${PORT}${NEXT_BASE_PATH}/healthz" >/dev/null || exit 1
CMD ["node", "server.js"]
