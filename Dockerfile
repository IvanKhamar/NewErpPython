# Build:  docker build -t newerp-python .
# Run:    docker compose up -d   (the api service; it reaches MySQL and Redis by their service names)
# Two stages: uv builds the virtual environment, only the environment goes into the smaller runtime image.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build
# Compile .py to bytecode at build time (faster start); copy files instead of linking from uv's cache;
# use the image's Python instead of downloading one.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
# Dependencies first: this layer stays cached while only the code changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY README.md ./
COPY src ./src
# --no-editable: the package itself is installed into .venv, so the runtime image needs no source tree.
RUN uv sync --locked --no-dev --no-editable

FROM python:3.13-slim-bookworm
RUN useradd --system --create-home newerp
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" NEWERP_ENVIRONMENT=Production
USER newerp
EXPOSE 8000
# The slim image has no curl, so the health check asks /health with Python itself.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
CMD ["uvicorn", "newerp.main:app", "--host", "0.0.0.0", "--port", "8000"]
