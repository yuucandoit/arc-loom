FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY arc_loom /app/arc_loom

RUN pip install --no-cache-dir ".[server]"

EXPOSE 8080

CMD ["uvicorn", "arc_loom.server.app:create_fastapi_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
