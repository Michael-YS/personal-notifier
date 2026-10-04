FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev && useradd --uid 10001 --create-home notify && mkdir /app/data && chown notify:notify /app/data
COPY server ./server
USER notify
EXPOSE 8000
CMD [".venv/bin/uvicorn", "server.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
