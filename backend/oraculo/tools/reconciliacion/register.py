"""register_all — importa las 7 capas. Cada módulo, al importarse, ejecuta
register(ToolDef(...)) como side effect."""
# Importar los 7 — el side-effect del import registra cada tool
from . import capa1_normalize  # noqa: F401
from . import capa2_padron_exact  # noqa: F401
from . import capa3_padron_variants  # noqa: F401
from . import capa4_federadas  # noqa: F401
from . import capa5_cohabitacion  # noqa: F401
from . import capa6_broker  # noqa: F401
from . import capa7_sintetizar  # noqa: F401


def register_all():
    """Idempotente — ya se registró al importar."""
    pass
