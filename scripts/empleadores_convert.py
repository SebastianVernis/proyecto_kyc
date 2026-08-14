"""Convertir /Descargas/Bases/EMPLEADORES.csv → bases_consolidadas/empleadores/duckdb/."""
import duckdb, os, time
SRC = '/home/sebastianvernis/Descargas/Bases/EMPLEADORES.csv'
DST = '/home/sebastianvernis/Descargas/Bases/bases_consolidadas/empleadores/duckdb/empleadores.duckdb'
import os; os.makedirs(os.path.dirname(DST), exist_ok=True)
if os.path.exists(DST): os.remove(DST)
con = duckdb.connect(DST)
con.execute(f"""
    CREATE TABLE empleadores AS
    SELECT * FROM read_csv('{SRC}',
        delim='|', header=true, quote='"',
        all_varchar=true, sample_size=50000, ignore_errors=true, strict_mode=false)
""")
con.execute('ALTER TABLE empleadores ADD COLUMN rfc_clean VARCHAR')
con.execute('ALTER TABLE empleadores ADD COLUMN rfc_kind VARCHAR')
con.execute(r'''UPDATE empleadores SET rfc_clean = UPPER(REGEXP_REPLACE(REGEXP_REPLACE(COALESCE(rfc,''),'\s+','','g'),'[^A-Z0-9Ñ&]','','i'))''')
con.execute("UPDATE empleadores SET rfc_clean = NULL WHERE rfc_clean = ''")
con.execute("""UPDATE empleadores SET rfc_kind = CASE
    WHEN rfc_clean IS NULL THEN 'NULL'
    WHEN rfc_clean ~ '^[A-ZÑ&]{4}[0-9]{6}[A-Z0-9Ñ&]{3}$' THEN 'PF13'
    WHEN rfc_clean ~ '^[A-ZÑ&]{3}[0-9]{6}[A-Z0-9Ñ&]{3}$' THEN 'PM12'
    WHEN rfc_clean ~ '^[A-ZÑ&]{4}[0-9]{6}[A-Z0-9Ñ&]{2}$' THEN 'PM12'
    WHEN rfc_clean ~ '^[A-ZÑ&]{4}[0-9]{6}$' THEN 'PF10'
    WHEN rfc_clean ~ '^[A-ZÑ&]{3}[0-9]{6}$' THEN 'PM10'
    ELSE 'OTRO' END""")
con.execute('CHECKPOINT')

OUT = '/home/sebastianvernis/Descargas/Bases/bases_consolidadas/empleadores/parquet_full/empleadores_full.parquet'
os.makedirs(os.path.dirname(OUT), exist_ok=True)
con.execute(f"COPY (SELECT * FROM empleadores) TO '{OUT}' (FORMAT PARQUET, COMPRESSION zstd)")
con.close()
print('done')
