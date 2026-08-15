# Validación OSINT — JOSE ALBERTO CANO MENA

## Identificación del sujeto

| Campo | Valor | Fuente |
|-------|-------|--------|
| Nombre(s) | JOSE ALBERTO | INE + CheckID |
| Primer apellido | CANO | INE + CheckID |
| Segundo apellido | MENA | INE + CheckID |
| CURP | `CAMA840522HDFNNL08` | INE + CheckID |
| RFC SAT (con homoclave real) | `CAMA840522NP0` | CheckID |
| Fecha de nacimiento | 1984-05-22 | INE + CheckID |
| Sexo | HOMBRE | CheckID |
| Entidad de nacimiento (CURP) | CIUDAD DE MEXICO | CheckID |
| NSS (IMSS) | `45028439185` | CheckID |

## Domicilio según padrón INE (CDMX_2.xlsx)

| Campo | Valor |
|-------|-------|
| Calle | C PEGASO |
| Número exterior | 78 |
| Colonia | COL PRADO CHURUBUSCO |
| Código postal | 04230 |
| Sección | 519 |
| Manzana | 5 |
| Folio INE | 150044627 |
| Clave de elector (CVE INE) | CNMNAL84052209H901 |

## Datos fiscales (CheckID)

| Campo | Valor |
|-------|-------|
| Régimen fiscal | 605 - Sueldos y Salarios e Ingresos Asimilados a Salarios |
| Código postal fiscal | 04230 |
| Situación 69 / 69B | Sin problema (`conProblema: false`) |
| Email de contacto SAT | albertocanomena@gmail.com |
| RFC válido hasta | 2029-07-04 |

## Notas y advertencias

- **El campo `CVE` del INE (`CNMNAL84052209H901`) NO es el RFC**. Es el identificador interno del padrón electoral. El RFC real con homoclave del SAT es `CAMA840522NP0`, confirmado vía CheckID.
- El CURP `CAMA840522HDFNNL08` coincide entre INE y CheckID.
- El CP `04230` coincide entre el domicilio INE y el CP fiscal reportado por CheckID.
- No se encontró indicio de situación 69/69B en la validación fiscal.
- Créditos CheckID restantes al momento de la consulta: **282**.

## Fuentes consultadas

1. Padrón electoral INE 2018 — archivo `CDMX_2.xlsx` (local).
2. API CheckID (Canicalabs) — búsqueda por CURP/RFC, 2026-07-29.

---
*Reporte generado automáticamente. Última actualización: 2026-07-29.*
