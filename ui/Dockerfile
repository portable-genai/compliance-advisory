# `compliance-advisory`: Compliance Assistant, console image.
#
# The console is mounted under a reverse-proxy sub-path when embedded in the portal
# (`NEXT_PUBLIC_BASE_PATH=/apps/compliance-advisory`), and the base path is a BUILD-time input
# to Next.js: it rewrites every asset URL and the router's own prefix, so it cannot be supplied
# as a runtime environment variable. The same is true of NEXT_PUBLIC_API_BASE, which
# ui/lib/api.ts inlines and ui/lib/csp.mjs must agree with; behind the portal it is the
# same-origin `/apps/compliance-advisory/api`. Both are ARGs for that reason.
#
#   docker build ui \
#     --build-arg NEXT_PUBLIC_BASE_PATH=/apps/compliance-advisory \
#     --build-arg NEXT_PUBLIC_API_BASE=/apps/compliance-advisory/api
FROM node:22.22.0-alpine@sha256:e4bf2a82ad0a4037d28035ae71529873c069b13eb0455466ae0bc13363826e34 AS builder

ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# NO empty defaults and NO `ENV` assignment, and both halves are the fix rather than tidying.
# The console reads these three-state: unset takes the documented default, set-and-empty is a
# REFUSAL. `ARG X=""` plus an unconditional `ENV X=${X}` turned "not passed" into "passed as
# empty", so `docker build ui` with no arguments could not build this image at all, and failed
# with an error about an emptied variable rather than a missing argument. A bare `ARG` reaches
# the build's `RUN` steps as a genuine environment variable when it is passed, and stays UNSET
# when it is not: verified by execution rather than by reading the reference.
ARG NEXT_PUBLIC_BASE_PATH
ARG NEXT_PUBLIC_API_BASE

# The build context is ui/, so only the console's own checks run here; the Python suite needs
# the full checkout and runs in CI instead. `assert-hydratable` is the one that matters most for
# a deployed console: it fails the build if any script tag would reach the browser without the
# CSP nonce, which renders as dead markup rather than an error.
RUN npm run lint \
 && npm run test \
 && npm run build \
 && npm run assert-hydratable

FROM node:22.22.0-alpine@sha256:e4bf2a82ad0a4037d28035ae71529873c069b13eb0455466ae0bc13363826e34 AS runtime

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000

WORKDIR /app

# A digest pin freezes the base image, and therefore also freezes its unpatched packages.
# Reproducible and patched are independent properties; the pin only buys the first.
RUN apk upgrade --no-cache

# No package manager in a serving container. Standalone output ships its own minimal
# node_modules and starts with `node server.js`, and npm bundles its own dependencies, which is
# where a serving container's Node advisories come from.
RUN rm -rf /usr/local/lib/node_modules/npm \
           /usr/local/lib/node_modules/corepack \
           /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
           /opt/yarn-* /usr/local/bin/yarn /usr/local/bin/yarnpkg

RUN addgroup -S compliance -g 10001 \
 && adduser -S compliance -u 10001 -G compliance

COPY --from=builder --chown=compliance:compliance /app/.next/standalone ./
COPY --from=builder --chown=compliance:compliance /app/.next/static ./.next/static
# Standalone output does not copy public/, and the console serves its logo from there.
COPY --from=builder --chown=compliance:compliance /app/public ./public

USER compliance
EXPOSE 3000

CMD ["node", "server.js"]
