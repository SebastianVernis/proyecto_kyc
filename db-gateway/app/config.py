"""Configuración del DB-gateway desde variables de entorno."""
import os
from pathlib import Path

BASES_DIR = Path(os.getenv("BASES_DIR", "/bases"))
GATEWAY_PORT = int(os.getenv("GATEWAY_PORT", "8001"))
GATEWAY_SHARED_SECRET = os.getenv("GATEWAY_SHARED_SECRET", "")

# Bases que se precalientan al arrancar (las 8 más grandes)
PREWARM_DBS = [
    "padron_v1.duckdb",
    "cfe_v1.duckdb",
    "imss_asegurados_v1.duckdb",
    "imss_segmentacion_v1.duckdb",
    "telcel_v1.duckdb",
    "telcel_v3.duckdb",
    "att_v1.duckdb",
    "empleadores_v1.duckdb",
]

# Timeout para queries (segundos)
QUERY_TIMEOUT = int(os.getenv("QUERY_TIMEOUT", "10"))
