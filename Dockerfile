FROM python:3.12-alpine AS build

RUN pip install --no-cache-dir uv==0.11.28

WORKDIR /app
COPY pyproject.toml uv.lock* ./
COPY lookout ./lookout
COPY README.md ./

RUN uv sync --no-dev --frozen

FROM python:3.12-alpine

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/lookout /app/lookout

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Runs as root deliberately: lookout needs read/write access to
# /var/run/docker.sock, whose group ownership/GID varies host-to-host, so a
# fixed non-root UID/GID can't be relied on to have access without extra
# per-host configuration the image can't assume. This is the same tradeoff
# Watchtower and most Docker-socket-mounting tools make.
ENTRYPOINT ["lookout"]
