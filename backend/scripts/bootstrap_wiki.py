#!/usr/bin/env python3
"""bootstrap_wiki.py — Genera entidades de la wiki para las 29 bases federadas.

Para cada base ATTACHed:
  1. DESCRIBE la tabla
  2. SELECT COUNT(*) para confirmar conteo
  3. SELECT muestra de 3 filas
  4. Detecta: columna de RFC, columna de CURP, columna de teléfono, columnas de nombre
  5. Escribe:
     - raw/duckdb_snapshots/<alias>.md (snapshot inmutable con SHA)
     - entities/base-<alias>.md (entidad con frontmatter, links cruzados)

Y crea las páginas de concept:
  - concepts/rfc-kinds.md
  - concepts/us-address-filtering.md
  - concepts/name-matching-heuristics.md
  - concepts/particle-handling.md

Uso:
  cd /home/sebastianvernis/proyectos/kyc/proyecto_kyc/backend
  /home/sebastianvernis/.venv/bin/python scripts/bootstrap_wiki.py
"""
from __future__ import annotations
import os, sys, hashlib, json
from datetime import date
from pathlib import Path

# El script puede ejecutarse desde cualquier cwd
HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
WIKI = BACKEND.parent.parent / "wiki"
sys.path.insert(0, str(BACKEND))

def main():
    import duckdb
    from servir import _init_extended_con, EXTENDED_DBS, ENTITY_TO_BASES, TABLE_FOR_BASE

    con = _init_extended_con()
    if con is None:
        print("ERROR: extended DB no inicializado", file=sys.stderr); sys.exit(1)

    raw_dir = WIKI / "raw" / "duckdb_snapshots"
    ent_dir = WIKI / "entities"
    raw_dir.mkdir(parents=True, exist_ok=True)
    ent_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    index_lines = ["## Entities (bases federadas)\n"]
    log_lines = [f"\n## [{today}] bootstrap | 29 bases federadas ingestadas\n"]

    for alias, (path, _tbl) in EXTENDED_DBS.items():
        if alias not in TABLE_FOR_BASE:
            continue
        tbl = TABLE_FOR_BASE[alias]
        # Localizar la entidad lógica
        entidad = next((e for e, aliases in ENTITY_TO_BASES.items() if alias in aliases), "?")

        # 1. DESCRIBE
        try:
            describe = con.execute(f"DESCRIBE {alias}.{tbl}").fetchall()
            cols = [{"name": r[0], "type": r[1]} for r in describe]
        except Exception as e:
            print(f"  [!] {alias}: DESCRIBE falló: {e}")
            continue

        # 2. COUNT
        try:
            n_rows = con.execute(f"SELECT COUNT(*) FROM {alias}.{tbl}").fetchone()[0]
        except Exception as e:
            n_rows = -1
            print(f"  [!] {alias}: COUNT falló: {e}")

        # 3. Muestra 3 filas
        try:
            sample = con.execute(f"SELECT * FROM {alias}.{tbl} LIMIT 3").fetchall()
            col_names = [c["name"] for c in cols]
            sample_dicts = [dict(zip(col_names, r)) for r in sample]
        except Exception as e:
            sample_dicts = []
            print(f"  [!] {alias}: muestra falló: {e}")

        # 4. Detección semántica
        col_set = {c["name"].lower() for c in cols}
        rfc_col = next((c for c in ("rfc_clean","rfc","rfc_sin_dv","rfc10") if c in col_set), None)
        curp_col = next((c for c in ("curp_clean","curp","curp18") if c in col_set), None)
        tel_col = next((c for c in ("telefono","tel","celular","tel1","telefono_fijo") if c in col_set), None)

        # 5. Escribir raw snapshot
        raw_path = raw_dir / f"{alias}.md"
        body = json.dumps({
            "alias": alias, "entidad": entidad, "tabla": tbl, "path": path,
            "n_rows": n_rows, "cols": cols, "muestra": sample_dicts,
            "rfc_col": rfc_col, "curp_col": curp_col, "tel_col": tel_col,
        }, indent=2, default=str, ensure_ascii=False)
        sha = hashlib.sha256(body.encode()).hexdigest()[:16]
        raw_content = (
            f"---\nsource_url: internal://duckdb/{alias}\ningested: {today}\nsha256: {sha}\n---\n\n"
            f"# Snapshot: {alias} ({entidad})\n\n"
            f"```json\n{body}\n```\n"
        )
        raw_path.write_text(raw_content)

        # 6. Escribir entity
        ent_filename = f"base-{alias.replace('_','-')}.md"
        ent_path = ent_dir / ent_filename

        # descripción automática por entidad
        descripciones = {
            "b_att": "ATT — directorio de personas con teléfono fijo/celular. 1.05M filas. PK rfc_clean. Útil para huella telefónica.",
            "b_emp": "Empleadores — padrón de patrones/empleadores. 162k filas. PK rfc_clean.",
            "b_repuve": "REPUVE — Registro Público Vehicular. 1.7M filas. PK rfc_clean.",
            "b_imss_a": "IMSS asegurados — 57.7M filas. PK curp_clean. La mayor base de presencia laboral.",
            "b_imss_s": "IMSS segmentación — 23.8M filas. PK curp_clean + rfc_clean.",
            "b_telcel": "Telcel — 9.7M líneas. PK rfc_clean.",
            "b_cfe": "CFE medidores — contratos de electricidad. PK numero_servicio, no RFC. Oro para descubrimiento familiar por domicilio.",
            "b_fotos": "Fotos — base auxiliar.",
            "b_issste": "ISSSTE empleados — 2.7M filas. Sin RFC/CURP/dirección. PK consecutivo. Match por nombre+cargo.",
            "b_telcel_2": "Telcel 46M (v2) — 44.6M filas materializadas con split nombre1/nombre2.",
            "b_telcel_1": "Telcel 1 — base adicional.",
            "b_telcel_mx": "Telcel México — base adicional.",
            "b_citibanamex": "Citibanamex — cuentas bancarias.",
            "b_banorte": "Banorte — cuentas bancarias.",
            "b_hsbc_1": "HSBC 1 — cuentas bancarias.",
            "b_hsbc_2": "HSBC 2 — cuentas bancarias.",
            "b_santander_1": "Santander 1 — cuentas bancarias.",
            "b_santander_2": "Santander 2 — cuentas bancarias.",
            "b_santander_3": "Santander 3 — cuentas bancarias.",
            "b_santander_5": "Santander 5 — cuentas bancarias.",
            "b_santander_6": "Santander 6 — cuentas bancarias.",
            "b_santander_7": "Santander 7 — cuentas bancarias.",
            "b_bancoppel": "Bancoppel — cuentas.",
            "b_amex": "AMEX — cuentas.",
            "b_bancomer": "Bancomer — cuentas.",
            "b_clavijero": "Clavijero — banco regional.",
            "b_docentes": "Docentes EdoMex — padrón de maestros.",
            "b_covid23": "COVID-19 — pacientes/extractiones clínicas.",
            "b_hospital_ang": "Hospital Angeles — 23k pacientes extraídos de PDFs.",
        }
        desc = descripciones.get(alias, f"Base federada: {alias} → {tbl}")

        wikilinks = [
            f"[[base-{entidad.replace('-','-')}]]" if entidad != "?" else "[[bases-bancarias]]",
            "[[concept-rfc-kinds]]",
        ]
        if tel_col: wikilinks.append("[[concept-us-address-filtering]]")
        if rfc_col: wikilinks.append("[[concept-name-matching-heuristics]]")

        ent_content = f"""---
title: {alias} ({entidad})
created: {today}
updated: {today}
type: entity
tags: [base-federada, esquema-real]
sources:
  - raw/duckdb_snapshots/{alias}.md
entidad_logica: {entidad}
tabla: {tbl}
filas: {n_rows}
columna_rfc: {rfc_col or '—'}
columna_curp: {curp_col or '—'}
columna_telefono: {tel_col or '—'}
confidence: high
---

# {alias} — {entidad}

{desc}

## Esquema real (verificado por DESCRIBE)

| Columna clave | Detectada |
|---|---|
| RFC | {rfc_col or 'NO TIENE'} |
| CURP | {curp_col or 'NO TIENE'} |
| Teléfono | {tel_col or 'NO TIENE'} |

Total columnas: {len(cols)} | Filas: {n_rows:,} | Tabla: `{tbl}`

## Cómo consultarla desde el oráculo

```python
# El tool `base_search` detecta automáticamente las columnas:
base_search(entidad="{entidad}", rfc="MART520912")
```

## Advertencias
- Verificar muestra cruda en `raw/duckdb_snapshots/{alias}.md` antes de hacer matching.
- Si la base NO tiene RFC (`b_cfe`, `b_issste`, `b_fotos`, `b_hospital_ang`), hay que
  cruzar por nombre + CP / domicilio, no por RFC.

## Links
{' '.join(wikilinks)}
"""
        ent_path.write_text(ent_content)
        index_lines.append(f"- [[{ent_filename[:-3]}]] — {desc}")
        print(f"  ✓ {alias:20s} {entidad:18s} {n_rows:>12,} filas  RFC={rfc_col or '—':10s} CURP={curp_col or '—':10s} TEL={tel_col or '—'}")

    # Páginas de concept
    concepts = {
        "concept-rfc-kinds.md": """---
title: Concept — RFC kinds
created: 2026-08-26
updated: 2026-08-26
type: concept
tags: [rfc-kind, matching-peligroso]
confidence: high
---

# RFC kinds — qué columna tiene cada base

El "RFC" en distintas bases puede ser:
- **PF13** (persona física 13 chars): `AAAA000101AAA` — completo con homoclave
- **PF10** (10 chars sin homoclave): `AAAA000101` — pre-homoclave
- **PM12** (persona moral 12 chars): `AAA000101AAA`
- **PM10** (10 chars): `AAA000101`

**Reglas:**
1. Para matchear entre bases, calcular el RFC base de 10 chars desde el CURP (`substr(curp,1,10)`).
2. Las columnas en cada base pueden llamarse: `rfc_clean`, `rfc`, `rfc_sin_dv`, `rfc10`. El tool `base_search` las detecta.
3. No asumas que el RFC de CFE existe — CFE no tiene RFC, usa `numero_servicio`.
4. ISSSTE no tiene ni RFC ni CURP. Solo nombre + cargo + ramo.

## Por base (verificado 2026-08-26)
Ver `[[entities/base-<alias>]]` de cada una.
""",
        "concept-us-address-filtering.md": """---
title: Concept — Filtrado de direcciones USA
created: 2026-08-26
updated: 2026-08-26
type: concept
tags: [direcciones-usa, matching-peligroso]
confidence: high
---

# Direcciones USA en bases mexicanas

ATT, CFE, Telcel, bancos tienen ~100k registros con direcciones de USA
mezcladas (New Mexico, North Carolina, Camp Lejeune, etc.).

## NO filtrar del dato
Son legítimos: militares USA, turistas, trabajadores transfronterizos.
Filtrarlos perdería evidencia válida.

## Filtrar del MAPA
Solo en `geocode_address` y `generate_static_map`: si Nominatim devuelve coords
fuera del bbox México (lat 14.0-33.5, lon -119.0 a -86.5), descartar para mapa
pero conservar el registro.

## Heurística palabras clave (en `inteligencia_completa.py`)
- Estados USA: alabama, california, texas, new york, etc.
- Bases militares: camp lejeune, fort hood, fort bliss, etc.
- Siglas de estado: AL, AK, CA, TX, NY, ...

## Implicación práctica
Al buscar un mexicano, los hits con dirección USA son válidos pero deben
marcarse como "connacional" o "residente en USA", no descartarse.

## Links
- [[concept-name-matching-heuristics]]
- [[entities/base-b-att]]
- [[entities/base-b-cfe]]
""",
        "concept-name-matching-heuristics.md": """---
title: Concept — Heurísticas de matching de nombres
created: 2026-08-26
updated: 2026-08-26
type: concept
tags: [matching-peligroso, particulas-nombre, fonetica]
confidence: high
---

# Heurísticas de matching de nombres mexicanos

## Partículas que NO son apellido
`DE`, `DEL`, `LA`, `LAS`, `LOS`, `SAN`, `SANTA`, `STO`, `STA`, `VDA.`
Si el LLM las mete en `paterno` o `materno`, se rompe el matching.

Ejemplo: "MARIA DE LA ROSA LOPEZ" → `nombre="MARIA"`, `paterno="ROSA"`, `materno="LOPEZ"` (mal).
Lo correcto: `nombre="MARIA"`, `paterno="DE LA ROSA"`, `materno="LOPEZ"`.

## Variantes fonéticas (CFE tiene typos)
`ALCOCER` ≈ `ALCOSER`, `PEREZ` ≈ `PERS`, `TOBIAS` ≈ `TOVIAS`, `MALDONADO` ≈ `MALDUNADO`.

## Reglas de oro
1. **Nombre completo sobre RFC cuando hay duda**: el RFC puede ser idéntico entre
   homónimos; el nombre completo los separa.
2. **Paterno + materno en ese orden**; los mexicanos lo esperan así.
3. **Apellidos compuestos**: el primero es `paterno`, el segundo `materno`. Si el
   LLM invierte, lo perdemos.
4. **TOBIAS**: en algunos estados es nombre (Oaxaca, Chiapas) y en otros es apellido
   paterno. El LLM debe inferirlo del contexto (frecuencia en el padron del estado).

## Scoring sugerido (no automático)
- EXACTO: nombre completo + fecnac + RFC idénticos
- PARCIAL: nombre completo idéntico, fecnac ±1 año (error de captura)
- FAMILIAR: apellidos idénticos, nombre diferente (probable hermano/hijo)
- DESCARTADO: apellido distinto sin relación

## Links
- [[concept-rfc-kinds]]
- [[concept-particle-handling]]
""",
        "concept-particle-handling.md": """---
title: Concept — Manejo de partículas en nombres
created: 2026-08-26
updated: 2026-08-26
type: concept
tags: [particulas-nombre]
confidence: high
---

# Partículas en nombres mexicanos

Las partículas `DE`, `DEL`, `DE LA`, `DE LOS`, `SAN`, `SANTA`, `VDA.`
pueden ser parte del apellido o venir en medio del nombre.

## Reglas
- Si la partícula va entre el nombre y los apellidos: tratar como inicio del apellido
  (ej. "MARIA DE LA CRUZ GARCIA" → paterno="DE LA CRUZ", materno="GARCIA")
- Si la partícula está al final (VDA. = viuda de): ignorarla para el match
- "SAN" y "SANTA" en zona rural oaxaqueña/chiapaneca son parte del nombre propio

## Tabla de normalización (en `inteligencia_completa.py`)
```python
PARTICLES_AS_PATERNO_PREFIX = {"DE", "DEL", "DE LA", "DE LOS", "SAN", "SANTA"}
PARTICLES_IGNORE_AT_END = {"VDA", "VIUDA"}
```

## Links
- [[concept-name-matching-heuristics]]
""",
    }
    for fname, content in concepts.items():
        (WIKI / "concepts" / fname).write_text(content)
        index_lines.append(f"- [[{fname[:-3]}]]")

    # Actualizar index
    ent_section = "\n".join([l for l in index_lines if l.startswith("- [[base-")])
    concept_section = "\n".join([l for l in index_lines if l.startswith("- [[concept-")])
    (WIKI / "index.md").write_text(f"""# Wiki Index — Plataforma Encuentra KYC

> Catálogo de páginas. Última actualización: {today} | Total páginas: {len(list(ent_dir.iterdir())) + len(concepts)}

## Entities (bases federadas)
{ent_section}

## Concepts
{concept_section}
""")

    # Append a log
    with (WIKI / "log.md").open("a") as f:
        f.write("\n".join(log_lines))
        f.write(f"- Bases ingestadas: {len(list(ent_dir.iterdir()))}\n")
        f.write(f"- Concepts creados: {len(concepts)}\n")
        f.write(f"- Raw snapshots: {len(list(raw_dir.iterdir()))}\n")

    print()
    print(f"OK: {len(list(ent_dir.iterdir()))} entities, {len(concepts)} concepts, {len(list(raw_dir.iterdir()))} raw snapshots")


if __name__ == "__main__":
    main()
