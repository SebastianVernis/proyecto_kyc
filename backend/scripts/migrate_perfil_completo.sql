-- migrate_perfil_completo.sql
-- Crea la base perfil_completo.duckdb con la tabla de sujetos consolidados.
--
-- Uso:
--   cd /home/sebastianvernis/proyectos/kyc/proyecto_kyc
--   python3 -c "import duckdb; c=duckdb.connect('bases/perfil_completo.duckdb'); \
--     c.execute(open('backend/scripts/migrate_perfil_completo.sql').read()); c.close()"
--
-- O manualmente:
--   duckdb bases/perfil_completo.duckdb < backend/scripts/migrate_perfil_completo.sql

SET autoinstall_known_extensions=true;
SET autoload_known_extensions=true;

-- =========================================================================
-- 1) Tabla maestra: perfil consolidado por CURP
-- =========================================================================
-- Cada sujeto UNICO es una fila. Estado:
--   'padron_solo'   → INSERT inicial (solo padrón)
--   'checkid_ok'    → CheckID exitoso, RFC/NSS confirmados
--   'checkid_error' → CheckID falló (no se pudo obtener RFC)
--   'completo'      → Todas las bases locales consultadas

CREATE TABLE IF NOT EXISTS perfil_completo (
    -- ──────────────────────────────────────────────────────────────────────
    -- IDENTIDAD PRINCIPAL (siempre presente si hay padrón)
    -- ──────────────────────────────────────────────────────────────────────
    curp VARCHAR PRIMARY KEY,

    -- Confirmado por CheckID (NULL hasta que se pague y se actualice)
    rfc VARCHAR,
    nss VARCHAR,

    -- ──────────────────────────────────────────────────────────────────────
    -- FUENTE 1: Padrón INE
    -- ──────────────────────────────────────────────────────────────────────
    -- Los datos electorales van como JSON (son 20+ columnas por fila)
    padron_data JSON NOT NULL,
    -- Ejemplo contenido:
    -- {
    --   "curp": "MARA900101HDFXXX03",
    --   "nombre": "MARIO ALBERTO",
    --   "paterno": "AGUILAR",
    --   "materno": "CASTILLO",
    --   "fecnac": "01/01/1990",
    --   "sexo": "M",
    --   "calle": "AV INSURGENTES SUR",
    --   "ext": "1673",
    --   "int": "904",
    --   "colonia": "GUADALUPE INN",
    --   "cp": "03100",
    --   "e": "9",  "d": "09",  "m": "014",  "s": "0001",
    --   "clave_elector": "MARA90010109...",  "folio": "...",
    --   "fuente": "padron_2024"
    -- }

    -- ──────────────────────────────────────────────────────────────────────
    -- FUENTE 2: CheckID (API externa — solo cuando usuario paga)
    -- ──────────────────────────────────────────────────────────────────────
    checkid_data JSON,
    -- Ejemplo contenido:
    -- {
    --   "rfc": "MARA900101XXX",
    --   "nss": "12345678901",
    --   "codigo_postal_fiscal": "03100",
    --   "regimen_fiscal": "612",
    --   "situacion_69b": "definitivo",
    --   "resultado_crudo": {...}   -- respuesta completa de CheckID
    -- }
    checkid_fecha TIMESTAMP,
    checkid_ok BOOLEAN,
    -- Si checkid_ok=true → rfc, nss, cp_fiscal están confirmados
    -- Si checkid_ok=false → el sujeto existe en padrón pero CheckID falló

    -- ──────────────────────────────────────────────────────────────────────
    -- FUENTES 3-4: IMSS (por CURP — match exacto)
    -- ──────────────────────────────────────────────────────────────────────
    imss_data JSON,
    -- Ejemplo contenido:
    -- {
    --   "asegurado": [
    --     {
    --       "nss": "12345678901",
    --       "registro_patronal": "Y12345678",
    --       "nombre_patron": "EMPRESA SA",
    --       "sueldo": "15000",
    --       "cp_patron": "03100",
    --       "giro": "Servicios",
    --       "domicilio_patron": "Av Insurgentes Sur 1673"
    --     }
    --   ],
    --   "salud": [
    --     {
    --       "unidad_medica": "UMF-123",
    --       "modalidad": "Familiar",
    --       "telefono": "5512345678",
    --       "celular": "5512345678",
    --       "correo": "mario@example.com",
    --       "hipertension": true,
    --       "diabetes": false
    --     }
    --   ]
    -- }

    -- ──────────────────────────────────────────────────────────────────────
    -- FUENTE 5: ATT (por RFC — 2 pasadas)
    -- ──────────────────────────────────────────────────────────────────────
    att_data JSON,
    -- Ejemplo contenido:
    -- {
    --   "pasada1_exacto": [
    --     {
    --       "rfc": "MARA900101XXX",
    --       "nombre_completo": "MARIO ALBERTO AGUILAR CASTILLO",
    --       "telefono_fijo": "5512345678",
    --       "celular": "5512345678",
    --       "direccion": "Av Insurgentes Sur 1673",
    --       "colonia": "Guadalupe Inn",
    --       "municipio": "Alvaro Obregon",
    --       "estado": "CDMX",
    --       "cp": "03100",
    --       "rfc_kind": "PF13",
    --       "confianza": "alta"
    --     }
    --   ],
    --   "pasada2_fuzzy": [
    --     {
    --       "rfc": "MARA900101",
    --       "nombre_completo": "MARIO ALBERTO AGUILAR CASTILLO",
    --       "telefono": "...",
    --       "rfc_kind": "PF10",
    --       "confianza": "media"
    --     }
    --   ]
    -- }

    -- ──────────────────────────────────────────────────────────────────────
    -- FUENTE 6: Telcel (por RFC — 2 pasadas)
    -- ──────────────────────────────────────────────────────────────────────
    telcel_data JSON,
    -- Ejemplo contenido:
    -- {
    --   "lineas": [
    --     {
    --       "telefono": "5512345678",
    --       "plan_actual": "Telcel 150",
    --       "marca": "TELCEL",
    --       "modelo": "GSM",
    --       "domicilio": "Av Insurgentes Sur 1673",
    --       "colonia": "Guadalupe Inn",
    --       "ciudad": "CDMX",
    --       "rfc_kind": "PF13",
    --       "confianza": "alta"
    --     }
    --   ]
    -- }

    -- ──────────────────────────────────────────────────────────────────────
    -- FUENTE 7: REPUVE (por RFC — 2 pasadas)
    -- ──────────────────────────────────────────────────────────────────────
    repuve_data JSON,
    -- Ejemplo contenido:
    -- {
    --   "vehiculos": [
    --     {
    --       "placa": "ABC123",
    --       "marca": "NISSAN",
    --       "tipo": "SENTRA",
    --       "modelo": "2020",
    --       "color": "BLANCO",
    --       "nombre_propietario": "MARIO ALBERTO AGUILAR CASTILLO",
    --       "rfc_kind": "PF13",
    --       "confianza": "alta"
    --     }
    --   ]
    -- }

    -- ──────────────────────────────────────────────────────────────────────
    -- FUENTE 8: Empleadores (por RFC — 2 pasadas)
    -- ──────────────────────────────────────────────────────────────────────
    empleadores_data JSON,
    -- Ejemplo contenido:
    -- {
    --   "empresas": [
    --     {
    --       "rfc": "EMPRESA123XXX",
    --       "razon_social": "EMPRESA SA DE CV",
    --       "nombre_comercial": "EMPRESA",
    --       "num_empleados": "150",
    --       "dom_calle": "Av Insurgentes Sur",
    --       "dom_colonia": "Guadalupe Inn",
    --       "dom_cp": "03100",
    --       "giro": "Servicios",
    --       "confianza": "alta"
    --     }
    --   ]
    -- }

    -- ──────────────────────────────────────────────────────────────────────
    -- FUENTE 9: ISSSTE (por nombre — fuzzy, puede tener falsos positivos)
    -- ──────────────────────────────────────────────────────────────────────
    issste_data JSON,
    -- Ejemplo contenido:
    -- {
    --   "empleos": [
    --     {
    --       "nombres": "MARIO ALBERTO",
    --       "paterno": "AGUILAR",
    --       "materno": "CASTILLO",
    --       "cargo": "DOCENTE",
    --       "sueldo": "25000",
    --       "ramo": "Educación",
    --       "entidad": "Secretaría de Educación Pública",
    --       "modalidad": "Base",
    --       "sector": "Federal",
    --       "estado": "CDMX",
    --       "confianza": "media"
    --     }
    --   ]
    -- }

    -- ──────────────────────────────────────────────────────────────────────
    -- FUENTE 10: CFE (por nombre/CP/dirección — 3 pasadas fuzzy)
    -- ──────────────────────────────────────────────────────────────────────
    cfe_data JSON,
    -- Ejemplo contenido:
    -- {
    --   "servicios": [
    --     {
    --       "numero_servicio": "137021201613",
    --       "nombre": "MARIA I GARCIA",
    --       "direccion": "PIEDRA TRONADA MZ138 LT16",
    --       "calle_adicional_1": "TROYA",
    --       "calle_adicional_2": "DURAZNO",
    --       "colonia": "2 DE OCTUBRE",
    --       "cp": "14370",
    --       "division": "DN",
    --       "match_tipo": "cp_direccion",      -- cp | direccion | nombre
    --       "confianza": "alta"                 -- alta | media | baja
    --     }
    --   ],
    --   "por_paso": {
    --     "cp_direccion": 2,
    --     "nombre_completo": 1,
    --     "paterno_materno": 0
    --   },
    --   "total_encontrados": 3,
    --   "buscado_por": {
    --     "cp": true,
    --     "direccion": true,
    --     "nombre": true
    --   }
    -- }

    -- ──────────────────────────────────────────────────────────────────────
    -- METADATA Y CONTROL
    -- ──────────────────────────────────────────────────────────────────────
    estado VARCHAR DEFAULT 'padron_solo',
    -- Estados posibles:
    --   'padron_solo'    → Solo INSERT con padrón, falta CheckID
    --   'checkid_ok'     → CheckID exitoso, RFC/NSS disponibles
    --   'checkid_error'  → CheckID falló (sin RFC)
    --   'completo'       → Todas las bases locales consultadas
    --   'parcial'        → Algunas bases consultadas (por errores)

    fuentes_consultadas JSON,
    -- Ejemplo:
    -- {
    --   "padron": true,
    --   "checkid": true,   "checkid_ok": true,
    --   "imss_asegurados": true,  "imss_segmentacion": true,
    --   "att": false,      "telcel": false,
    --   "repuve": false,   "empleadores": false,
    --   "issste": false,   "cfe": false
    -- }

    creditos_consumidos INTEGER DEFAULT 0,
    -- Cada llamada a CheckID suma 3 créditos
    -- Consultas a bases locales no suman (sin costo externo)

    creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    actualizado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    version INTEGER DEFAULT 1
    -- Para optimistic locking en updates concurrentes
);

-- =========================================================================
-- 2) Índices para búsquedas secundarias
-- =========================================================================

CREATE INDEX IF NOT EXISTS idx_perfil_rfc ON perfil_completo(rfc);
CREATE INDEX IF NOT EXISTS idx_perfil_nss ON perfil_completo(nss);
CREATE INDEX IF NOT EXISTS idx_perfil_estado ON perfil_completo(estado);

-- =========================================================================
-- 3) Vista de export rápido
-- =========================================================================
-- Vista para cuando se quiere exportar el perfil completo en formato plano
CREATE OR REPLACE VIEW perfil_completo_flat AS
SELECT
    curp,
    rfc,
    nss,
    -- Padrón (descomponer el JSON)
    json_extract_string(padron_data, '$.nombre')      AS nombre,
    json_extract_string(padron_data, '$.paterno')     AS paterno,
    json_extract_string(padron_data, '$.materno')     AS materno,
    json_extract_string(padron_data, '$.fecnac')      AS fecha_nacimiento,
    json_extract_string(padron_data, '$.sexo')        AS sexo,
    json_extract_string(padron_data, '$.calle')       AS calle,
    json_extract_string(padron_data, '$.ext')         AS num_ext,
    json_extract_string(padron_data, '$.int')         AS num_int,
    json_extract_string(padron_data, '$.colonia')     AS colonia,
    json_extract_string(padron_data, '$.cp')          AS cp_padron,
    json_extract_string(padron_data, '$.clave_elector') AS ine_clave,
    json_extract_string(padron_data, '$.folio')       AS ine_folio,
    -- CheckID
    json_extract_string(checkid_data, '$.codigo_postal_fiscal') AS cp_fiscal,
    json_extract_string(checkid_data, '$.regimen_fiscal')       AS regimen_fiscal,
    json_extract_string(checkid_data, '$.situacion_69b')        AS situacion_69b,
    -- Fuentes (número de registros en cada una)
    json_array_length(imss_data->'asegurado')                    AS imss_trabajos,
    json_array_length(att_data->'pasada1_exacto')                AS att_contactos,
    json_array_length(telcel_data->'lineas')                     AS telcel_lineas_count,
    json_array_length(repuve_data->'vehiculos')                  AS vehiculos_count,
    json_array_length(issste_data->'empleos')                    AS issste_empleos,
    json_array_length(cfe_data->'servicios')                     AS cfe_servicios_count,
    -- Control
    estado,
    creditos_consumidos,
    creado_en,
    actualizado_en
FROM perfil_completo;

-- =========================================================================
-- 4) Validación final
-- =========================================================================
-- Mostrar la estructura creada
SELECT
    'Tabla perfil_completo creada' AS mensaje,
    COUNT(*) AS filas_iniciales
FROM perfil_completo;
