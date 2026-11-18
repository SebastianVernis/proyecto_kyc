# Motor anterior del oráculo (retirado)

`engine.py` era el ciclo libre de *tool calling*: el modelo decidía qué herramienta llamar y,
cuando contestaba en prosa en vez de invocar una, ese texto se tomaba como veredicto final
(`if not tool_calls: break`). De ahí salieron 71 consultas con `INCIERTO`, 0 candidatos y una
sola iteración, y capas caras consultadas sin identidad confirmada.

| Qué | Por qué se retira |
|---|---|
| `engine.py` | El flujo ahora lo decide el backend en `backend/oraculo/conversacion.py`, por fases (`nombre → desambiguar → confirmar → profundizar → cerrado`), con el estado en el servidor. |
| Endpoint `POST /api/oraculo` | Sustituido por `POST /api/oraculo/chat` (+ `GET /api/oraculo/chat`, `POST /api/oraculo/chat/guardar`). |

Se conserva por historia: `memory.py`, `tools/` y el audit log siguen en uso.
Lo que **no** cambia: `GET /api/oraculo/memoria` y `GET /api/oraculo/audit`, que la barra
lateral del oráculo sigue consultando.
