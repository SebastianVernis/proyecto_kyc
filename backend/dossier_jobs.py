"""dossier_jobs.py — Registro de trabajos de fondo con progreso consultable.

Un dossier completo (motor de inteligencia relacional + mapa familiar +
expediente PDF) tarda minutos: no cabe dentro de una petición HTTP sin
arriesgar el timeout del navegador, del proxy o del Worker. Se modela como
trabajo de fondo:

    POST   → crea el job y devuelve {job_id}  (responde en milisegundos)
    GET …/estado/<job_id>    → {estado, paso, pct, archivos}
    GET …/archivo/<job_id>/<nombre>  → el archivo
    GET …/zip/<job_id>               → todo en un ZIP

Todo lo que produce un job vive en su propio directorio, así que se puede
abrir, copiar o borrar desde fuera sin tocar la base ni el servidor.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import traceback
import uuid
import zipfile
from pathlib import Path

_LOCK = threading.Lock()
_JOBS: dict = {}
_TTL_SEGUNDOS = 6 * 3600          # los jobs terminados se olvidan (y borran) a las 6 h


def dir_base() -> Path:
    """Dónde viven los dossiers.

    En el contenedor /bases está montado en escritura y sobrevive a un rebuild,
    así que es el sitio natural; en local se cae a `<proyecto>/dossiers/`.
    """
    env = os.environ.get("KYC_DOSSIER_DIR")
    if env:
        p = Path(env)
    elif Path("/bases").is_dir() and os.access("/bases", os.W_OK):
        p = Path("/bases/_dossiers")
    else:
        p = Path(__file__).resolve().parent.parent / "dossiers"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def _slug(texto: str, maximo: int = 60) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", texto or "").strip("_")
    return (s[:maximo] or "dossier")


def _limpiar_viejos() -> None:
    ahora = time.time()
    with _LOCK:
        vencidos = [j for j, v in _JOBS.items()
                    if v["estado"] in ("completo", "error")
                    and ahora - v["actualizado"] > _TTL_SEGUNDOS]
        for j in vencidos:
            _JOBS.pop(j, None)


def _run(job_id: str, fn) -> None:
    job = _JOBS[job_id]

    def progreso(paso: str, pct: int | None = None) -> None:
        with _LOCK:
            job["paso"] = str(paso)
            if pct is not None:
                job["pct"] = int(max(0, min(100, pct)))
            job["actualizado"] = time.time()

    try:
        fn(progreso, Path(job["dir"]), job_id)
        with _LOCK:
            job["estado"] = "completo"
            job["pct"] = 100
            job["actualizado"] = time.time()
    except Exception as exc:                                  # noqa: BLE001
        with _LOCK:
            job["estado"] = "error"
            job["error"] = f"{type(exc).__name__}: {exc}"
            job["trace"] = traceback.format_exc()[-1500:]
            job["actualizado"] = time.time()


def crear(titulo: str, fn, *, slug: str = "dossier") -> str:
    """Arranca `fn(progreso, carpeta)` en un hilo y devuelve el id del job.

    `progreso(paso, pct)` publica el avance; `carpeta` es el directorio propio
    del job, el único sitio donde debe escribir.
    """
    _limpiar_viejos()
    job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    carpeta = dir_base() / f"{_slug(slug)}-{job_id}"
    carpeta.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "titulo": titulo,
            "estado": "en_cola",
            "paso": "En cola…",
            "pct": 0,
            "error": None,
            "dir": str(carpeta),
            "creado": time.time(),
            "actualizado": time.time(),
        }
    hilo = threading.Thread(target=_run, args=(job_id, fn), name=f"dossier-{job_id}", daemon=True)
    hilo.start()
    return job_id


def dir_job(job_id: str) -> Path | None:
    job = _JOBS.get(job_id)
    return Path(job["dir"]) if job else None


def estado(job_id: str) -> dict | None:
    job = _JOBS.get(job_id)
    if not job:
        return None
    with _LOCK:
        snap = dict(job)
    archivos = []
    carpeta = Path(snap["dir"])
    if carpeta.is_dir():
        for p in sorted(carpeta.iterdir()):
            if p.is_file():
                archivos.append({
                    "nombre": p.name,
                    "bytes": p.stat().st_size,
                    "mb": round(p.stat().st_size / 1024 / 1024, 2),
                })
    snap["archivos"] = archivos
    snap["elapsed_s"] = round(time.time() - snap["creado"], 1)
    if snap["estado"] != "error":
        snap.pop("trace", None)
    return snap


def ruta_archivo(job_id: str, nombre: str) -> Path | None:
    """Resuelve el archivo pedido DENTRO del directorio del job.

    Sin esta comprobación un `nombre` con `../` leería cualquier cosa del
    sistema: se exige que la ruta resuelta siga colgando de la carpeta del job.
    """
    carpeta = dir_job(job_id)
    if not carpeta:
        return None
    objetivo = (carpeta / nombre).resolve()
    try:
        objetivo.relative_to(carpeta.resolve())
    except ValueError:
        return None
    return objetivo if objetivo.is_file() else None


def empaquetar_zip(job_id: str) -> Path | None:
    """Comprime todo lo que produjo el job (menos el propio ZIP)."""
    carpeta = dir_job(job_id)
    if not carpeta or not carpeta.is_dir():
        return None
    zip_path = carpeta / "dossier_completo.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(carpeta.iterdir()):
            if p.is_file() and p != zip_path:
                z.write(p, p.name)
    return zip_path


def guardar_json(job_id: str, nombre: str, data) -> Path | None:
    carpeta = dir_job(job_id)
    if not carpeta:
        return None
    p = carpeta / nombre
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def borrar(job_id: str) -> bool:
    job = _JOBS.pop(job_id, None)
    if not job:
        return False
    try:
        shutil.rmtree(job["dir"], ignore_errors=True)
    except Exception:
        pass
    return True
