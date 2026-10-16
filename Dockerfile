FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# git keeps the vault's history (one commit per change).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first (cached until pyproject.toml changes), then the code.
COPY pyproject.toml README.md ./
RUN mkdir -p src/home_brain && touch src/home_brain/__init__.py \
    && pip install --root-user-action=ignore . \
    && pip uninstall --root-user-action=ignore -y home-brain \
    && rm -rf src
COPY src ./src
RUN pip install --root-user-action=ignore --no-deps .

# UID/GID 1000 by default; override with `user:` in compose to match the NAS share owner.
# /data is sticky world-writable so an overridden UID can still use a fresh data volume.
RUN useradd --uid 1000 --create-home brain \
    && mkdir -p /vault /data \
    && chown brain:brain /vault /data \
    && chmod 1777 /data
USER brain

ENV VAULT_PATH=/vault \
    DATA_PATH=/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["home-brain", "serve", "--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
