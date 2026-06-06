# syntax=docker/dockerfile:1
# Polymarket BTC UP/DOWN state-pricing bot (PAPER)
#
# Build:   docker build -t polymarket-updown-bot:local .
# Run:     docker run --rm -v $(pwd)/data:/app/data polymarket-updown-bot:local --event-url ... --once
# Compose: docker compose up -d
FROM python:3.12-slim

# Polymarket rounds are anchored to UTC :00/:15/:30/:45 boundaries,
# so the bot MUST run in UTC.
ENV TZ=UTC \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install runtime deps first (better layer caching).
# pyproject.toml declares [tool.setuptools.packages.find] where=["src"],
# so src/ must be present at install time. Copy src/ before pip install.
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# Copy the scripts and state rules (src/ is already in place from above).
COPY scripts/ ./scripts/
COPY config/ ./config/

# Set PYTHONPATH so `python scripts/...` finds the src/ package.
# Override paths to absolute /app/... so they survive the
# PROJECT_ROOT = parents[2] heuristic in config.py (which would
# otherwise point at /usr/local/lib/python3.12/ after pip install).
ENV PYTHONPATH=/app/src \
    STATE_RULES_PATH=/app/config/btc_updown_state_rules_15m.json \
    DATABASE_PATH=/app/data/polymarket_round_paper.sqlite

# Non-root user. /app/data and /app/logs are writable for the bind mounts.
RUN groupadd --system bot \
    && useradd --system --gid bot --home /app --shell /usr/sbin/nologin bot \
    && mkdir -p /app/data /app/logs \
    && chown -R bot:bot /app
USER bot

WORKDIR /app
ENTRYPOINT ["python", "scripts/run_polymarket_round_paper.py"]
CMD ["--timeframe", "15m", "--mode", "paper"]
