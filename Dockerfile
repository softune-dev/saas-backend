# Production image for the backend — used for both the api and worker
# services in docker-compose.prod.yml (same image, different command).
FROM python:3.11-slim

# Postgres client headers aren't needed (asyncpg is a pure C-extension wheel,
# no libpq required at build time), so this stays a plain slim image with no
# extra system packages — smaller image, fewer things to patch.
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Runs as a non-root user — no reason a Python process needs root inside its
# own container.
RUN useradd --create-home appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
