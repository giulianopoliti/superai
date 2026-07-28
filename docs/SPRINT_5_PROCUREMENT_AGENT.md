# Sprint 5 - Procurement Agent

Documento operativo actualizado:

- `docs/PROCUREMENT_AGENT_ESTADO_Y_PLAN.md`

## Objetivo

Crear el primer modulo de comparacion asistida de compras para que un comercio pueda importar su
catalogo POS, cargar una lista PDF/CSV de proveedor, detectar productos comparables y revisar
oportunidades de compra con aprendizaje por tenant.

El agente debe ayudar a extraer y desambiguar, pero las decisiones de precio, margen, confianza y
persistencia deben quedar en servicios deterministas del backend.

## Problema A Resolver

Hoy el comercio descarga un CSV del POS con miles de productos y recibe listas de proveedores en PDF
o imagen. Comparar manualmente requiere:

- Buscar productos similares aunque los nombres no coincidan.
- Distinguir producto exacto, producto alternativo y producto parecido.
- Ver costo actual, precio de venta, stock y margen.
- Anotar proveedor por proveedor que conviene comprar.
- Recordar correcciones anteriores.

Ejemplos reales:

- Vital suele usar nomenclatura estable: marca primero, producto y presentacion.
- Listas de lacteos/fiambres pueden requerir ver la marca en la imagen.
- `QUESO CREMA CREMIGAL X290GR` no debe guardarse como el mismo producto que
  `QUESO CREMA LA SERENISIMA 290GR`, pero si puede ser una alternativa comparable.

## Decision Sobre Embeddings, RAG Y Base Vectorial

No usar base vectorial ni RAG en el primer sprint.

Motivos:

- El catalogo inicial ronda 3.000 productos por tenant, manejable con Postgres y scoring local.
- El valor principal esta en datos estructurados confirmados: producto, marca, presentacion,
  proveedor, precio, costo, margen, relacion y feedback humano.
- RAG no resuelve bien la diferencia critica entre "mismo producto" y "alternativa comparable" si no
  existe feedback confirmado.
- Agregar pgvector/Qdrant antes de tener ejemplos corregidos aumenta complejidad sin mejorar el MVP.

Si usar embeddings mas adelante, cuando existan datos reales de:

- Matches confirmados.
- Matches rechazados.
- Alias por proveedor.
- Equivalencias entre marcas.
- Notas operativas por tenant.

Decision recomendada futura:

- Primero `pg_trgm` o similitud textual en Postgres si hace falta mejorar busqueda.
- Luego `pgvector` en Supabase si el volumen y la calidad de datos lo justifican.
- Qdrant solo si necesitamos busqueda hibrida avanzada, reranking, payload filtering complejo o
  escala mucho mayor.

## Alcance Del Sprint

### Incluido

- Crear modulo `app/modules/procurement/`.
- Definir schemas Pydantic para importacion de catalogo, items de proveedor, candidatos de match y
  revision humana.
- Definir modelos SQL y migracion Alembic para el nucleo de compras.
- Importar productos desde CSV del POS.
- Normalizar nombres, marcas, presentaciones y precios.
- Extraer items de proveedor desde entrada estructurada o texto extraido.
- Crear motor inicial de matching deterministico/fuzzy.
- Generar candidatos con `confidence_score` y `relationship_type`.
- Guardar feedback humano por tenant.
- Reutilizar feedback confirmado en comparaciones futuras.
- Crear tests sin llamadas reales a LLM/OCR.

### Fuera De Alcance

- Frontend web o desktop.
- Compra automatica.
- Actualizar precios del POS.
- Facturas.
- OCR perfecto para todos los PDFs.
- Base vectorial.
- RAG semantico.
- Agentes autonomos con tools que escriban sin confirmacion.

## Modelo Conceptual

```txt
Catalog CSV
    |
CatalogImportService
    |
products / product_catalog_items

Supplier PDF or list
    |
SupplierOfferExtractionProvider
    |
supplier_offer_documents / supplier_offer_items
    |
ProductMatchService
    |
product_match_candidates
    |
Human review
    |
product_match_feedback / supplier_product_mappings
    |
Next comparison learns from tenant history
```

## Tipos De Relacion

Usar relaciones explicitas en vez de un booleano `matched`.

```txt
exact_match
same_product_different_name
comparable_alternative
similar_but_not_equivalent
not_same_product
new_product
unknown
```

Ejemplos:

- `CAÑUELAS ACEITE GIRASOL 900ML` vs `ACEITE CAÑUELAS GIRASOL 900ML`:
  `same_product_different_name`.
- `QUESO CREMA CREMIGAL 290GR` vs `QUESO CREMA LA SERENISIMA 290GR`:
  `comparable_alternative`, no `exact_match`.
- `QUESO CREMA 290GR` vs `QUESO UNTABLE 190GR`:
  probablemente `similar_but_not_equivalent` o `not_same_product`.

## Tablas Propuestas

Todas las tablas de negocio deben tener `business_id`.

### `products`

Catalogo interno normalizado.

- `id`
- `business_id`
- `external_product_id`
- `sku`
- `barcode`
- `name`
- `normalized_name`
- `brand`
- `category`
- `unit_size`
- `unit`
- `sale_price`
- `current_cost`
- `margin_percentage`
- `stock_quantity`
- `active`
- `source`
- `created_at`
- `updated_at`

### `catalog_imports`

Importaciones del POS.

- `id`
- `business_id`
- `source_filename`
- `source_type`
- `row_count`
- `imported_count`
- `skipped_count`
- `status`
- `metadata`
- `created_at`

### `suppliers`

Proveedores.

- `id`
- `business_id`
- `name`
- `normalized_name`
- `notes`
- `created_at`
- `updated_at`

### `supplier_offer_documents`

Documento/lista recibido.

- `id`
- `business_id`
- `supplier_id`
- `source_filename`
- `document_type`
- `extraction_status`
- `extraction_provider`
- `raw_text`
- `metadata`
- `created_at`

### `supplier_offer_items`

Items extraidos de la lista.

- `id`
- `business_id`
- `supplier_offer_document_id`
- `supplier_id`
- `raw_name`
- `normalized_name`
- `brand`
- `unit_size`
- `unit`
- `package_quantity`
- `offer_price`
- `currency`
- `tax_included`
- `page_number`
- `confidence_score`
- `metadata`
- `created_at`

### `supplier_product_mappings`

Aprendizaje confirmado.

- `id`
- `business_id`
- `supplier_id`
- `supplier_item_name`
- `supplier_item_normalized_name`
- `product_id`
- `relationship_type`
- `confidence_source`
- `notes`
- `created_at`
- `updated_at`

### `product_match_feedback`

Historial de correcciones humanas.

- `id`
- `business_id`
- `supplier_offer_item_id`
- `candidate_product_id`
- `relationship_type`
- `accepted`
- `reviewed_by_user_id`
- `notes`
- `created_at`

### `purchase_opportunities`

Resultado calculado para revision.

- `id`
- `business_id`
- `supplier_offer_item_id`
- `product_id`
- `relationship_type`
- `supplier_price`
- `current_cost`
- `sale_price`
- `cost_difference`
- `cost_difference_percentage`
- `estimated_margin_percentage`
- `stock_quantity`
- `confidence_score`
- `recommendation`
- `status`
- `created_at`

## Servicios

### `CatalogImportService`

- Lee CSV del POS.
- Mapea columnas conocidas (`snombre`, `rpreciou`, `rcostou`, `rstock`, `sfamilia`, `sean`).
- Convierte decimales argentinos.
- Normaliza marca, unidad y presentacion.
- Inserta/actualiza productos por `business_id` y claves externas.

### `SupplierOfferExtractionService`

- Recibe PDF/texto/lista.
- Primer MVP: acepta texto ya extraido o CSV/manual fixture.
- Siguiente paso: usa `DocumentExtractionProvider` para PDF visual.
- Devuelve `SupplierOfferItem` validado por Pydantic.

### `ProductNormalizationService`

- Normaliza nombres.
- Detecta unidades: `GR`, `KG`, `ML`, `L`, `UN`, `X12`, etc.
- Detecta marcas conocidas por catalogo/proveedor.
- Remueve ruido sin perder datos importantes.

### `ProductMatchService`

Orden de scoring:

1. Mapping confirmado por tenant.
2. Codigo de barras, si existe.
3. Marca + presentacion + familia.
4. Nombre normalizado.
5. Similitud fuzzy.
6. LLM solo para desambiguar casos con baja/media confianza.

### `PurchaseOpportunityService`

- Calcula diferencia contra costo actual.
- Calcula margen nuevo contra precio de venta actual.
- Clasifica recomendacion:
  - `buy`
  - `review`
  - `do_not_buy`
  - `unknown`
- Nunca depende del prompt para calculos comerciales.

## Umbrales Iniciales

```txt
>= 0.92: match seguro, mostrar como recomendado
0.75 - 0.91: requiere revision
0.55 - 0.74: candidato debil
< 0.55: no encontrado
```

Reglas:

- `comparable_alternative` siempre requiere revision al principio.
- Distinta marca nunca debe convertirse automaticamente en `exact_match`.
- Distinta presentacion debe bajar fuerte el score.
- Feedback humano confirmado supera al scoring automatico.

## Integracion Con Assistant Core

Nuevas intenciones futuras:

```txt
import_product_catalog
analyze_supplier_offer
list_purchase_opportunities
confirm_product_match
reject_product_match
save_supplier_mapping
```

Para el primer sprint, se pueden exponer endpoints internos/CLI antes de meterlo en WhatsApp:

```txt
POST /internal/procurement/catalog-imports
POST /internal/procurement/supplier-offers
POST /internal/procurement/supplier-offers/{id}/match
POST /internal/procurement/matches/{id}/feedback
GET  /internal/procurement/opportunities
```

## Proveedores De IA/OCR

Mantener interfaces:

```txt
DocumentExtractionProvider
LLMProductDisambiguationProvider
EmbeddingProvider
```

Implementacion inicial:

- `LocalTextExtractionProvider` para PDFs con texto.
- `FakeDocumentExtractionProvider` en tests.
- OpenAI Responses API despues, para PDF visual y structured outputs.
- Mistral OCR como alternativa si OpenAI no rinde bien con folletos.

## Criterios De Aceptacion

- Se importa el CSV del POS de ejemplo.
- Se crean/actualizan productos normalizados por `business_id`.
- Se procesa una lista de proveedor de ejemplo.
- Se generan candidatos de match.
- Se distingue `exact_match` de `comparable_alternative`.
- Se calcula margen estimado sin IA.
- Se guarda feedback humano.
- Una segunda corrida reutiliza el feedback.
- Tests pasan sin red.
- `ruff check` pasa.

## Orden De Implementacion

1. Crear schemas Pydantic de procurement.
2. Crear modelos SQL y migracion.
3. Crear normalizador de productos.
4. Crear importador CSV POS.
5. Crear repositorios.
6. Crear extractor fake/texto para listas.
7. Crear matcher deterministico/fuzzy.
8. Crear calculador de oportunidades.
9. Crear endpoints internos o CLI.
10. Agregar tests unitarios y de repositorio.
11. Probar con el CSV real.
12. Probar con lista de lacteos como fixture.
13. Recien despues integrar OpenAI/OCR visual.

## Riesgos

- Marcar productos alternativos como exactos.
- Comparar unidades distintas sin normalizar.
- Tomar precios de bulto como precio unitario.
- Ignorar IVA/flete/descuentos del proveedor.
- Aprender mal por feedback ambiguo.
- Meter IA en calculos que deben ser auditables.

## Decision Final Del Sprint

Iniciar sin RAG, sin base vectorial y sin pgvector.

La memoria inicial del agente debe ser relacional y auditable. Primero aprender matches,
rechazos y equivalencias por tenant. Cuando exista suficiente feedback real, agregar embeddings como
acelerador de busqueda y no como fuente de verdad.
