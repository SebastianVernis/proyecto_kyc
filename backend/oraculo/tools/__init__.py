"""Tool registry.

Cada tool es una función `(args: dict, ctx: ToolContext) -> dict`.
Se registran en TOOL_REGISTRY con su esquema JSON para el system prompt.
"""
from __future__ import annotations
from typing import Callable, Any
import dataclasses


@dataclasses.dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict          # JSON schema de los args
    fn: Callable[[dict, "ToolContext"], dict]
    cost_hint: str = "low"    # low | medium | high (sirve al LLM para priorizar)


class ToolContext:
    """Estado compartido entre tools dentro de una misma query.

    tenant_id, user_id: identidad
    audit_trail:        lista de tool calls para auditoría
    cache:              dict mutable para compartir resultados intermedios
    """
    def __init__(self, tenant_id: int, user_id: int, query_id: int):
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.query_id = query_id
        self.audit_trail: list[dict] = []
        self.cache: dict = {}

    def record(self, tool: str, args: dict, results_count: int, duration_ms: float, error: str = ""):
        self.audit_trail.append({
            "tool": tool,
            "args": args,
            "results_count": results_count,
            "duration_ms": round(duration_ms, 1),
            "error": error,
        })


TOOL_REGISTRY: dict[str, ToolDef] = {}


def register(tool: ToolDef):
    TOOL_REGISTRY[tool.name] = tool
    return tool


def get_tool_descriptions_for_prompt(only_recon: bool = True) -> str:
    """Renderiza el catálogo de tools en un bloque de texto que va al system prompt.

    Si only_recon=True (default), solo incluye las 7 herramientas de reconciliación
    (recon_*). Las tools core (padron_search, etc.) quedan registradas para uso
    programático pero no se exponen al LLM.
    """
    lines = ["# CATÁLOGO DE TOOLS DISPONIBLES", ""]
    for name, t in TOOL_REGISTRY.items():
        if only_recon and not name.startswith("recon_"):
            continue
        params = ", ".join(t.parameters.get("properties", {}).keys())
        required = t.parameters.get("required", [])
        req_str = " (requeridos: " + ", ".join(required) + ")" if required else ""
        lines.append(f"## {name}{req_str}")
        lines.append(t.description)
        lines.append(f"Args: {params}")
        lines.append(f"Costo aproximado: {t.cost_hint}")
        lines.append("")
    return "\n".join(lines)
