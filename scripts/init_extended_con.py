-- init_extended_con.py
-- Contenido completo que va dentro de _init_extended_con() en servir.py.
-- Adaptado para /root/proyecto_kyc/bases/ con los headers normalizados.
-- Las 12 vistas que el backend espera:
--   1.  api.att_persona          (14 cols, RFC-keyed)
--   2.  api.att_persona_full     (18 cols)
--   3.  api.empleadores          (22 cols, RFC-keyed)
--   4.  api.telcel_lineas        (16 cols, RFC-keyed)
--   5.  api.telcel_lineas_full   (48 cols)
--   6.  api.repuve_de_persona    (12 cols, RFC-keyed)
--   7.  api.imss_asegurado       (10 cols, CURP-keyed)
--   8.  api.imss_asegurado_full  (15 cols)
--   9.  api.imss_salud           (17 cols, CURP/RFC-keyed)
--   10. api.imss_salud_full      (59 cols)
--   11. api.cfe_medidor          (CRUD con headers normalizados: division/zona/agencia/etc)
--   12. api.fuentes_por_rfc      (agregado)
-- No expuesto por servir.py pero incluido: api.fotos_indice, api.fotos_resumen

# Placeholder. Este archivo es de referencia; el código real va embebido en
# servir.py::_init_extended_con() entre las líneas 7384-7866.
