# Requisitos Tecnicos - Servidor Biometrico Self-Hosted
## Plataforma Encuentra - Padron INE Biometrico

**Fecha**: 2026-09-01
**Objetivo**: Especificaciones completas para compra de servidor dedicado

---

## 1. Servidor Principal (Procesamiento + Matching)

### 1.1 Opcion A: Workstation GPU (Recomendada)

| Componente | Especificacion minima | Especificacion recomendada | Precio estimado (USD) |
|------------|----------------------|---------------------------|----------------------|
| **CPU** | AMD Ryzen 9 7950X (16C/32T) | AMD EPYC 9354 (32C/64T) | $350 - $900 |
| **RAM** | 128 GB DDR5 ECC | 256 GB DDR5 ECC | $400 - $800 |
| **GPU** | NVIDIA RTX 4070 Ti Super 16GB | NVIDIA RTX 4090 24GB | $800 - $1,600 |
| **SSD OS** | 1 TB NVMe Gen4 | 2 TB NVMe Gen4 | $80 - $150 |
| **SSD Datos** | 2 TB NVMe Gen4 | 4 TB NVMe Gen4 | $150 - $300 |
| **HDD Backup** | 4 TB 7200rpm | 8 TB 7200rpm | $80 - $150 |
| **Mobo** | ASUS Pro WS X670E-ACE | Supermicro H13SSL-N (EPYC) | $400 - $700 |
| **PSU** | 1000W 80+ Platinum | 1200W 80+ Titanium | $200 - $350 |
| **Case** | Full tower con buen airflow | 4U rackmount | $100 - $300 |
| **Cooling** | AIO 360mm | Custom loop o Noctua NH-U14S | $100 - $200 |
| **Red** | 2.5GbE integrada | 10GbE PCIe card | $0 - $150 |
| **UPS** | 1500VA/900W | 2000VA/1200W | $200 - $400 |

**Total Opcion A: $2,860 - $6,000 USD (~$50,000 - $105,000 MXN)**

### 1.2 Opcion B: Servidor Rack (Para datacenter)

| Componente | Especificacion | Precio estimado (USD) |
|------------|----------------|----------------------|
| **Chasis** | Supermicro 4U GPU Server (SYS-421GE-TNRT) | $2,500 - $4,000 |
| **CPU** | 2x AMD EPYC 9354 (32C/64T c/u) | $1,800 |
| **RAM** | 512 GB DDR5 ECC (16x32GB) | $1,200 |
| **GPU** | NVIDIA RTX 4090 24GB | $1,600 |
| **SSD** | 2x 3.84TB NVMe U.2 (RAID 1) | $600 |
| **HDD** | 4x 8TB SAS (RAID 5) | $600 |
| **Red** | 2x 25GbE SFP28 | $300 |
| **PSU** | 2x 1600W redundante | Incluido en chasis |
| **Rail kit** | Supermicro MCP-290-00057-0N | $50 |

**Total Opcion B: $8,650 - $10,150 USD (~$151,000 - $178,000 MXN)**

### 1.3 Opcion C: Servidor Pre-armado (Dell / HPE)

| Modelo | Especificacion | Precio (USD) |
|--------|----------------|-------------|
| **Dell PowerEdge T640** | Xeon 4314, 128GB, RTX 4090, 2TB NVMe | $5,000 - $7,000 |
| **Dell PowerEdge R760xa** | Dual Xeon, 256GB, GPU ready, rack | $8,000 - $12,000 |
| **HPE ProLiant DL380a** | Gen11, Xeon, 256GB, GPU ready | $7,000 - $10,000 |
| **Lenovo ThinkSystem SR670 V2** | GPU optimized, 2x RTX 4090 | $9,000 - $14,000 |

**Nota**: Los servidores pre-armados de Dell/HPE/Lenovo incluyen garantia de 3-5 anos, soporte NBD (next business day) y certificacion de compatibilidad GPU. El precio premium se justifica si el servidor sera critico para operacion.

---

## 2. Almacenamiento

### 2.1 Layout de discos recomendado

```
NVMe 1 (2TB)  ──> OS + Aplicaciones + Indices calientes
NVMe 2 (4TB)  ──> Base de datos biometrica (500GB) + Embeddings + FAISS index
HDD 1 (8TB)   ──> Backup incremental diario
HDD 2 (8TB)   ──> Backup semanal + Archivos historicos
```

### 2.2 Requisitos de IOPS

| Operacion | IOPS requeridos | Tipo de disco |
|-----------|----------------|---------------|
| Query facial (embedding search) | 1,000-5,000 | NVMe |
| Query huella (template match) | 500-2,000 | NVMe |
| Lectura metadata (nombre, CURP) | 100-500 | NVMe o SSD |
| Backup incremental | 50-100 | HDD suficiente |
| Batch ingestion (escritura) | 2,000-10,000 | NVMe |

### 2.3 RAID recomendado

| Capa | RAID | Discos | Justificacion |
|------|------|--------|---------------|
| OS + Apps | RAID 1 | 2x NVMe | Redundancia, sin impacto |
| Datos biometricos | RAID 1 | 2x NVMe | Datos criticos, IOPS altos |
| Backup | RAID 5 | 3x HDD | Capacidad + tolerancia a fallos |

**Nota**: Si se usa una sola GPU y el presupuesto es limitado, se puede simplificar a 1x NVMe 4TB (datos) + 1x HDD 8TB (backup) sin RAID. El backup diario a disco externo o nube compensa.

---

## 3. Red y Conectividad

### 3.1 Red local

| Componente | Especificacion | Precio |
|------------|----------------|--------|
| Switch | 8-port 2.5GbE managed | $80 - $150 |
| Cableado | Cat6a (10Gbps hasta 100m) | $30 - $50 |
| UPS red | Si el servidor ya tiene UPS, compartir | $0 |

### 3.2 Red externa (acceso remoto)

| Opcion | Velocidad | Precio mensual |
|--------|-----------|----------------|
| Tailscale (ya instalado) | Variable | $0 |
| Cloudflare Tunnel (ya configurado) | Variable | $0 |
| IP publica fija (Telmex) | 100-200 Mbps | $200 - $500 MXN/mes |

**Nota**: El servidor biometrico NO necesita acceso directo a internet. Se accede via Tailscale o Cloudflare Tunnel desde la red interna. Esto reduce superficie de ataque.

### 3.3 Firewall

| Regla | Puerto | Protocolo | Accion |
|-------|--------|-----------|--------|
| SSH | 22 | TCP | Allow solo Tailscale |
| API biometrica | 8766 | TCP | Allow solo red interna |
| HTTPS (Tunnel) | 443 | TCP | Allow via Cloudflare |
| Todo lo demas | * | * | DENY |

---

## 4. Sistema Operativo

### 4.1 OS recomendado

| Opcion | Version | Justificacion |
|--------|---------|---------------|
| **Ubuntu Server 24.04 LTS** | 24.04.x | Soporte NVIDIA driver, CUDA 12.x, Docker |
| Debian 12 | 12.x | Alternativa estable, menos paquetes |
| Rocky Linux 9 | 9.x | Si se prefiere ecosistema RHEL |

**Recomendacion**: Ubuntu Server 24.04 LTS. Soporte hasta 2029, drivers NVIDIA actualizados, CUDA 12.4+.

### 4.2 Stack de software

```
OS: Ubuntu 24.04 LTS
GPU Driver: NVIDIA 550+ (produccion)
CUDA: 12.4+
cuDNN: 8.9+
Python: 3.11 o 3.13
Docker: 24.x (opcional, para aislamiento)
PostgreSQL: 16 (metadata + audit log)
Redis: 7.x (cache de queries biometricas)
Nginx: 1.24+ (reverse proxy + TLS)
systemd: para servicios
```

### 4.3 Particiones del disco

```
/dev/nvme0n1 (2TB):
  /              100 GB   ext4    (OS + apps)
  /var/lib/docker 200 GB  ext4    (containers si se usa Docker)
  /opt/biometric  500 GB  ext4    (aplicacion + modelos)
  /tmp            50 GB   ext4    (temporal para batch)
  swap            64 GB   swap    (igual a RAM/4)

/dev/nvme1n1 (4TB):
  /data/biometric 4 TB    ext4    (base de datos + indices)

/dev/sda (8TB HDD):
  /backup         8 TB    ext4    (backups incrementales)
```

---

## 5. GPU - Detalle y Benchmarks

### 5.1 Comparativa de GPUs para biometria

| GPU | VRAM | FP32 TFLOPS | Embeddings faciales | Match huellas | Precio (USD) |
|-----|------|-------------|--------------------|--------------:|-------------|
| RTX 4060 Ti 16GB | 16 GB | 22 | ~20ms/embed | ~50ms | $450 |
| RTX 4070 Ti Super | 16 GB | 44 | ~12ms/embed | ~30ms | $800 |
| RTX 4090 | 24 GB | 83 | ~5ms/embed | ~15ms | $1,600 |
| RTX A6000 | 48 GB | 39 | ~10ms/embed | ~25ms | $4,500 |
| NVIDIA L4 | 24 GB | 30 | ~12ms/embed | ~25ms | $2,000 |
| NVIDIA A100 40GB | 40 GB | 19.5 | ~3ms/embed | ~8ms | $10,000+ |

### 5.2 Benchmarks para 88M registros

| Operacion | RTX 4070 Ti | RTX 4090 | A100 |
|-----------|-------------|----------|------|
| Extraccion embeddings (88M fotos) | ~12 dias | ~6 dias | ~3 dias |
| Index FAISS (88M vectores) | ~4h | ~2h | ~1h |
| Query facial (1:N search) | ~15ms | ~5ms | ~3ms |
| Throughput queries/min | ~4,000 | ~12,000 | ~20,000 |

### 5.3 Memoria GPU necesaria

| Componente | VRAM requerida |
|------------|---------------|
| Modelo ArcFace (ResNet-100) | ~350 MB |
| FAISS index (88M x 512d, IVF-PQ) | ~4-8 GB |
| Batch processing buffer | ~2-4 GB |
| CUDA overhead | ~1 GB |
| **Total** | **~8-14 GB** |

**Veredicto**: RTX 4090 (24GB) tiene margen suficiente. RTX 4070 Ti Super (16GB) tambien funciona pero con menos margen para batch grande.

---

## 6. Energia y Cooling

### 6.1 Consumo electrico

| Componente | Idle | Carga media | Maximo |
|------------|------|-------------|--------|
| CPU (EPYC 9354) | 80W | 150W | 280W |
| GPU (RTX 4090) | 30W | 250W | 450W |
| RAM (256GB) | 20W | 30W | 40W |
| NVMe (2x) | 10W | 15W | 20W |
| HDD (2x) | 10W | 15W | 20W |
| Mobo + fans | 30W | 40W | 50W |
| **Total** | **180W** | **500W** | **860W** |

### 6.2 UPS requerido

| UPS | Capacidad | Tiempo autonomia (carga media) | Precio |
|-----|-----------|-------------------------------|--------|
| APC Smart-UPS SRT 1500VA | 1500VA/1350W | ~15 min | $500 |
| CyberPower PR1500LCD | 1500VA/1500W | ~12 min | $400 |
| APC Smart-UPS SRT 2200VA | 2200VA/1980W | ~20 min | $800 |

**Recomendacion**: APC Smart-UPS SRT 1500VA. Suficiente para apagado graceful.

### 6.3 Cooling

| Escenario | Solucion | Precio |
|-----------|----------|--------|
| Oficina/cuarto con AC | AIO 360mm + fans del case | $150 |
| Cuarto sin AC | AIO 360mm + extractor mural | $250 |
| Rack en datacenter | Cooling del datacenter | Incluido |
| Cuarto dedicado | Mini-split 12,000 BTU | $500 - $800 |

**Temperatura objetivo**: GPU < 80°C bajo carga, CPU < 75°C.

---

## 7. Software Requerido (Licencias)

### 7.1 Open source (sin costo)

| Software | Version | Uso |
|----------|---------|-----|
| Ubuntu Server | 24.04 LTS | OS |
| Python | 3.11/3.13 | Runtime |
| ArcFace (InsightFace) | 0.7.x | Embeddings faciales |
| FAISS (Meta) | 1.7.x | Indice vectorial |
| SourceAFIS | 3.x | Matching huellas (fallback) |
| PostgreSQL | 16 | Metadata + audit |
| Redis | 7.x | Cache |
| Nginx | 1.24 | Reverse proxy |
| CUDA Toolkit | 12.4 | GPU compute |
| cuDNN | 8.9 | Deep learning primitives |
| Docker | 24.x | Aislamiento (opcional) |

### 7.2 Comercial (con costo)

| Software | Licencia | Precio (USD) | Justificacion |
|----------|----------|-------------|---------------|
| **VeriFinger Standard** | Server, 1M templates | $3,500 | Matching huellas NIST-grade |
| **VeriFinger Extended** | Server, 10M+ templates | $8,000 | Si se escala mas alla de 1M |
| **MegaMatcher** | Server, face+fingerprint | $15,000 | Suite completa (opcion premium) |

**Recomendacion**: VeriFinger Standard ($3,500). Cubre 1M templates que es suficiente para indexar las 88.4M personas (cada persona = 1 template consolidado de 10 huellas).

---

## 8. Resumen de Compra

### 8.1 Servidor (Opcion A - Workstation)

| Concepto | Precio USD | Precio MXN |
|----------|-----------|-----------|
| CPU AMD EPYC 9354 | $900 | $15,750 |
| RAM 256GB DDR5 ECC | $800 | $14,000 |
| GPU NVIDIA RTX 4090 24GB | $1,600 | $28,000 |
| SSD NVMe 2TB (OS) | $150 | $2,625 |
| SSD NVMe 4TB (datos) | $300 | $5,250 |
| HDD 8TB (backup) | $150 | $2,625 |
| Mobo Supermicro H13SSL-N | $700 | $12,250 |
| PSU 1200W Platinum | $350 | $6,125 |
| Case 4U rackmount | $300 | $5,250 |
| Cooling Noctua NH-U14S | $100 | $1,750 |
| UPS APC 1500VA | $500 | $8,750 |
| Switch 2.5GbE | $100 | $1,750 |
| Cableado + accesorios | $50 | $875 |
| **Subtotal hardware** | **$6,000** | **$105,000** |

### 8.2 Software

| Concepto | Precio USD | Precio MXN |
|----------|-----------|-----------|
| VeriFinger Standard SDK | $3,500 | $61,250 |
| ArcFace (open source) | $0 | $0 |
| FAISS (open source) | $0 | $0 |
| Ubuntu Server (open source) | $0 | $0 |
| **Subtotal software** | **$3,500** | **$61,250** |

### 8.3 Total

| Concepto | USD | MXN |
|----------|-----|-----|
| Hardware | $6,000 | $105,000 |
| Software | $3,500 | $61,250 |
| Instalacion + configuracion (1 dia) | $200 | $3,500 |
| **TOTAL** | **$9,700** | **$169,750** |

### 8.4 Costo operativo mensual

| Concepto | USD/mes | MXN/mes |
|----------|---------|---------|
| Electricidad (500W promedio, 24/7) | $45 | $788 |
| Internet (ya existente) | $0 | $0 |
| Backup a nube (500GB, Wasabi/B2) | $3 | $53 |
| Dominio SSL (ya existente) | $0 | $0 |
| Mantenimiento (1h/mes) | $50 | $875 |
| **Total mensual** | **$98** | **$1,716** |

---

## 9. Donde Comprar

### 9.1 En Mexico

| Proveedor | Ventaja | URL |
|-----------|---------|-----|
| **Cyberpuerta** | Stock nacional, facturacion MX | cyberpuerta.mx |
| **PCEL** | Precios competitivos, envio rapido | pcel.com |
| **DDTech** | Servidores custom, soporte tecnico | ddt.com.mx |
| **PCMig** | Distribuidor autorizado Supermicro | pcmig.com.mx |
| **Amazon MX** | Variedad, garantia Amazon | amazon.com.mx |

### 9.2 Importacion directa

| Proveedor | Ventaja | URL |
|-----------|---------|-----|
| **Newegg** | Precios USD, envio a MX | newegg.com |
| **B&H Photo** | Hardware profesional | bhphotovideo.com |
| **Supermicro Store** | Servidores directo de fabricante | supermicro.com |

**Nota sobre importacion**: Los servidores y GPUs pueden pagar 16% IVA + 0-15% arancel dependiendo del clasificacion arancelaria. Verificar con agente aduanal antes de importar.

### 9.3 GPU usada (opcion economica)

| Fuente | Descuento vs nuevo | Riesgo |
|--------|-------------------|--------|
| eBay (vendedor verificado) | 30-40% | Garantia limitada |
| Facebook Marketplace | 40-50% | Sin garantia |
| Mineros de crypto (RTX 4090) | 30-50% | GPUs trabajadas, verificar |

**Recomendacion**: Comprar GPU nueva con garantia de 3 anos. La GPU es el componente mas critico y mas propenso a fallo bajo carga sostenida.

---

## 10. Checklist de Implementacion

### Fase 1: Compra y ensamble (Semana 1)
- [ ] Ordenar servidor (componentes o pre-armado)
- [ ] Ordenar UPS
- [ ] Ordenar VeriFinger SDK
- [ ] Recibir y ensamblar
- [ ] Instalar Ubuntu 24.04 LTS
- [ ] Instalar NVIDIA driver 550+
- [ ] Instalar CUDA 12.4 + cuDNN 8.9
- [ ] Verificar GPU con `nvidia-smi` y `torch.cuda.is_available()`

### Fase 2: Stack de software (Semana 2)
- [ ] Instalar Python 3.11 + venv
- [ ] Instalar PostgreSQL 16
- [ ] Instalar Redis 7
- [ ] Instalar Nginx
- [ ] Instalar ArcFace + FAISS
- [ ] Instalar VeriFinger SDK
- [ ] Configurar firewall (ufw)
- [ ] Configurar Tailscale
- [ ] Configurar Cloudflare Tunnel

### Fase 3: Pipeline biometrico (Semana 3)
- [ ] Desarrollar `biometric_ingest.py` (lectura del padron)
- [ ] Desarrollar `face_embedder.py` (ArcFace embeddings)
- [ ] Desarrollar `fingerprint_extractor.py` (VeriFinger templates)
- [ ] Desarrollar `index_builder.py` (FAISS + VeriFinger DB)
- [ ] Desarrollar `biometric_matcher.py` (API de matching)
- [ ] Integrar con `servir.py` (endpoints nuevos)
- [ ] Integrar con Oraculo (prompt de matching biometrico)

### Fase 4: Batch processing (Semanas 4-6)
- [ ] Obtener muestra de 1,000 registros para pruebas
- [ ] Procesar muestra completa (embeddings + templates)
- [ ] Verificar precision (FAR/FRR)
- [ ] Ajustar umbrales de matching
- [ ] Procesar lote completo (88M registros)
- [ ] Verificar integridad del indice

### Fase 5: Produccion (Semana 7)
- [ ] Configurar backup automatico diario
- [ ] Configurar monitoreo (Prometheus/Grafana o simple)
- [ ] Documentar API para el equipo
- [ ] Capacitacion de operadores
- [ ] Go-live
