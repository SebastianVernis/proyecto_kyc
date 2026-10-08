# `bases/_obsoleto/` — documentos y logs retirados del volumen de bases

El volumen `bases/` estaba usado también como caja de documentos: tenía copias
idénticas de los handoffs y logs del túnel antiguo. Se movieron aquí para que el
volumen sea solo datos.

| Contenido | Qué es |
|---|---|
| `ANALISIS_BUSQUEDAS_LAYOUTS.md` | copia antigua del inventario (la vigente está en la raíz del repo) |
| `HANDOFF_NORMALIZACION.md`, `HANDOFF_PROYECTO.md`, `RESUMEN_PROYECTO.md`, `INVENTARIO_BASES.md` | copias idénticas de los handoffs (vigentes en `docs/handoffs/`) |
| `_logs/` | logs de `rematerializar.sh`, `servir.log`, `tunnel.log` (túnel cloudflared, ya no se usa) |

`bases/AGENTS.md` y `bases/CLAUDE.md` **no** se movieron: son cabeceras que
genera GitNexus dentro del índice de bases.
