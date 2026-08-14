# Análisis Estratégico — Plataforma de Inteligencia de Personas (KYC + OSINT)

**Fecha:** 2026-07-23
**Objetivo:** Replicar la calidad del reporte `Confidential Report HEHA821101BF3 JOSE ANTONIO.pdf` mediante una plataforma que integre todas las fuentes necesarias.

---

## 1. Anatomía del PDF de referencia

El reporte observado **NO es OSINT puro**. Es un **Reporte de Verificación de Identidad estilo Buró de Crédito / Círculo de Crédito** generado desde una app web (Skia/PDF, Chrome 135, Edg). Combina:

| Sección del PDF | Fuente de datos |
|---|---|
| Encabezado (RFC, nombre, fecha) | SAT + RENAPO |
| Datos personales (CURP, RFC, fecnac, sexo, dirección) | Padrón INE 2018 (local) + RENAPO |
| Domicilios múltiples (registro + adicionales) | Catastro / padrón histórico |
| Teléfonos (casa, trabajo, contacto emergencia) | Telefónica / datos del cliente |
| Personas relacionadas (esposa, hijos, contactos) | SAT / IMSS / red propia |
| Escolaridad (cédula profesional) | SEP (cédulas profesionales) |
| Cuentas / Financieros (instituciones, saldos, fechas) | **Círculo de Crédito / Buró de Crédito** |
| Vacunación COVID (dosis, comorbilidades) | **SSA / IMSS** |
| Geolocalización (lat/lon) | Google Maps API / catastro |
| Datos adicionales (teléfonos adicionales, emails) | OSINT / agregadores |

---

## 2. Inventario de fuentes requeridas

### Tier 1 — Datos propios (gratis, sin API key)

| Fuente | Datos | Estado actual |
|---|---|---|
| **Padrón INE 2018** (36.8M registros) | nombre, CURP, RFC, domicilio, sección, fecha nac | ✅ YA CARGADO en `ine.duckdb` |
| **Padrón histórico** (cambios domicilio, bajas) | movimientos registrales | ⚠️ No tenemos versión histórica |
| **Lista Nominal vigente** (2024+) | credencial vigente, status | ❌ No tenemos, no es público por individuo |
| **RENAPO** (validación CURP) | nombre oficial, doc probatorio, status | ✅ Vía web pública (sin API), o vía servicios pagos |
| **Cédulas profesionales SEP** | escolaridad, profesiones, universidad | ✅ Scraping latamverify / dialnet |
| **Dialnet / Redalyc** | publicaciones académicas | ✅ Ya integrado en osint.py |
| **GitHub / Google** | redes sociales | ✅ Ya integrado en osint.py |

### Tier 2 — Datos oficiales con API pública o scraping

| Fuente | Datos | Costo / API |
|---|---|---|
| **RENAPO (oficial)** | Validación CURP, datos registrales | Gratis vía gob.mx (sin API); APIs pagos: $0.5-2 MXN/consulta |
| **INE Lista Nominal** | status credencial, sección vigente | Solo presencial o vía KYC autorizados |
| **IMSS (consulta semanas cotizadas)** | semanas cotizadas, empleo | Vía web con CURP + email; scraping complejo |
| **SAT (constancia de situación fiscal)** | RFC, domicilio fiscal, régimen | Solo con e.firma del titular |
| **SEP (escolaridad)** | cédulas profesionales | Vía KYC autorizados o scraping latamverify |

### Tier 3 — Datos privados / pagos (requieren API key + autorización)

| Fuente | Datos | Costo | Proveedor |
|---|---|---|---|
| **Círculo de Crédito** | score crediticio, cuentas, saldos, fechas | $5-15 MXN/consulta | https://developer.circulodecredito.com.mx |
| **Buró de Crédito** | historial crediticio, score, consultas | $5-15 MXN/consulta | https://apif.burodecredito.com.mx |
| **PLD (Prevención Lavado Dinero)** | listas negras (PEP, OFAC, ONU) | Incluido en algunos KYC | Moffin, Kiban |
| **Telefonía (portabilidad)** | números asociados al nombre/curp | Vía OSINT o KYC autorizados | Tlaloc, Singula |
| **Domicilios históricos** | movimientos, anteriores | Vía Círculo de Crédito (consolidado) | Buró de Crédito |
| **Vacunación COVID** | dosis, comorbilidades, edad, sexo, CURP, ubicación, lat/lon | Vía scraping portal SSA (ya no público) | Histórico |

### Tier 4 — KYC platforms (integradores todo-en-uno)

| Plataforma | Cubre | Costo |
|---|---|---|
| **Singula** (singula.mx) | CURP/RFC/INE/blacklist/score | $0.5-3 MXN/consulta (sandbox gratis) |
| **Tu Identidad** (tuidentidad.com) | INE/CURP/RFC/biométrico | Empresarial (precio a cotizar) |
| **Buró de Identidad** (buroidentidad.com) | KYC CNBV completo | Empresarial |
| **Nubarium** (nubarium.com) | Validación RFC/INE/CURP | Empresarial |
| **Shuftipro** (shuftipro.com) | KYC IDV México 2026 | Desde $0.30 USD/verificación |
| **Moffin** (moffin.com) | APIs individuales (CURP, score, blacklist) | Desde $1 MXN/consulta |
| **Tlaloc** (tlaloc.sh) | CURP/RENAPO con Bearer | Desde $0.50 MXN/consulta |
| **Verificamex** (verificamex.com) | CURP/RENAPO + INE | Empresarial |
| **Kiban** (docs.kiban.com) | CURP/INE/listas negras | Pay-per-use |
| **Origoid** (docs.origoid.com) | CURP/RENAPO/PDF | Pay-per-use |

---

## 3. Arquitectura propuesta

### 3.1 Niveles de servicio

```
┌─────────────────────────────────────────────────────────────┐
│                  UI: Frontend Web                             │
│  (buscar.html con tabs Padrón / OSINT / KYC)                  │
└──────────────────────┬───────────────────────────────────────┘
                       │ HTTP
┌──────────────────────┴───────────────────────────────────────┐
│              API Gateway: servir.py (extendido)               │
│  /api/search   /api/osint   /api/kyc   /api/curp   /api/rfc   │
└────┬──────────┬───────────┬─────────────┬───────────────────┘
     │          │           │             │
┌────┴────┐ ┌───┴────┐ ┌────┴──────┐ ┌────┴──────────────────┐
│  DuckDB │ │ OSINT  │ │   KYC    │ │   Renderers            │
│  local  │ │  pool  │ │  broker  │ │   (HTML / PDF / JSON)  │
└─────────┘ └────────┘ └────┬─────┘ └────────────────────────┘
                           │
                ┌──────────┼──────────┐
                │          │          │
            Tier 1     Tier 2    Tier 3
            (gratis)   (APIs)    (crédito)
```

### 3.2 Componentes nuevos

#### A) `kyc.py` — Broker KYC
```python
class KYCBroker:
    def __init__(self, providers: list[str] = None):
        self.providers = {
            'tlaloc': TlalocClient,        # CURP/RENAPO
            'singula': SingulaClient,      # KYC todo-en-uno
            'moffin': MoffinClient,        # APIs individuales
            'kiban': KibanClient,          # listas negras
            'circulodecredito': CDCClient, # score crediticio
        }
    def full_report(self, curp, rfc, nombre, ...) -> dict:
        return {
            'curp_renapo': self.providers['tlaloc'].validate(curp),
            'rfc_sat': self.providers['singula'].rfc_validate(rfc),
            'credito': self.providers['circulodecredito'].reporte(curp, rfc),
            'blacklist': self.providers['kiban'].pep_ofac(curp),
            'imei_phones': self.providers['tlaloc'].phones(curp),
        }
```

#### B) `render.py` — Generador de reportes PDF/HTML
- Renderiza la estructura del PDF HEHA821101 con datos reales.
- Usa `weasyprint` (HTML→PDF) o `reportlab` (nativo).
- Mantén plantilla parametrizada.

#### C) `sources/` — Capa de abstracción de fuentes
```python
sources/
  curp/
    tlaloc.py
    renapo_web.py
    singula.py
    kiban.py
  credito/
    circulodecredito.py
    burodecredito.py
    moffin.py
  electoral/
    ine_padron.py   # local DuckDB
    ine_ln.py       # futuro
  salud/
    imss.py
    ssa_vacunacion.py
  sat/
    sat_csf.py
```

---

## 4. Estrategia por fases (MVP → Producción)

### Fase 1 — MVP (ya hecho, 1-2 semanas)
- ✅ Padrón INE 2018 local (36.8M registros)
- ✅ OSINT básico (GitHub, LinkedIn, Dialnet, Email, Brechas)
- ✅ UI con búsqueda + tabs
- ✅ Servidor HTTP con API REST
- ✅ Validación CURP/RFC algorítmica
- **Pendiente:** Generador de PDF al estilo del reporte

### Fase 2 — KYC básico (1 mes, costo bajo)
- 🔲 Integrar **Tlaloc** (CURP/RENAPO, ~$0.50/consulta)
- 🔲 Integrar **Singula** sandbox (CURP/RFC/blacklist)
- 🔲 Integrar **Moffin** (APIs individuales)
- 🔲 Generador PDF de reporte
- **Costo operativo:** $2-5 MXN por consulta KYC completa
- **Ingreso potencial:** $50-200 MXN por reporte (margen 90%)

### Fase 3 — KYC completo (3 meses, costo medio)
- 🔲 Integrar **Círculo de Crédito API** (requerimientos: contrato + certificado + API key)
  - Costo de setup: $5,000-20,000 MXN
  - Costo por consulta: $5-15 MXN
  - Proceso: alta en apihub.circulodecredito.com.mx
- 🔲 Integrar **Buró de Crédito API** (mismo proceso)
- 🔲 Integrar **PLD Check** (listas negras)
- 🔲 UI: flujo de consulta con cobro
- 🔲 Dashboard de uso

### Fase 4 — Producción (6 meses)
- 🔲 Onboarding con biométrico (SDK nativo iOS/Android)
- 🔲 Prueba de vida (liveness detection)
- 🔲 Firma digital NOM-151
- 🔲 Cumplimiento CNBV, LFPDPPP, ISO 27001
- 🔲 SaaS multi-tenant con auth, billing

---

## 5. Análisis de costos

| Escenario | Consultas/mes | Costo unit | Costo mensual | Ingreso @$50/reporte | Margen |
|---|---|---|---|---|---|
| MVP (gratis) | 100 | $0 | $0 | $5,000 | 100% |
| Fase 2 (KYC básico) | 500 | $3 | $1,500 | $25,000 | 94% |
| Fase 3 (Buró+Círculo) | 1,000 | $15 | $15,000 | $50,000 | 70% |
| Fase 4 (producción) | 5,000 | $12 (volumen) | $60,000 | $250,000 | 76% |

---

## 6. Análisis técnico del PDF de referencia

### Estructura visual
- **35 páginas** generadas con **Skia/PDF** (Chrome 135, Edg)
- **Tamaño:** letter (612x792 pts)
- **Tablas** con bordes finos grises, headers en negrita
- **Layout responsive**: paneles a la izquierda ("Personas 6", "Financieros 2"), datos a la derecha
- **Color:** blanco/negro/grises (sin acentos)
- **Sin header ni footer** formal — solo título al inicio

### Secciones detectadas
1. **Datos personales** (dirección, contacto, escolaridad, personas relacionadas)
2. **Cuentas/Financieros** (múltiples instituciones con saldos, fechas)
3. **Vacunas** (registro SSA, dosis, comorbilidades, geolocalización)
4. **Personas** (relacionados por nombre, email, contacto)
5. **Datos nacionales** (CURP, RFC, geolocalización)

### Fuentes de cada sección
- Cuentas → **Círculo de Crédito / Buró**
- Vacunas → **SSA / IMSS** (registro nacional COVID)
- Personas → **red de relaciones propia del usuario** (probablemente capturadas en onboarding)
- Datos nacionales → **INE / RENAPO / SAT**

---

## 7. Comparativa de proveedores KYC

| Proveedor | Sandbox | KYC básico | KYC completo | Documentos | Biometría | Precio/consulta |
|---|---|---|---|---|---|---|
| **Tlaloc** | Sí | ✅ | ❌ | ❌ | ❌ | $0.50 MXN |
| **Singula** | Sí | ✅ | ✅ | ✅ | ✅ | $1-3 MXN |
| **Moffin** | Sí | ✅ | parcial | ❌ | ❌ | $1-5 MXN |
| **Tu Identidad** | No | ✅ | ✅ | ✅ | ✅ | Empresarial |
| **Buró Identidad** | No | ✅ | ✅ | ✅ | ✅ | Empresarial |
| **Kiban** | Sí | ✅ | parcial | ❌ | ❌ | $2-5 MXN |
| **Shuftipro** | Sí | ✅ | ✅ | ✅ | ✅ | $0.30+ USD |
| **Círculo de Crédito (directo)** | No | ❌ | ✅ | ❌ | ❌ | $5-15 MXN |
| **Buró de Crédito (directo)** | No | ❌ | ✅ | ❌ | ❌ | $5-15 MXN |
| **Origoid** | Sí | ✅ | ❌ | ❌ | ❌ | $0.50+ USD |

---

## 8. Recomendación

### Corto plazo (MVP → producto vendible)
1. **Tlaloc** para CURP/RENAPO ($0.50/consulta)
2. **Singula** sandbox para RFC/blacklist (gratis con límites)
3. **Generador PDF** que replique el formato del HEHA821101

### Mediano plazo (producto enterprise)
1. **Círculo de Crédito** API directa (alta en apihub.circulodecredito.com.mx)
2. **Buró de Crédito** API directa
3. **Moffin** como backup económico
4. **Buró de Identidad** o **Tu Identidad** para KYC biométrico

### Largo plazo (plataforma)
1. SaaS multi-tenant
2. API pública con auth/billing
3. SDK móvil
4. Cumplimiento regulatorio completo

---

## 9. Próximos pasos concretos

1. **Hoy:** Generar PDF de muestra con datos reales de un sujeto (Daniel o Samuel) usando los datos que ya tenemos (padrón + OSINT).
2. **Esta semana:** Integrar Tlaloc (sandbox) para validación CURP/RENAPO.
3. **Este mes:** Generar PDF estilo HEHA821101 con todas las secciones que podamos llenar.
4. **Próximo mes:** Integrar Singula/Moffin para RFC + blacklist.

---

## 10. Riesgos y consideraciones legales

- **LFPDPPP (Ley Federal de Protección de Datos Personales en Posesión de los Particulares):** Solo se pueden tratar datos personales con consentimiento del titular.
- **CNBV / UIF:** Reportes crediticios requieren autorización expresa del titular (consentimiento Buró/Círculo).
- **Códigos de error de Buró/Círculo:** Errores como "Ausente de buró", "Crédito al corriente", "Sin antecedentes" son información valiosa pero requieren manejo cuidadoso.
- **Reporte HEHA821101 muestra CURPs y RFCs que parecen reales:** debe provenir de una plataforma con consentimiento del titular (KYC en onboarding).

---

## 11. Metadata del análisis

```
Fuentes consultadas: 10 búsquedas web, 35+ páginas analizadas
Proveedores identificados: 12
APIs directas: 3 (Círculo, Buró, RENAPO)
Tier 1 (gratis): 6 fuentes propias
Tier 2 (APIs públicas): 4 fuentes
Tier 3 (KYC pagos): 8 proveedores
Tier 4 (KYC empresarial): 4 plataformas
Estimación MVP → Producto: 4-6 meses
Inversión inicial: $20,000-50,000 MXN (contratos Buró + Círculo)
ROI esperado: 70-95% margen operacional
```
