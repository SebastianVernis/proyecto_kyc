# Analisis de Integracion Biometrica - Padron INE
## Conexion con Plataforma Encuentra

**Fecha**: 2026-09-01
**Alcance**: Costos tecnicos de implementacion (sin desarrollo)

---

## 1. Contexto del Padron Biometrico INE

### 1.1 Datos disponibles por registro

| Campo | Tipo | Tamano estimado |
|-------|------|-----------------|
| Clave de elector | VARCHAR(18) | 18 bytes |
| CURP | VARCHAR(18) | 18 bytes |
| Nombre completo | VARCHAR(200) | ~120 bytes |
| Domicilio | VARCHAR(300) | ~180 bytes |
| Fecha de nacimiento | DATE | 4 bytes |
| Seccion electoral | INT | 4 bytes |
| Fotografia del rostro | JPEG/PNG | ~50-150 KB |
| 10 huellas dactilares | WSQ/BMP | ~30-60 KB c/u (300-600 KB total) |
| Firma | JPEG | ~10-30 KB |

### 1.2 Dimension estimada de la base

| Concepto | Valor |
|----------|-------|
| Registros | ~88.4 millones |
| Tamano total declarado | ~500 GB |
| Rostros (fotos) | ~88.4M imagenes |
| Huellas | ~884M huellas (10 x persona) |
| Formato probable de huellas | WSQ (Wavelet Scalar Quantization) - estandar FBI/NIST |
| Formato probable de rostros | JPEG 500x700px (~80KB promedio) |

### 1.3 Estandares biometricos del INE

- **Huellas**: formato WSQ 3.1 (ISO/IEC 19794-4), resolucion 500 DPI, 8 bits escala de grises.
- **Rostro**: JPEG, resolucion minima 480x640px, frontal, sin accesorios.
- **Template de huellas**: probablemente ANSI/INCITS 378-2004 (minucias) o ISO/IEC 19794-2.
- **Template de rostro**: vector embedding (128-512 dimensiones) o estandar ISO/IEC 19794-5.

---

## 2. Arquitectura de Integracion

### 2.1 Flujo de datos

```
PADRON INE BIOMETRICO (500 GB)
  |
  +---> [1] INGESTA Y NORMALIZACION
  |       - Parsear metadatos (nombre, CURP, domicilio)
  |       - Extraer rostros (JPEG -> embedding vector)
  |       - Extraer huellas (WSQ -> template minucias)
  |       - Indexar por CURP/clave_elector
  |
  +---> [2] INDICE BIOMETRICO
  |       - Vector index para rostros (FAISS/Milvus)
  |       - Template index para huellas (ABIS)
  |       - Metadata index (DuckDB/SQLite)
  |
  +---> [3] SERVICIO DE MATCHING
  |       - API REST: /match_rostro (foto -> persona)
  |       - API REST: /match_huella (huella -> persona)
  |       - API REST: /match_dual (foto + huella -> persona)
  |
  +---> [4] INTEGRACION KYC
          - Nuevo endpoint en servir.py: /api/v1/ine_biometrico/buscar
          - El Oraculo decide cuando activar matching biometrico
          - Resultado se inyecta en el expediente KYC
```

### 2.2 Dos opciones de arquitectura

**Opcion A: Self-hosted (local)**
- Todo corre en el servidor actual o uno dedicado.
- Sin dependencia de APIs externas.
- Costo: hardware + licencias SDK.
- Ventaja: datos nunca salen del servidor.

**Opcion B: Hibrido (cloud para matching, local para storage)**
- Los templates se generan localmente.
- El matching pesado se delega a AWS/Azure/Google.
- Costo: por transaccion + almacenamiento.
- Ventaja: sin necesidad de GPU local.

---

## 3. Opcion A: Self-Hosted (Recomendada para privacidad)

### 3.1 Hardware requerido

#### Servidor de procesamiento (ingesta + indexacion inicial)

| Componente | Especificacion | Justificacion |
|------------|----------------|---------------|
| CPU | AMD EPYC 7763 (64 cores) o Intel Xeon 8380 | Procesamiento paralelo de 88M imagenes |
| RAM | 256 GB ECC | Carga parcial de vectores + templates en memoria |
| GPU | NVIDIA A100 40GB o RTX 4090 24GB | Inferencia de embeddings faciales (FaceNet/ArcFace) |
| Storage | 2 TB NVMe SSD (hot) + 4 TB HDD (cold) | 500GB base + 200GB indices + overhead |
| Red | 10 Gbps | Transferencia interna rapida |

**Costo estimado (servidor dedicado o alquiler mensual):**

| Modalidad | Costo |
|-----------|-------|
| Compra servidor nuevo (equivalente) | $15,000 - $25,000 USD |
| Alquiler mensual (OVH/Hetzner dedicado) | $300 - $600 USD/mes |
| AWS EC2 p3.2xlarge (GPU V100) | ~$3.00/hr = ~$2,160/mes |
| AWS EC2 g5.2xlarge (GPU A10G) | ~$1.00/hr = ~$720/mes |
| Hetzner GPU dedicado (RTX 4090) | ~$250 - $400 USD/mes |

#### Servidor de produccion (matching en tiempo real)

| Componente | Especificacion | Justificacion |
|------------|----------------|---------------|
| CPU | 16-32 cores | Queries concurrentes |
| RAM | 64-128 GB | Indice FAISS/Milvus en memoria |
| GPU | NVIDIA RTX 4070 o T4 | Inferencia facial ~50ms/query |
| Storage | 1 TB NVMe | Base + indices |

**Costo estimado:**

| Modalidad | Costo |
|-----------|-------|
| Compra | $4,000 - $8,000 USD |
| Alquiler mensual | $150 - $300 USD/mes |
| AWS g4dn.xlarge (T4 GPU) | ~$0.53/hr = ~$380/mes |

### 3.2 Software y licencias

#### Reconocimiento facial

| Opcion | Tipo | Costo | Precision (LFW) | Velocidad |
|--------|------|-------|-----------------|-----------|
| **ArcFace (InsightFace)** | Open source | $0 | 99.83% | ~10ms/embedding con GPU |
| **FaceNet (dlib)** | Open source | $0 | 99.63% | ~30ms/embedding |
| **DeepFace** | Open source | $0 | 99.5% | ~50ms/embedding |
| **Neurotechnology MegaMatcher** | Comercial SDK | $5,000 - $25,000 licencia | 99.9%+ | ~5ms/embedding |
| **Cognitec FaceVACS** | Comercial SDK | $10,000 - $50,000 licencia | 99.95%+ | ~3ms/embedding |
| **ROC Face** | Comercial SDK | $8,000 - $30,000 licencia | 99.9%+ | ~5ms/embedding |

**Recomendacion**: ArcFace (open source) para embedding + FAISS para indexacion. Costo: $0 en licencias.

#### Reconocimiento de huellas

| Opcion | Tipo | Costo | Precision (NIST MINEX) | Velocidad |
|--------|------|-------|----------------------|-----------|
| **SourceAFIS** | Open source (Java/.NET) | $0 | ~96% FAR@0.1% FRR | ~100ms/match |
| **Neurotechnology VeriFinger** | Comercial SDK | $3,000 - $15,000 | 99.9%+ MINEX | ~10ms/match |
| **id3 Finger SDK** | Comercial SDK | $5,000 - $20,000 | NIST validado | ~15ms/match |
| **Aware XM** | Comercial SDK | $10,000 - $40,000 | MINEX certificado | ~8ms/match |
| **ROC ABIS** | Comercial ABIS | $20,000 - $100,000 | NIST #1 ranked | ~5ms/match |
| **Neurotechnology MegaMatcher ABIS** | Comercial ABIS | $15,000 - $80,000 | NIST PFT III | ~5ms/match |

**Recomendacion**: VeriFinger de Neurotechnology para matching de huellas. Balance precio/precision. Licencia ~$8,000 USD (server license, 1M templates).

#### Vector database (para embeddings faciales)

| Opcion | Tipo | Costo | Escalabilidad |
|--------|------|-------|---------------|
| **FAISS (Meta)** | Open source | $0 | 88M vectores en ~35GB RAM |
| **Milvus** | Open source | $0 | 88M vectores, distribuido |
| **Qdrant** | Open source / Cloud | $0 local / $0.35/hr cloud | 88M vectores |
| **Pinecone** | Cloud | ~$70/mes por 1M vectores = ~$6,200/mes | Managed |

**Recomendacion**: FAISS (open source). Para 88M vectores de 512 dimensiones en float32:
- Tamano del indice: 88M x 512 x 4 bytes = ~170 GB en RAM
- Con cuantizacion IVF-PQ: ~15-25 GB en RAM
- Tiempo de busqueda: ~5-15ms por query

### 3.3 Proceso de ingesta (batch)

| Paso | Operacion | Tiempo estimado | Recursos |
|------|-----------|-----------------|----------|
| 1 | Carga y parseo de metadatos | 2-4 horas | CPU only |
| 2 | Extraccion de embeddings faciales (88M fotos) | 10-20 dias (GPU A100) | 1 GPU |
| 3 | Extraccion de templates de huellas (884M huellas) | 5-10 dias (CPU 64 cores) | CPU intensive |
| 4 | Construccion indice FAISS (rostros) | 4-8 horas | 256 GB RAM |
| 5 | Construccion indice ABIS (huellas) | 2-4 horas | 64 GB RAM |
| 6 | Verificacion de integridad | 1-2 horas | CPU |
| **Total** | | **15-30 dias** | |

**Costo de procesamiento batch (si se alquila GPU por dias):**
- AWS p3.2xlarge (V100) x 20 dias = ~$1,440 USD
- Hetzner GPU x 20 dias = ~$170-270 USD

### 3.4 Costos operativos mensuales (self-hosted)

| Concepto | Costo mensual |
|----------|---------------|
| Servidor matching (alquiler) | $150 - $300 |
| Electricidad (si on-premise) | $50 - $100 |
| Backup incremental (500GB) | $5 - $25 |
| Dominio/SSL/CDN | $10 - $20 |
| **Total operativo** | **$215 - $445/mes** |

---

## 4. Opcion B: Hibrido (Cloud Matching)

### 4.1 AWS Rekognition

| Operacion | Precio | Para 88M registros |
|-----------|--------|-------------------|
| IndexFace (ingesta) | $0.001/foto | $88,400 (one-time) |
| SearchFacesByImage (query) | $0.001/busqueda | $0.001/query |
| Almacenamiento faces collection | $0.000004/face/mes | $353/mes |

**Problema**: AWS Rekognition NO soporta huellas dactilares. Solo facial.

### 4.2 Azure Face API

| Operacion | Precio | Para 88M registros |
|-----------|--------|-------------------|
| Face Detect | $1.00/1,000 llamadas | N/A (ingesta) |
| Face Identify | $1.00/1,000 llamadas | $1.00/1,000 queries |
| Face Verify | $1.00/1,000 llamadas | $1.00/1,000 queries |
| Free tier | 30K/mes gratis | Insuficiente |

**Problema**: Azure Face tampoco soporta huellas. Solo facial. Ademas, limite de 1M faces por PersonGroup.

### 4.3 Google Cloud Vision

| Operacion | Precio |
|-----------|--------|
| Face Detection | $1.50/1,000 imagenes |
| No tiene Face Search/Identify nativo | Requiere Vertex AI custom |

**Problema**: No tiene servicio de face matching/identification nativo. Requiere construir sobre Vertex AI.

### 4.4 Comparativa cloud vs self-hosted

| Factor | Cloud (AWS/Azure) | Self-hosted |
|--------|-------------------|-------------|
| Costo ingesta 88M rostros | $88,000+ (one-time) | ~$1,500 (GPU rental) |
| Costo mensual matching | $350-1,000/mes | $215-445/mes |
| Soporte huellas | NO | SI (VeriFinger) |
| Privacidad datos | Datos en servidores externos | Datos nunca salen del servidor |
| Latencia | 50-200ms (red) | 5-15ms (local) |
| Dependencia | Vendor lock-in | Independiente |

**Veredicto**: Self-hosted es superior en todos los aspectos para este caso.

---

## 5. Integracion con Plataforma Encuentra

### 5.1 Nuevo modulo: `biometric_matcher.py`

```
servir.py (backend actual)
  +---> /api/v1/ine_biometrico/buscar
  |       Input: foto (JPEG) o huella (WSQ/BMP)
  |       Output: lista de coincidencias con CURP, nombre, score
  |
  +---> /api/v1/ine_biometrico/buscar_por_curp
  |       Input: CURP
  |       Output: foto + huellas del registro
  |
  +---> /api/v1/ine_biometrico/verificar
          Input: foto + CURP
          Output: match/no_match + score de confianza
```

### 5.2 Integracion con el Oraculo

El Oraculo ya decide que herramientas activar. Se agrega:

```python
# En el prompt del Oraculo:
# "Si el sujeto tiene foto disponible, activar recon_biometrico para
#  buscar coincidencias en el padron biometrico INE."
```

### 5.3 Integracion con el expediente KYC

Nueva seccion en el reporte:

```
## 05b. Padron Biometrico INE
- Rostro: [FOTO] - Match: 98.7% confianza
- Huellas: 10/10 coincidencias
- CURP confirmado: XXXXXXXXXXXXXXXXXXX
- Clave de elector: XXXXXXXXXXXXXXXXXXX
```

---

## 6. Resumen de Costos Tecnicos

### 6.1 Inversion inicial (one-time)

| Concepto | Opcion A (Self-hosted) | Opcion B (Cloud) |
|----------|----------------------|------------------|
| Hardware (compra) | $4,000 - $8,000 | $0 |
| Licencia VeriFinger (huellas) | $8,000 - $15,000 | N/A (no soportado) |
| Licencia facial SDK (si comercial) | $0 - $25,000 | N/A |
| Procesamiento batch ingesta | $170 - $1,440 | $88,000+ |
| **Total inicial** | **$12,170 - $49,440** | **$88,000+** |

### 6.2 Costo operativo mensual

| Concepto | Opcion A (Self-hosted) | Opcion B (Cloud) |
|----------|----------------------|------------------|
| Servidor matching | $150 - $300 | Incluido en API |
| Matching queries (1,000/mes) | $0 | $1 - $88 |
| Almacenamiento | $5 - $25 | $350 - $1,000 |
| Backup | $5 - $25 | Incluido |
| **Total mensual** | **$160 - $350** | **$350 - $1,100** |

### 6.3 Escenario recomendado (minimo viable)

| Concepto | Costo |
|----------|-------|
| Servidor Hetzner GPU dedicado (RTX 4090) | $300/mes |
| VeriFinger Standard SDK (1M templates) | $3,500 one-time |
| ArcFace (open source) | $0 |
| FAISS (open source) | $0 |
| Procesamiento batch (20 dias en servidor) | $200 |
| **Total primer ano** | **$3,500 + ($300 x 12) = $7,100** |
| **Total mensual despues del primer ano** | **$300/mes** |

---

## 7. Riesgos y Consideraciones

### 7.1 Legales
- Los datos biometricos del INE son datos sensibles bajo la Ley Federal de Proteccion de Datos Personales (LFPDPPP).
- Requiere consentimiento explicito del titular para tratamiento.
- La transferencia a terceros (cloud) puede requerir evaluacion de impacto privacy.
- Self-hosted minimiza riesgo legal.

### 7.2 Tecnicos
- El formato exacto de los datos del INE puede variar (WSQ vs BMP, JPEG vs PNG).
- Se necesita acceso al formato de template del INE para matching directo.
- Si el INE usa templates propietarios, se requiere re-extraccion con el SDK elegido.
- La calidad de las fotos del padron varia (algunas son escaneadas de credenciales fisicas).

### 7.3 Operativos
- El procesamiento batch de 88M registros toma 2-4 semanas.
- Se necesita UPS y redundancia para el servidor de matching.
- Los indices deben actualizarse cuando el INE publica actualizaciones del padron.

---

## 8. Siguiente paso

1. Verificar el formato exacto de los datos biometricos del INE (WSQ? BMP? JPEG?).
2. Obtener una muestra de 1,000 registros para probar el pipeline.
3. Elegir SDK de huellas (VeriFinger recomendado).
4. Levantar servidor de prueba con ArcFace + FAISS + VeriFinger.
5. Procesar muestra y medir precision/tiempo.
6. Desplegar en produccion.
