"""
cruzar.py — Cruce multi-base por clave de match en cascada.

Lee los índices materializados (match_index.duckdb + mkidx_<base>.duckdb) y
mide, para un par (A, B), cuántas direcciones de A encuentran gemela en B,
asignando a cada fila de A el nivel de clave MÁS ESTRICTO en que matchea.

Uso:
    python cruzar.py A B            # ej: python cruzar.py cfe padron
    python cruzar.py --list         # bases disponibles en los índices
"""
from __future__ import annotations
import sys, glob, os
import duckdb

INDEX_MAIN = "/root/proyecto_kyc/bases/match_index.duckdb"
MKIDX_GLOB = "/root/proyecto_kyc/bases/mkidx_*.duckdb"

# niveles de clave, de más estricto a más laxo (los cruzables entre bases)
NIVELES = ["k_via_ext", "k_col_via_ext", "k_via_cp", "k_col_via"]


def _conectar():
    """Conecta a un :memory: y expone cada dir_<base> como tabla accesible.

    Los índices grandes viven en mkidx_<base>.duckdb (tabla dir_<base>);
    att/empleadores/repuve viven en match_index.duckdb.
    """
    con = duckdb.connect(":memory:")
    con.execute("SET memory_limit='4GB'; SET threads=4;")
    # permitir derrame a disco en los joins grandes (66M x 88M) en vez de OOM
    con.execute("SET temp_directory='/root/proyecto_kyc/bases/_duckdb_tmp';")
    con.execute("SET preserve_insertion_order=false;")
    bases = {}
    # match_index.duckdb (bases chicas ya materializadas juntas)
    if os.path.exists(INDEX_MAIN):
        con.execute(f"ATTACH '{INDEX_MAIN}' AS ixmain (READ_ONLY)")
        for (t,) in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_catalog='ixmain' AND table_name LIKE 'dir_%'").fetchall():
            bases[t[4:]] = f"ixmain.{t}"
    # mkidx_<base>.duckdb (una base por archivo)
    for path in glob.glob(MKIDX_GLOB):
        base = os.path.basename(path)[len("mkidx_"):-len(".duckdb")]
        alias = f"ix_{base}"
        try:
            con.execute(f"ATTACH '{path}' AS {alias} (READ_ONLY)")
            bases[base] = f"{alias}.dir_{base}"
        except duckdb.IOException:
            # aún se está materializando (lock del escritor) -> se omite
            print(f"  [.] {base}: aún materializando (omitido)", file=sys.stderr)
    return con, bases


def cruzar(con, refs_A: str, refs_B: str, etiqueta_A: str, etiqueta_B: str):
    total = con.execute(f"SELECT COUNT(*) FROM {refs_A}").fetchone()[0]
    # LEFT JOIN por nivel contra las claves distintas de B (hash join spillable).
    # El CASE asigna a cada fila de A el nivel MÁS ESTRICTO con match.
    joins = "\n".join(
        f"        LEFT JOIN (SELECT DISTINCT {lvl} AS k FROM {refs_B} "
        f"WHERE {lvl} IS NOT NULL) e{i} ON e{i}.k = a.{lvl}"
        for i, lvl in enumerate(NIVELES))
    case = "\n".join(
        f"            WHEN a.{lvl} IS NOT NULL AND e{i}.k IS NOT NULL THEN '{lvl}'"
        for i, lvl in enumerate(NIVELES))
    rows = con.execute(f"""
        WITH m AS (
            SELECT CASE
{case}
                ELSE NULL END AS nivel
            FROM {refs_A} a
{joins}
        )
        SELECT COALESCE(nivel,'(sin match)') AS nivel, COUNT(*) n
        FROM m GROUP BY 1 ORDER BY 2 DESC
    """).fetchall()
    print(f"\n=== {etiqueta_A} ({total:,} filas)  →  {etiqueta_B} ===")
    matched = 0
    for nivel, n in rows:
        pct = 100 * n / total
        print(f"  {nivel:<15} {n:>12,}  {pct:5.1f}%")
        if nivel != "(sin match)":
            matched += n
    print(f"  {'TOTAL con match':<15} {matched:>12,}  {100*matched/total:5.1f}%")


def main(argv):
    con, bases = _conectar()
    if not argv or argv[0] == "--list":
        print("Bases disponibles:", ", ".join(sorted(bases)))
        return
    A, B = argv[0], argv[1]
    if A not in bases or B not in bases:
        print(f"Falta índice. Disponibles: {', '.join(sorted(bases))}"); return
    cruzar(con, bases[A], bases[B], A, B)
    con.close()


if __name__ == "__main__":
    main(sys.argv[1:])
