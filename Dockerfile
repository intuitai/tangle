FROM ghcr.io/astral-sh/uv:python3.10-alpine AS builder
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --extra server --extra otel
COPY . .

FROM python:3.10-alpine
COPY --from=builder /app /app
WORKDIR /app
EXPOSE 4317 8090
CMD ["/app/.venv/bin/python", "-m", "tangle.cli", "--host", "0.0.0.0", "--port", "8090"]
