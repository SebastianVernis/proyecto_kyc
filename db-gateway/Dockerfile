# ── Dockerfile db-gateway ──────────────────────────────────────────────────────
# Gateway ligero: solo DuckDB + FastAPI + uvicorn.
# Sin dependencias de PDF/weasyprint (eso queda en el backend).
# ────────────────────────────────────────────────────────────────────────────────

FROM python:3.13-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY db-gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY db-gateway/app/ /app/app/

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import socket; s=socket.socket(); s.settimeout(3); s.connect(('localhost',8001)); s.close()"

RUN useradd -m -s /bin/bash gateway && chown -R gateway:gateway /app
USER gateway

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
