# Flujo de Coordenadas a Servicios CFE

## Objetivo

Recibir coordenadas GPS, convertirlas a una dirección, buscar los servicios CFE relacionados en la base normalizada y exportar los resultados a CSV.

## 1. Coordenadas

Las coordenadas pueden recibirse en grados, minutos y segundos:

```text
16°48'41.3"N 99°50'40.9"W
```

Se convierten a grados decimales:

```text
latitud  = 16.8114722
longitud = -99.8446944
```

Para longitud oeste se utiliza signo negativo.

## 2. Geocodificación inversa

Se consulta Nominatim/OpenStreetMap:

```text
https://nominatim.openstreetmap.org/reverse?lat=16.8114722&lon=-99.8446944&format=jsonv2&zoom=18
```

La respuesta para este punto fue:

```text
Calle Baja Catita,
Pichilingue, Puerto Marqués,
Acapulco de Juárez, Guerrero, México
```

El CP devuelto por el mapa fue `99820`. Este dato no debe usarse como único criterio de búsqueda, porque los CSV de CFE pueden contener CP truncados, incorrectos o mezclados con otros campos.

## 3. Búsqueda en CFE

La base normalizada está en:

```text
_normalized/cfe.duckdb
_normalized/parquet/
```

La tabla principal es `medidores`. Para evitar problemas de bloqueo de DuckDB por otro proceso, también se puede consultar directamente el Parquet.

La búsqueda debe revisar todos los campos de domicilio:

- `direccion`
- `calle_adicional_1`
- `calle_adicional_2`
- `colonia`

Consulta para la calle literal:

```sql
SELECT
    numero_servicio,
    nombre,
    direccion,
    calle_adicional_1,
    calle_adicional_2,
    colonia,
    division,
    zona_codigo,
    __source_folder,
    __source_file
FROM read_parquet(
    '/ruta/a/_normalized/parquet/*/*.parquet'
)
WHERE upper(concat_ws(
    ' ',
    coalesce(direccion, ''),
    coalesce(calle_adicional_1, ''),
    coalesce(calle_adicional_2, ''),
    coalesce(colonia, '')
)) LIKE '%BAJA CATITA%'
ORDER BY numero_servicio;
```

En la ejecución original, la búsqueda se limitó a la carpeta de Guerrero:

```text
5_MOR_GRO_EDMX__5_152_982
```

La coincidencia corresponde a la división `DG`, zona `81`, archivo `Hoja4.csv`.

## 4. Resultados

La búsqueda literal `BAJA CATITA` produjo:

- `60` registros
- `53` números de servicio distintos

Puede haber duplicados en los CSV originales. Para contar registros y servicios únicos:

```sql
SELECT
    COUNT(*) AS registros,
    COUNT(DISTINCT numero_servicio) AS servicios_distintos
FROM read_parquet(
    '/ruta/a/_normalized/parquet/5_MOR_GRO_EDMX__5_152_982/*.parquet'
)
WHERE upper(concat_ws(
    ' ',
    coalesce(direccion, ''),
    coalesce(calle_adicional_1, ''),
    coalesce(calle_adicional_2, ''),
    coalesce(colonia, '')
)) LIKE '%BAJA CATITA%';
```

## 5. Exportación CSV

Se exportan todos los registros encontrados, conservando las columnas normalizadas y los metadatos de origen:

```sql
COPY (
    SELECT *
    FROM read_parquet(
        '/ruta/a/_normalized/parquet/5_MOR_GRO_EDMX__5_152_982/*.parquet'
    )
    WHERE upper(concat_ws(
        ' ',
        coalesce(direccion, ''),
        coalesce(calle_adicional_1, ''),
        coalesce(calle_adicional_2, ''),
        coalesce(colonia, '')
    )) LIKE '%BAJA CATITA%'
)
TO '/ruta/a/_normalized/baja_catita.csv'
(HEADER, DELIMITER ',');
```

Archivo generado:

```text
_normalized/baja_catita.csv
```

## 6. Recomendaciones para el servidor

- No depender únicamente del CP obtenido por geocodificación.
- Buscar el nombre de la calle en todos los campos de domicilio.
- Normalizar mayúsculas/minúsculas antes de comparar.
- Usar `coalesce` para manejar valores nulos.
- Mostrar tanto los registros como los números de servicio distintos.
- Conservar `__source_folder` y `__source_file` para auditoría.
- Informar al usuario cuando una coordenada corresponde a varios servicios.
- Solicitar número exterior, departamento, titular o medidor cuando se necesite un recibo único.
- Respetar la política de uso de Nominatim y configurar un `User-Agent` identificable en el servidor.
