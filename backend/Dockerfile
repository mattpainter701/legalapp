FROM python:3.11-slim@sha256:e031123e3d85762b141ad1cbc56452ba69c6e722ebf2f042cc0dc86c47c0d8b3 AS base

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libgl1 \
    libglib2.0-0 \
    fonts-nanum \
    wget \
    && rm -rf /var/lib/apt/lists/*

FROM base AS builder

COPY backend/requirements.txt ./requirements.txt
RUN pip install --upgrade pip==26.1.2 && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM base AS final

# Dependencies installed to /usr/local are world-readable, so the non-root
# user below can still import them.
COPY --from=builder /install /usr/local

# Runtime JWT verification is restricted to HS256 for application tokens and
# RS256 for OAuth identity tokens. Remove python-jose's unused pure-Python ECDSA
# backend (which has no patched release) and the package installer itself from
# the production image to reduce vulnerable/runtime mutation surface.
RUN python -m pip uninstall -y ecdsa pip

COPY backend/ /app/
# The disposable sales-demo fixture imports this reviewed synthetic pack at
# runtime.  Keeping it in the image makes the fixture reproducible and avoids
# coupling a production demo to a host checkout or a customer upload volume.
COPY demo/cybersafeadvisor-corporate-pack/ /app/demo/cybersafeadvisor-corporate-pack/

# ── Non-root runtime user ─────────────────────────────────────────────────────
# Fixed UID/GID 10001 so host-mounted volumes can be chowned to a known owner.
# IMPORTANT (prod): the host dir bind-mounted at /app/uploads
# (e.g. /data/legalapp/uploads) must be writable by UID 10001 — run on the host:
#   sudo chown -R 10001:10001 /data/legalapp/uploads
RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid 10001 --no-create-home appuser \
    && mkdir -p /app/uploads \
    && chown -R 10001:10001 /app

EXPOSE 8000

# Lightweight healthcheck — image has no curl, so use the stdlib via python.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
