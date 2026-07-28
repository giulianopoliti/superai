# Procurement Agent - Estado Y Plan De Accion

Fecha de actualizacion: 2026-07-27

## Resumen Ejecutivo

El Procurement Agent es el modulo de Stock AI para comparar listas de proveedores contra el catalogo
real del comercio, detectar oportunidades de compra y aprender por tenant a partir de revisiones
humanas.

La decision de arquitectura se mantiene:

```txt
Canales -> Assistant Core -> Procurement Services -> Repositories/Providers
```

No se esta construyendo un bot de WhatsApp para compras. Se esta construyendo una capacidad del core
que despues puede usarse desde WhatsApp, CLI, API, web o desktop.

## Objetivo Del Modulo

Permitir que un comercio:

- Importe el catalogo POS desde CSV.
- Cargue una lista de proveedor en PDF, imagen, texto o CSV.
- Extraiga productos y precios ofrecidos por el proveedor.
- Busque productos exactos, similares o alternativos en el catalogo interno.
- Compare costo actual, precio de venta, stock y precio de proveedor.
- Revise casos ambiguos antes de guardar aprendizaje.
- Reutilice correcciones futuras por `business_id`.

## Estado Actual

### Ya Implementado

- Modulo base `app/modules/procurement/`.
- Schemas Pydantic para:
  - `Product`
  - `Supplier`
  - `SupplierProduct`
  - `ProductSupplierPrice`
  - `ProductSupplierComparison`
  - `CatalogImportResult`
- Modelos SQLAlchemy para:
  - `products`
  - `suppliers`
  - `supplier_products`
  - `catalog_imports`
- Migraciones Alembic:
  - `20260725_0002_procurement_products_suppliers.py`
  - `20260725_0003_allow_duplicate_product_barcodes.py`
  - `20260727_0004_catalog_imports.py`
- Repositorio SQL `SqlProcurementRepository`.
- Importador POS `PosCatalogImportService`.
- Normalizador inicial:
  - texto sin acentos
  - decimales argentinos
  - unidades basicas (`g`, `kg`, `ml`, `l`)
- Carga bulk de productos para evitar imports lentos contra Supabase remoto.
- Registro auditable de importaciones de catalogo.
- Alta/upsert de proveedores por `business_id + normalized_name`.
- Guardado de listas estructuradas en:
  - `supplier_offer_documents`
  - `supplier_offer_items`
- CLI para importar ofertas estructuradas desde JSON.
- Interfaz `DocumentExtractionProvider`.
- Provider local `LocalTextSupplierOfferProvider` para textos simples y tests.
- Provider `OpenAISupplierOfferDocumentProvider` preparado para PDFs/imagenes con structured output.
- Prompt versionado `app/assistant/prompts/supplier_offer_extractor.md`.
- Comparacion inicial de precios por proveedor para un producto.
- `ProductMatchService` inicial para comparar ofertas contra el catalogo.
- CLI de comparacion con salida `summary`, `csv` o `json`.
- Persistencia de candidatos de matching en `product_match_candidates`.
- Feedback humano de matches en `product_match_feedback`.
- CLI para persistir, listar y aceptar/rechazar candidatos.
- Uso de feedback aceptado/rechazado en corridas futuras de matching.
- API FastAPI reusable por frontend, CLI interna o WhatsApp para importar, comparar y revisar.
- Tests unitarios y SQL con SQLite in-memory.

### Ya Aplicado En Base Real

Se aplicaron migraciones en Supabase/Postgres con:

```powershell
& "$env:APPDATA\Python\Python312\Scripts\uv.exe" run alembic upgrade head
```

Se creo un tenant inicial:

```txt
business_id = demo-business
```

Se importo el CSV real:

```txt
archivo: productos_2026-07-24T21-02-31.881Z.csv
filas leidas: 3035
productos importados: 3035
omitidos: 0
productos en DB: 3035
```

Ultimo import registrado via CLI:

```txt
catalog_import_id: ddf81291-758f-455a-911c-0dbab77b9b01
status: completed
productos activos: 3030
productos sin costo: 440
productos sin barcode: 552
barcodes duplicados: 56
```

Primera lista de proveedor guardada en base real:

```txt
supplier: Vital
supplier_offer_document_id: 03803038-deb9-4ce1-af78-604f1b3d2832
items guardados: 2
status: extracted
```

Primera comparacion real Vital vs catalogo:

```txt
source_pdf: 112964.pdf
supplier_offer_document_id: c71e30dd-76b9-42fd-a4ca-34575169b532
items comparados: 29
matches encontrados: 20
recomendados comprar: 5
revisar: 23
no comprar: 1
csv: output/vital_112964_comparison.csv
```

Diagnostico:

- `UVITA Vino t/b 1lt` matchea contra `UVITA TINTO TETRABRICK 1L`.
- Perfumeria tiene varios casos de distinta presentacion: Sedal 340ml vs catalogo 300ml/190ml.
- Packs como `NIVEA Jabón 3x125gr` o `REXONA Jabón 3x90gr` requieren comparacion por unidad.
- Hay falsos positivos de baja confianza que deben quedar en revision, por ejemplo lentejas vs nuggets.

### Validacion Local

Ultima validacion conocida:

```txt
ruff check: passed
pytest: 54 passed
```

## Decisiones Tomadas

### Identidad Del Producto POS

La identidad principal del producto importado desde POS es:

```txt
business_id + external_product_id
```

Motivo: el CSV real tiene `3035` IDs externos unicos, pero algunos codigos de barras repetidos.

El campo `barcode` no es unico. Es una ayuda de busqueda/matching, no una identidad absoluta.

### Producto Con Varios Proveedores

El modelo correcto es:

```txt
products
    1 -> N
supplier_products
    N -> 1
suppliers
```

Un mismo producto interno puede tener muchos precios de proveedor, cada uno con:

- `supplier_id`
- `supplier_product_name`
- `cost_price`
- `observed_at`
- `tax_included`
- `package_quantity`
- `metadata`

### Sin RAG Ni Base Vectorial Todavia

No usar RAG, pgvector ni Qdrant en esta etapa.

Motivos:

- El catalogo actual de 3035 productos es manejable con Postgres y scoring deterministico/fuzzy.
- Todavia no hay suficiente feedback humano confirmado para que embeddings aporten calidad real.
- La distincion critica no es solo semantica:
  - mismo producto
  - mismo producto con otro nombre
  - alternativa comparable
  - parecido pero no equivalente
  - producto nuevo
- Esa decision debe ser auditable y revisable por tenant.

Decision futura:

```txt
Postgres normal
-> pg_trgm si necesitamos similitud textual mejorada
-> pgvector cuando haya matches/rechazos/aliases reales
-> Qdrant solo si aparece escala o busqueda hibrida compleja
```

## Modelo De Datos Actual

### `products`

Catalogo interno normalizado del comercio.

Campos principales:

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

### `suppliers`

Proveedores del comercio.

Campos principales:

- `business_id`
- `name`
- `normalized_name`
- `notes`

### `supplier_products`

Precio observado de un proveedor para un producto interno.

Campos principales:

- `business_id`
- `supplier_id`
- `product_id`
- `supplier_product_name`
- `supplier_product_normalized_name`
- `cost_price`
- `currency`
- `tax_included`
- `package_quantity`
- `observed_at`
- `metadata`

### `catalog_imports`

Registro auditable de cada importacion del catalogo POS.

Campos principales:

- `business_id`
- `source_filename`
- `source_type`
- `row_count`
- `imported_count`
- `skipped_count`
- `status`
- `errors`
- `summary`
- `created_at`
- `completed_at`

### `supplier_offer_documents`

Documento o lista recibida de un proveedor.

Campos principales:

- `business_id`
- `supplier_id`
- `source_filename`
- `document_type`
- `extraction_status`
- `extraction_provider`
- `raw_text`
- `metadata`
- `created_at`
- `completed_at`

### `supplier_offer_items`

Items extraidos de una lista de proveedor.

Campos principales:

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

### `product_match_candidates`

Candidatos generados por el comparador para que un humano los revise.

Campos principales:

- `business_id`
- `supplier_offer_document_id`
- `supplier_offer_item_id`
- `product_id`
- `relationship_type`
- `confidence_score`
- `reasons`
- `cost_difference`
- `cost_difference_percentage`
- `estimated_margin_percentage`
- `recommendation`
- `status`
- `created_at`
- `updated_at`

Estados actuales:

- `pending`
- `accepted`
- `rejected`

### `product_match_feedback`

Historial de decisiones humanas sobre candidatos.

Campos principales:

- `business_id`
- `product_match_candidate_id`
- `supplier_offer_item_id`
- `candidate_product_id`
- `relationship_type`
- `accepted`
- `reviewed_by_user_id`
- `notes`
- `created_at`

## Riesgos Detectados Con Datos Reales

### Codigos De Barras Repetidos

El CSV real contiene codigos de barras repetidos. Ejemplos detectados:

- `7798117660196`: varias variantes de Zamboni.
- `7791290794276`: varias formas de detergente Ala limon 300ml.

Decision:

- No fusionar productos por barcode si existe `external_product_id`.
- Usar barcode como senal de matching, no como regla final.

### Encoding Del CSV

Algunos nombres muestran caracteres rotos, por ejemplo `CA�UELAS`.

Decision:

- El importador actual no bloquea por encoding.
- Hay que agregar deteccion/normalizacion de encoding en una mejora proxima.
- Para matching, conviene normalizar texto con y sin reemplazos comunes.

### Stock Negativo

El CSV contiene stocks negativos.

Decision:

- Importarlos tal cual por ahora.
- No corregirlos automaticamente.
- Usarlos como senal operativa, no como error tecnico.

## Plan De Accion

### Fase 1 - Cerrar Base De Catalogo

Objetivo: dejar el catalogo POS importable, repetible y auditable.

Estado: completada para el MVP.

Tareas:

1. Crear tabla `catalog_imports`. Hecho.
2. Registrar cada import con archivo, conteos, estado y errores. Hecho.
3. Agregar CLI o endpoint interno para importar catalogo sin scripts ad hoc. Hecho via CLI.
4. Agregar resumen post-import. Hecho con:
   - productos activos
   - productos sin costo
   - productos sin barcode
   - barcodes duplicados
   - familias detectadas
5. Mejorar normalizacion de encoding.

Criterio de aceptacion:

- Reimportar el mismo CSV no duplica productos.
- El resultado queda registrado en DB.
- El import se puede ejecutar con un comando documentado.

Resultado:

- El comando `python -m app.cli import-catalog` importa el CSV y crea una fila en
  `catalog_imports`.
- La ultima corrida real importo `3035/3035` productos.
- El resumen post-import queda guardado en `summary`.

### Fase 2 - Cargar Proveedores Y Listas Simples

Objetivo: poder guardar proveedores y listas estructuradas antes de OCR/vision.

Estado: completada para el MVP estructurado/manual.

Tareas:

1. Crear `SupplierService`. Hecho como `SupplierOfferService`.
2. Crear upsert de proveedor por `business_id + normalized_name`. Hecho.
3. Crear `supplier_offer_documents`. Hecho.
4. Crear `supplier_offer_items`. Hecho.
5. Agregar extractor inicial desde texto o CSV fixture. Pendiente para parser automatico.
6. Probar con lista estructurada manual. Hecho.

Criterio de aceptacion:

- Se puede crear proveedor `Vital`.
- Se puede crear proveedor de lacteos/fiambres.
- Se pueden guardar items extraidos con precio y nombre original.

Resultado:

- Se creo proveedor `Vital` en `demo-business`.
- Se guardo una lista manual con `CAÑUELAS ACEITE 900ML` y `ARROZ OKITA 1KG`.
- Los items quedan normalizados y vinculados al documento/proveedor.

### Fase 3 - Matching Inicial

Objetivo: generar candidatos contra `products`.

Estado: iniciada.

Tareas:

1. Crear `ProductMatchService`. Hecho.
2. Scoring inicial:
   - mapping confirmado
   - barcode si existe
   - marca + presentacion
   - nombre normalizado
   - similitud fuzzy con stdlib o dependencia liviana
3. Tipar relaciones:
   - `exact_match`
   - `same_product_different_name`
   - `comparable_alternative`
   - `similar_but_not_equivalent`
   - `not_same_product`
   - `new_product`
   - `unknown`
4. Crear `product_match_candidates`. Hecho.
5. Agregar tests con ejemplos reales:
   - Cañuelas aceite 900ml
   - Cremigal queso crema 290gr vs La Serenisima queso crema 290gr

Mejoras pendientes:

- Persistir `product_match_candidates`. Hecho.
- Guardar feedback humano. Hecho.
- Priorizar feedback aceptado/rechazado antes del fuzzy matching. Hecho.
- Normalizar precio por unidad para packs.
- Mejorar equivalencia exacta vs alternativa por marca y presentacion.

Criterio de aceptacion:

- Distinta marca no se marca como `exact_match`.
- Misma marca/presentacion con orden distinto puede ser match fuerte.
- Casos ambiguos quedan en revision.

### Fase 4 - Comparacion Comercial

Objetivo: convertir matches en oportunidades de compra.

Tareas:

1. Crear `PurchaseOpportunityService`.
2. Calcular:
   - precio proveedor
   - costo actual
   - diferencia en pesos
   - diferencia porcentual
   - margen estimado contra venta actual
   - stock actual
3. Clasificar recomendacion:
   - `buy`
   - `review`
   - `do_not_buy`
   - `unknown`
4. Crear `purchase_opportunities`.

Criterio de aceptacion:

- Las recomendaciones se calculan sin LLM.
- Los casos de baja confianza no se recomiendan automaticamente.

### Fase 5 - Feedback Y Aprendizaje Por Tenant

Objetivo: que el agente mejore con correcciones humanas.

Tareas:

1. Crear `supplier_product_mappings`.
2. Crear `product_match_feedback`. Hecho.
3. Guardar decisiones humanas:
   - aceptar match
   - rechazar match
   - marcar alternativa comparable
   - marcar producto nuevo
4. Priorizar feedback confirmado en corridas futuras.

Estado: implementado para `product_match_feedback` por `business_id`, `supplier_id` y
`supplier_offer_item.normalized_name`.

Criterio de aceptacion:

- Una correccion hecha para `demo-business` no afecta otro tenant.
- Una segunda corrida reutiliza matches confirmados.

### Fase 6 - OCR/Vision

Objetivo: soportar PDFs visuales como Vital.

Estado: iniciada.

Tareas:

1. Crear interfaz `DocumentExtractionProvider`. Hecho.
2. Implementar extractor local para texto simple. Hecho.
3. Implementar provider OpenAI para PDFs/imagenes con structured output. Preparado, pendiente de
   evaluacion real.
4. Mantener fake/local provider para tests. Hecho.
5. Evaluar Mistral OCR solo si OpenAI no alcanza en folletos reales.

Criterio de aceptacion:

- El backend recibe items estructurados.
- El provider no escribe en DB.
- Toda salida se valida con Pydantic.

## Proximo Paso Recomendado

Implementar Fase 1 completa:

```txt
catalog_imports + comando interno de import + reporte post-import
```

Despues avanzar con Fase 2:

```txt
suppliers + supplier_offer_documents + supplier_offer_items
```

Esto mantiene el avance ordenado y evita saltar a OCR/IA antes de tener una memoria de compras
auditable.

## Comandos Utiles

Levantar migraciones:

```powershell
cd C:\Users\54116\Downloads\stock-ai
& "$env:APPDATA\Python\Python312\Scripts\uv.exe" run alembic upgrade head
```

Validar codigo:

```powershell
& "$env:APPDATA\Python\Python312\Scripts\uv.exe" run ruff check .
& "$env:APPDATA\Python\Python312\Scripts\uv.exe" run pytest
```

Importar catalogo POS:

```powershell
& "$env:APPDATA\Python\Python312\Scripts\uv.exe" run python -m app.cli import-catalog `
  "C:\Users\54116\Downloads\posinterface 26-02-2026\csv\productos_2026-07-24T21-02-31.881Z.csv" `
  --business-id demo-business
```

Importar oferta estructurada de proveedor:

```powershell
& "$env:APPDATA\Python\Python312\Scripts\uv.exe" run python -m app.cli import-supplier-offer `
  "C:\ruta\oferta-vital.json" `
  --business-id demo-business
```

Formato JSON esperado:

```json
{
  "supplier_name": "Vital",
  "source_filename": "lista-vital.txt",
  "raw_text": "CAÑUELAS ACEITE 900ML $2990",
  "items": [
    {
      "raw_name": "CAÑUELAS ACEITE 900ML",
      "brand": "CAÑUELAS",
      "offer_price": "2990",
      "currency": "ARS",
      "page_number": 1,
      "confidence_score": "0.95"
    }
  ]
}
```

Extraer una oferta desde texto simple sin persistir:

```powershell
& "$env:APPDATA\Python\Python312\Scripts\uv.exe" run python -m app.cli extract-supplier-offer-text `
  "C:\ruta\oferta-vital.txt" `
  --supplier-name Vital
```

Extraer y persistir desde texto simple:

```powershell
& "$env:APPDATA\Python\Python312\Scripts\uv.exe" run python -m app.cli extract-supplier-offer-text `
  "C:\ruta\oferta-vital.txt" `
  --supplier-name Vital `
  --business-id demo-business `
  --persist
```

Comparar una oferta contra el catalogo:

```powershell
& "$env:APPDATA\Python\Python312\Scripts\uv.exe" run python -m app.cli compare-supplier-offer `
  "c71e30dd-76b9-42fd-a4ca-34575169b532" `
  --business-id demo-business `
  --format summary
```

Comparar y persistir candidatos revisables:

```powershell
& "$env:APPDATA\Python\Python312\Scripts\uv.exe" run python -m app.cli compare-supplier-offer `
  "c71e30dd-76b9-42fd-a4ca-34575169b532" `
  --business-id demo-business `
  --format summary `
  --persist-candidates
```

Listar candidatos persistidos con IDs:

```powershell
& "$env:APPDATA\Python\Python312\Scripts\uv.exe" run python -m app.cli list-product-matches `
  "c71e30dd-76b9-42fd-a4ca-34575169b532" `
  --business-id demo-business
```

Aceptar un candidato:

```powershell
& "$env:APPDATA\Python\Python312\Scripts\uv.exe" run python -m app.cli review-product-match `
  "c71e30dd-76b9-42fd-a4ca-34575169b532" `
  "<product_match_candidate_id>" `
  --business-id demo-business `
  --accepted `
  --reviewed-by-user-id local-user `
  --notes "Coincide con el producto del catalogo"
```

Endpoints API para frontend/WhatsApp:

```txt
POST /procurement/catalog-imports
POST /procurement/supplier-offers/from-json
POST /procurement/supplier-offers/{supplier_offer_document_id}/compare
GET  /procurement/supplier-offers/{supplier_offer_document_id}/matches
POST /procurement/product-matches/{product_match_candidate_id}/accept
POST /procurement/product-matches/{product_match_candidate_id}/reject
POST /procurement/product-matches/{product_match_candidate_id}/correct
```

Exportar comparacion a CSV:

```powershell
& "$env:APPDATA\Python\Python312\Scripts\uv.exe" run python -m app.cli compare-supplier-offer `
  "c71e30dd-76b9-42fd-a4ca-34575169b532" `
  --business-id demo-business `
  --format csv > output\vital_112964_comparison.csv
```

## Archivos Relacionados

- `docs/SPRINT_5_PROCUREMENT_AGENT.md`
- `app/modules/procurement/`
- `app/db/repositories/sql_procurement.py`
- `app/db/models.py`
- `alembic/versions/20260725_0002_procurement_products_suppliers.py`
- `alembic/versions/20260725_0003_allow_duplicate_product_barcodes.py`
- `alembic/versions/20260727_0006_product_match_candidates_feedback.py`
- `tests/db/test_sql_procurement_repository.py`
- `tests/modules/procurement/test_catalog_import.py`
