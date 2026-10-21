# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim@sha256:3ed98443660ff48413b5af19ecefce3b9d50845a901a43420d47cc63a9fddbe9 AS build
WORKDIR /sdk
COPY --from=siwc_sdk /package.json /package-lock.json /tsconfig.base.json /LICENSE /THIRD_PARTY_NOTICES.md ./
COPY --from=siwc_sdk /packages/local ./packages/local
COPY --from=siwc_sdk /scripts ./scripts
COPY --from=siwc_sdk /docs ./docs
RUN npm ci --ignore-scripts --workspace @siwc/local --include-workspace-root && npm run build -w @siwc/local
WORKDIR /app
COPY apps/siwc/package.json apps/siwc/package-lock.json ./
RUN npm ci --ignore-scripts
COPY apps/siwc/tsconfig.json ./
COPY apps/siwc/src ./src
RUN npm run build

FROM node:22-bookworm-slim@sha256:3ed98443660ff48413b5af19ecefce3b9d50845a901a43420d47cc63a9fddbe9
LABEL com.consilith.project="consilith" com.consilith.role="chatgpt-bridge"
RUN apt-get update && apt-get install -y --no-install-recommends dbus-daemon dbus gnome-keyring libsecret-tools ca-certificates gosu && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /sdk /sdk
COPY --from=build /app /app
COPY apps/siwc/container-entrypoint.sh apps/siwc/container-session.sh /app/
RUN sed -i 's/\r$//' /app/container-*.sh && chmod 755 /app/container-*.sh && mkdir /data && chown node:node /data && usermod --home /data node
ENV HOME=/data XDG_DATA_HOME=/data/.local/share XDG_RUNTIME_DIR=/tmp/siwc-runtime CONSILITH_SIWC_HOME=/data/session CONSILITH_SIWC_DEVKIT=/sdk/packages/local/dist/index.js CONSILITH_SIWC_HOST=0.0.0.0 CONSILITH_SIWC_PORT=8730 CONSILITH_SIWC_CALLBACK_PORT=8787 CONSILITH_SIWC_CALLBACK_PROXY_PORT=8788 CONSILITH_SIWC_SECRET_FILE=/run/secrets/siwc-shared-secret
# Only entrypoint secret copying runs as root; gosu drops to node before D-Bus or SDK.
USER root
EXPOSE 8730 8788
ENTRYPOINT ["/app/container-entrypoint.sh"]
CMD ["node", "dist/src/main.js"]
