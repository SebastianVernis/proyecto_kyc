# `tests/_obsoleto/` — tests del despliegue antiguo

Estos tests verificaban el modelo de despliegue anterior (unidad systemd,
plantilla `cuartodepazsearch.service.template`, `install-service.sh`,
paths `/root/proyecto_kyc`). Ese modelo ya no existe: el backend corre en
Docker y el frontend en un Cloudflare Worker.

No forman parte de la suite: `unittest discover` no baja a este subdirectorio
(no es un paquete), así que no se ejecutan ni pueden fallar por el cambio de
despliegue. Se conservan como referencia.

| Archivo | Verificaba |
|---|---|
| `test_path_migration.py` | que no quedaran paths viejos y que el template/install-service apuntaran a `/root/proyecto_kyc` |
| `test_unit_and_defaults.py` | el unit systemd activo y los defaults `--db`/`--html` |

**Suite vigente:** `tests/*.py` → `python -m unittest discover -s tests` (~252 tests).
