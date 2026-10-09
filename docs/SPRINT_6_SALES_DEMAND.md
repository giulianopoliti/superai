# Sprint 6 - Sales Demand

## Objetivo

Agregar una base de demanda historica por producto para que Stock AI pueda responder:

- Cuanto vendimos en un periodo.
- Cual fue la venta diaria promedio.
- Cuantos dias de stock estimados quedan.
- Cuanto conviene comprar cuando se cruza una oferta de proveedor con venta real.

Este modulo no reemplaza al POS. Lee exportaciones del POS y las normaliza para analisis.

## Archivo real analizado

```txt
C:\Users\54116\Downloads\posinterface 26-02-2026\csv\ventasitems_2026-06-30_2026-07-30.csv
```

El periodo viene en el nombre del archivo:

```txt
2026-06-30 a 2026-07-30
```

Resultado de la importacion real:

- Filas leidas: 8693
- Lineas nuevas insertadas: 8693
- Lineas duplicadas: 0
- Tickets unicos: 3169
- Productos POS unicos vendidos: 1390
- Dias con ventas: 31
- Registros diarios agregados: 6263
- Cantidad total vendida: 9292.676
- Venta total: 28375147.25
- Lineas sin costo: 1794
- Lineas sin match contra catalogo: 20
- Productos POS unicos sin match contra catalogo: 14

Resultado al volver a cargar el mismo archivo:

- Filas leidas: 8693
- Lineas nuevas insertadas: 0
- Lineas duplicadas: 8693
- Registros diarios reconstruidos: 6263

Estado historico despues de la carga:

- Ventas desde: 2026-06-30
- Ventas hasta: 2026-07-30
- Lineas de venta guardadas: 8693
- Registros diarios guardados: 6263

## Decision de modelo

La identidad primaria del producto del POS en ventas es `idproducto`.

En Stock AI:

- `products.id` es el UUID interno.
- `products.external_product_id` guarda el `idproducto` del POS.
- `product_sales_daily.external_product_id` guarda siempre el ID POS.
- `product_sales_daily.product_id` es nullable, para soportar ventas que todavia no matchean catalogo.

Esto evita perder ventas si el catalogo cambia o si algun producto no se importo todavia.

## Tablas agregadas

### `sales_imports`

Audita cada archivo de ventas importado.

Campos principales:

- `business_id`
- `source_filename`
- `source_type`
- `period_start`
- `period_end`
- `row_count`
- `imported_count`
- `skipped_count`
- `unique_ticket_count`
- `unique_product_count`
- `status`
- `errors`
- `summary`

### `sales_item_lines`

Guarda cada linea original de `ventasitems`.

Clave unica:

```txt
business_id + external_sale_item_id
```

Campos principales:

- `business_id`
- `source_import_id`
- `product_id`
- `external_sale_item_id`
- `external_sale_id`
- `external_product_id`
- `sku`
- `sold_at`
- `sale_date`
- `quantity`
- `revenue`
- `unit_cost`
- `cost_amount`
- `gross_margin`
- `raw_payload`

Esta tabla es la fuente de verdad para no duplicar ventas.
Si se carga otro archivo con dias superpuestos, las lineas ya existentes se cuentan como duplicadas
y no vuelven a sumarse.

### `product_sales_daily`

Guarda ventas agregadas por producto y dia.

Clave unica:

```txt
business_id + external_product_id + sale_date
```

Campos principales:

- `business_id`
- `source_import_id`
- `product_id`
- `external_product_id`
- `sku`
- `sale_date`
- `quantity_sold`
- `revenue`
- `cost_amount`
- `gross_margin`
- `ticket_count`
- `line_count`
- `zero_cost_line_count`

## Servicios y endpoints

Se agrego `PosSalesImportService`, que:

- Detecta periodo desde el nombre del archivo.
- Lee CSV con delimitador `;`.
- Soporta encodings `utf-8-sig`, `cp1252` y `latin-1`.
- Convierte decimales argentinos.
- Resuelve `product_id` interno desde `products.external_product_id`.
- Inserta lineas nuevas por `business_id + external_sale_item_id`.
- Cuenta duplicados si una venta ya fue cargada.
- Reconstruye ventas diarias por producto desde `sales_item_lines`.
- Persiste los agregados con upsert por tenant/producto/dia.

Endpoints agregados:

```txt
POST /sales/imports/from-file
GET  /sales/status
GET  /sales/products/{product_id}/summary
GET  /sales/top-products
GET  /sales/purchase-suggestions
```

## Performance

Contra Supabase/Postgres remoto se uso upsert nativo por lotes de 100 filas.

Motivo:

- Una sentencia unica supera el limite de parametros de Postgres/psycopg.
- Lotes grandes pueden pegar contra `statement_timeout`.
- Lotes de 100 fueron estables para 6263 registros diarios.

## Reglas para el proximo modulo

El calculo de compra sugerida no debe usar solo "top vendidos".

Debe considerar:

- Venta diaria promedio por periodo elegido.
- Stock actual solo si `use_stock=true`.
- Dias objetivo de cobertura.
- Lead time del proveedor.
- Stock minimo o colchon.
- Productos genericos del POS que conviene excluir o revisar, por ejemplo `VARIOS`.
- Stock negativo, que hoy aparece en el catalogo y debe tratarse como dato sospechoso o stock cero operativo.
- Productos sin costo, porque distorsionan margen.
- Oportunidades de proveedor ya calculadas por Procurement Agent.

Formula base sin stock, recomendada para el negocio hoy:

```txt
suggested_quantity =
  average_daily_quantity * (target_coverage_days + safety_days)
```

Formula opcional con stock:

```txt
suggested_quantity = max(
  0,
  average_daily_quantity * target_coverage_days
  + safety_stock
  - stock_deduction
)
```

Donde:

```txt
stock_deduction = 0 cuando use_stock=false
stock_deduction = max(current_stock, 0) cuando use_stock=true e ignore_negative_stock=true
```

Para proveedores:

```txt
target_coverage_days = lead_time_days + review_period_days + safety_days
```

Default operativo inicial:

```txt
use_stock=false
ignore_negative_stock=true
target_coverage_days=7
safety_days=0
```

## Proximos pasos recomendados

1. Permitir excluir productos genericos o familias no reponibles.
2. Guardar reglas por proveedor: lead time, visita, minimo de compra y frecuencia.
3. Luego sumar vista tipo "pedido sugerido" por proveedor/documento.

## Plan de compra por documento

Se agrego:

```txt
GET /procurement/supplier-offers/{supplier_offer_document_id}/purchase-plan
```

Parametros:

- `business_id`
- `date_from`
- `date_to`
- `target_coverage_days` opcional
- `use_stock` default `false`
- `ignore_negative_stock` default `true`

Comportamiento:

- Si `target_coverage_days` esta vacio, devuelve ventas del periodo y promedio diario sin cantidad
  sugerida.
- Si `target_coverage_days` tiene valor, calcula `suggested_quantity`.
- El calculo usa los matches del documento ya persistidos y cruza contra ventas historicas.
- Las ventas se consultan en bulk para que documentos grandes no hagan una consulta por item.

Prueba real:

- Documento: `276ab732-5399-4fd3-a5bf-e280f4c7f03c`
- Items del documento: 237
- Items con ventas historicas en el periodo 2026-06-30 a 2026-07-30: 73
