# Production image for the backend — used for both the api and worker
# services in docker-compose.prod.yml (same image, different command). Only
# the worker actually launches Chromium (storefront screenshots after a
# publish — see app/screenshot.py), but both services share this one image,
# so the browser + its system libs end up in both.
#
# Pinned to bookworm (Debian 12), NOT the floating `python:3.11-slim` tag —
# that tag moved to trixie (Debian 13) and Playwright's `--with-deps`
# installer doesn't officially support trixie yet (its fallback package
# list references `ttf-ubuntu-font-family`/`ttf-unifont`, which trixie's
# repos don't have under those names — build fails with
# "Package 'ttf-unifont' has no installation candidate"). bookworm is on
# Playwright's supported list, so `--with-deps` works without a fallback.
FROM python:3.11-slim-bookworm

# Postgres client headers aren't needed (asyncpg is a pure C-extension wheel,
# no libpq required at build time), so no extra system packages beyond what
# Playwright's own --with-deps installs below.
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Installed to a fixed, non-root-owned path (not the default ~/.cache under
# whichever user runs this RUN step, which appuser below couldn't read) so
# the worker can actually launch it after USER appuser switches away from root.
ENV PLAYWRIGHT_BROWSERS_PATH=/app/pw-browsers
RUN playwright install --with-deps chromium

COPY app ./app

# Runs as a non-root user — no reason a Python process needs root inside its
# own container.
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
