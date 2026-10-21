# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 AS uv
FROM python:3.12-slim@sha256:2259debdc6af85626058a15a7bf44ad6a68b8c98c9ae82a02cf5cdb92f096d47 AS build
COPY --from=uv /uv /uvx /bin/
WORKDIR /app
COPY wheels /wheels
COPY requirements.lock /app/requirements.lock
RUN --mount=type=cache,target=/root/.cache/uv uv venv /app/.venv && \
    uv pip install --python /app/.venv/bin/python --require-hashes \
    --find-links=/wheels -r /app/requirements.lock

FROM python:3.12-slim@sha256:2259debdc6af85626058a15a7bf44ad6a68b8c98c9ae82a02cf5cdb92f096d47
ARG SYSTEM_PACKAGES=""
RUN if [ -n "$SYSTEM_PACKAGES" ]; then apt-get update && \
    apt-get install -y --no-install-recommends $SYSTEM_PACKAGES && rm -rf /var/lib/apt/lists/*; fi
RUN groupadd --gid 10001 consilith && useradd --uid 10001 --gid 10001 --no-create-home consilith && \
    mkdir -p /data /public-keys /gateway-capability && \
    chown consilith:consilith /data /public-keys /gateway-capability
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY container.py /app/container.py
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MPLCONFIGDIR=/tmp/matplotlib
USER 10001:10001
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=12 \
    CMD ["python", "/app/container.py", "health"]
CMD ["python", "/app/container.py", "room"]
