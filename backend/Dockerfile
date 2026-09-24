# ── Dockerfile backend KYC ──────────────────────────────────────────────────────
# Multi-stage build para reducir tamaño de imagen.
# Stage 1: builder — instala dependencias del sistema + pip
# Stage 2: runtime — solo los bins necesarios
# ────────────────────────────────────────────────────────────────────────────────

# ═══ Stage 1: builder ═══════════════════════════════════════════════════════════
FROM python:3.13-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libffi-dev libjpeg-dev zlib1g-dev libglib2.0-dev \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-dev \
    libcairo2-dev libharfbuzz-dev libfribidi-dev libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ═══ Stage 2: runtime ═══════════════════════════════════════════════════════════
FROM python:3.13-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo zlib1g libglib2.0-0 \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
    libcairo2 libharfbuzz0b libfribidi0 libxml2 libxslt1.1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copiar dependencias instaladas en builder
COPY --from=builder /install /usr/local

# Directorio de trabajo
WORKDIR /app

# Copiar código del backend
COPY backend/ /app/

# Crear directorio para bases (se monta como volumen en /bases)
RUN mkdir -p /app/bases /bases

# Asegurar permisos de lectura para el usuario kyc
RUN chmod -R a+rX /app

# Puerto del backend
EXPOSE 8765

# Healthcheck (TCP socket — los endpoints API requieren auth)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import socket; s=socket.socket(); s.settimeout(3); s.connect(('localhost',8765)); s.close()"

# No correr como root
RUN useradd -m -s /bin/bash kyc && chown -R kyc:kyc /app
USER kyc

# Arrancar el servidor
CMD ["python", "-u", "servir.py", "--host", "0.0.0.0", "--port", "8765"]
