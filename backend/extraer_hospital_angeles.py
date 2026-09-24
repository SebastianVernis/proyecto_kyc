"""
extraer_hospital_angeles.py — Extrae texto de los 26,240 PDFs de
HOSPITAL ANGELES.rar (ya extraidos en /tmp/hospital_full/) y parsea
con regex los campos de identidad + metadata clinica.

Output: bases/hospital_angeles_v1.duckdb con tabla main.personas
(esquema comun) + campos extra (n_paciente, fecha_nacimiento, edad,
sexo, medico, cliente_aseguradora, sucursal, archivo_pdf).
"""
import os
import re
import time
import glob
from pathlib import Path
import fitz
import duckdb

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = Path(os.environ.get("HOSPITAL_PDF_DIR", "/tmp/hospital_full/HOSPITAL ANGELES"))
BASES_DIR = ROOT / "bases"

TELCEL_COLS = [
    "cuenta","padre","st_cta","st_cob","cls_crd","tipo","ciclo",
    "fecha_activ","fecha_cancel","fecha_term","plan_actual","telefono",
    "st_tel","motivo","fecha_cel","gsm_ind","marca","modelo",
    "dat_orig","dat_actual","asesor","adendum","plazo","nombre1",
    "nombre2","rfc","domicilio","numero","interior","colonia",
    "ciudad","edo","cp","tel_contacto","esn","imei","iccid",
    "fecha_plan","fecha_eq","tp_rfc","tp_pago","tc","contacto1",
    "contacto2","plan_orig","renaut",
]

PAT_PACIENTE = re.compile(r'Paciente\s*:\s*([A-ZÑÁÉÍÓÚÜ .]+?)\s{2,}')
PAT_NPAC = re.compile(r'N.\s*Paciente\s*:\s*(\d+)')
PAT_FECNAC = re.compile(r'Fec\.\s*Nac\.\s*:\s*(\d{2}/\d{2}/\d{4})')
PAT_EDAD = re.compile(r'Edad\s*:\s*([0-9A-Za-z ]+?)\s{2,}')
PAT_SEXO = re.compile(r'Sexo\s*:\s*([MF])')
PAT_MEDICO = re.compile(r'M.dico\s*:\s*([A-ZÑÁÉÍÓÚÜa-zñáéíóúü\(\)\. ]+?)\n')
PAT_CLIENTE = re.compile(r'Cliente\s*:\s*([A-ZÑÁÉÍÓÚÜ0-9,\.\ ]+?)\n')
PAT_SUCURSAL = re.compile(r'Sucursal\s*:\s*([A-ZÑÁÉÍÓÚÜ ]+?)\n')
PAT_EPISODIO = re.compile(r'Episodio\s*:\s*(\d+)')
PAT_SOLICITUD = re.compile(r'SOLICITUD\s*:\s*(\S+)')


def split_nombre(nombre):
    """El campo 'Paciente' en los PDFs viene como 'APELLIDO_P APELLIDO_M NOMBRE(S)'.
    Para ser compatible con el esquema comun (nombre1=nombres, nombre2=apellidos,
    igual que telcel/att/etc), invertimos: las 2 primeras palabras son apellidos,
    el resto es el/los nombre(s).
    """
    parts = nombre.split()
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    if len(parts) == 2:
        return parts[1], parts[0]
    if len(parts) == 3:
        return parts[2], parts[0] + ' ' + parts[1]
    # 4+: primeras 2 son apellidos, resto es nombre
    return ' '.join(parts[2:]), parts[0] + ' ' + parts[1]


def parse_pdf(path):
    try:
        doc = fitz.open(path)
        text = doc[0].get_text('text', sort=True)
        doc.close()
    except Exception as e:
        return None

    m_pac = PAT_PACIENTE.search(text)
    if not m_pac:
        return None
    paciente = m_pac.group(1).strip()
    n1, n2 = split_nombre(paciente)

    m_npac = PAT_NPAC.search(text)
    m_fecnac = PAT_FECNAC.search(text)
    m_edad = PAT_EDAD.search(text)
    m_sexo = PAT_SEXO.search(text)
    m_medico = PAT_MEDICO.search(text)
    m_cliente = PAT_CLIENTE.search(text)
    m_sucursal = PAT_SUCURSAL.search(text)
    m_episodio = PAT_EPISODIO.search(text)
    m_solicitud = PAT_SOLICITUD.search(text)

    return {
        "nombre1": n1, "nombre2": n2,
        "n_paciente": m_npac.group(1) if m_npac else None,
        "fecha_nacimiento": m_fecnac.group(1) if m_fecnac else None,
        "edad": m_edad.group(1).strip() if m_edad else None,
        "sexo": m_sexo.group(1) if m_sexo else None,
        "medico": m_medico.group(1).strip() if m_medico else None,
        "cliente_aseguradora": m_cliente.group(1).strip() if m_cliente else None,
        "sucursal": m_sucursal.group(1).strip() if m_sucursal else None,
        "episodio": m_episodio.group(1) if m_episodio else None,
        "solicitud": m_solicitud.group(1) if m_solicitud else None,
        "archivo_pdf": Path(path).name,
    }


if __name__ == "__main__":
    out = BASES_DIR / "hospital_angeles_v1.duckdb"
    if out.exists(): out.unlink()
    con = duckdb.connect(str(out))
    con.execute("SET memory_limit='4GB';")

    cols = (', '.join(f'"{c}" VARCHAR' for c in TELCEL_COLS)
            + ', n_paciente VARCHAR, fecha_nacimiento VARCHAR, edad VARCHAR,'
            + ' sexo VARCHAR, medico VARCHAR, cliente_aseguradora VARCHAR,'
            + ' sucursal VARCHAR, episodio VARCHAR, solicitud VARCHAR,'
            + ' archivo_pdf VARCHAR')
    con.execute(f"CREATE TABLE main.personas ({cols})")

    pdfs = sorted(glob.glob(str(PDF_DIR / "*.pdf")))
    print(f"Total PDFs a procesar: {len(pdfs):,}")

    t0 = time.time()
    batch = []
    n_ok = 0
    n_fail = 0
    n_total = len(pdfs)
    insert_sql = f"INSERT INTO main.personas VALUES ({','.join(['?']*(len(TELCEL_COLS)+10))})"

    for i, pdf_path in enumerate(pdfs, 1):
        rec = parse_pdf(pdf_path)
        if rec is None:
            n_fail += 1
            continue
        vals = [None] * len(TELCEL_COLS)
        # mapear nombre1/nombre2 al esquema comun
        idx_n1 = TELCEL_COLS.index("nombre1")
        idx_n2 = TELCEL_COLS.index("nombre2")
        vals[idx_n1] = rec["nombre1"]
        vals[idx_n2] = rec["nombre2"]
        vals.extend([
            rec["n_paciente"], rec["fecha_nacimiento"], rec["edad"],
            rec["sexo"], rec["medico"], rec["cliente_aseguradora"],
            rec["sucursal"], rec["episodio"], rec["solicitud"],
            rec["archivo_pdf"],
        ])
        batch.append(vals)
        n_ok += 1

        if len(batch) >= 2000:
            con.executemany(insert_sql, batch)
            batch = []

        if i % 5000 == 0:
            elapsed = time.time() - t0
            rate = i / elapsed
            eta = (n_total - i) / rate if rate > 0 else 0
            print(f"  {i:,}/{n_total:,} procesados ({n_ok:,} OK, {n_fail:,} fail) "
                  f"— {elapsed:.0f}s transcurridos, ETA {eta:.0f}s")

    if batch:
        con.executemany(insert_sql, batch)

    elapsed = time.time() - t0
    print(f"\nTotal: {n_ok:,} OK, {n_fail:,} fallidos, en {elapsed:.0f}s")

    n_total_db = con.execute("SELECT COUNT(*) FROM main.personas").fetchone()[0]
    n_fecnac = con.execute("SELECT COUNT(*) FROM main.personas WHERE fecha_nacimiento IS NOT NULL").fetchone()[0]
    n_nombre = con.execute("SELECT COUNT(*) FROM main.personas WHERE nombre1 IS NOT NULL").fetchone()[0]
    print(f"En BD: {n_total_db:,} filas, {n_nombre:,} con nombre, {n_fecnac:,} con fecha_nacimiento")

    print("Creando índices...")
    con.execute("CREATE INDEX idx_nombre2 ON main.personas(nombre2)")
    con.execute("CREATE INDEX idx_fecnac ON main.personas(fecha_nacimiento)")

    con.execute("VACUUM")
    con.close()
    sz = out.stat().st_size / 1024 / 1024
    print(f"Archivo: {out} ({sz:.0f} MB)")
