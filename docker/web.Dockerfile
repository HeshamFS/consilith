# syntax=docker/dockerfile:1
FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS build
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY apps/web/ ./
RUN npm run build

FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402
WORKDIR /app
COPY --from=build --chown=node:node /app/dist ./public
COPY --chown=node:node docker/gateway.mjs ./gateway.mjs
USER node
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=6 \
  CMD ["node", "--input-type=module", "-e", "const r = await fetch('http://127.0.0.1:8080/health'); if (!r.ok) process.exit(1)"]
CMD ["node", "gateway.mjs"]
