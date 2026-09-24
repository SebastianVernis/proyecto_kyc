"""tools/reconciliacion/ — 7 herramientas para reconciliación KYC por capas.

El LLM decide qué capas invocar y en qué orden según la query.
Cada herramienta es determinística (sin LLM interno) y devuelve JSON estructurado.

Capas:
  1. recon_normalize     — parsea query en [paterno, materno, nombre, partículas]
  2. recon_padron_exact  — match exacto en padrón (88.4M)
  3. recon_padron_variants — LIKE por palabra, invertir, fuzzy
  4. recon_federadas     — RFC base → 29 bases federadas
  5. recon_cohabitacion  — padrón.domicilio → CFE cohabitantes
  6. recon_broker        — RENAPO + Singula (gasta créditos)
  7. recon_sintetizar    — dedupe + score compuesto + veredicto
"""
# Importar los 7 módulos — el side-effect del import registra las tools
from . import capa1_normalize  # noqa
from . import capa2_padron_exact  # noqa
from . import capa3_padron_variants  # noqa
from . import capa4_federadas  # noqa
from . import capa5_cohabitacion  # noqa
from . import capa6_broker  # noqa
from . import capa7_sintetizar  # noqa
