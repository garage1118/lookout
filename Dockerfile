FROM python:3.12-slim AS build

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock* ./
COPY lookout ./lookout
COPY README.md ./

RUN uv sync --no-dev --frozen

FROM python:3.12-slim

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/lookout /app/lookout

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["lookout"]
