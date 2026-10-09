# Piloto de extracción dual y Jev

Estado: infraestructura offline/shadow implementada; aceptación automática y publicación de
precios **desactivadas**. No hay un conjunto etiquetado real en este repositorio. El PDF
original de Diarco referido por `DIARCO_PDF_EXTRACTION_PLAN.md` tampoco está disponible
en el workspace actual; las imágenes de inspección no equivalen a etiquetas verificadas.

## Preparar línea base

Etiquetar manualmente cada bloque visible de páginas de Diarco y al menos otro proveedor.
Separar documentos de desarrollo y ciegos antes de ajustar reglas. Guardar un JSON por
documento con la huella SHA-256 del PDF original, `business_id`, `supplier_id`, `split`
(`development` o `blind`) y `labels` con página (1-indexada), nombre literal, precio,
moneda y `product_id` (null si no existe). Ejemplo de forma, **no datos reales**:

```json
{
  "business_id": "business-id", "supplier_id": "supplier-id",
  "document_sha256": "sha256-del-pdf", "split": "development",
  "labels": [
    {"page_number": 1, "raw_name": "PRODUCTO 900 ML", "offer_price": "1234.00",
     "currency": "ARS", "product_id": null}
  ]
}
```

Registrar por separado el resultado del flujo legacy sobre esas páginas y sus matches
confirmados. No derivar la verdad de Gemini, Luna ni Jev. El conteo de 56 ofertas de
Diarco en la nota histórica no es un total de referencia.

## Ejecución

Con `DATABASE_URL` configurada, desde la raíz del repo:

```powershell
./.venv/Scripts/python.exe -m scripts.run_jev_pilot baseline --pdf "ruta/al/folleto.pdf" --business-id BUSINESS --document-id LEGACY_DOCUMENT --labels "ruta/etiquetas.json" --output "output/piloto-baseline.json"
```

Este comando solo lee ofertas legacy existentes en la base (`DATABASE_URL`) y exige el
PDF original para comprobar la huella de las etiquetas. Verificar manualmente que
`LEGACY_DOCUMENT` corresponde a ese archivo y proveedor: el documento antiguo no
guarda su SHA-256. Luego, con `GEMINI_API_KEY` y `OPENAI_API_KEY`, ejecutar la lectura dual:

```powershell
./.venv/Scripts/python.exe -m scripts.run_jev_pilot dual --pdf "ruta/al/folleto.pdf" --business-id BUSINESS --supplier-id SUPPLIER --labels "ruta/etiquetas.json" --output "output/piloto-dual.json"
```

Cada proveedor recibe el mismo PDF de una sola página, sin ver la salida del otro. La
lectura Gemini del piloto no reemplaza páginas dudosas con el fallback directo. La
conciliación solo une nombres únicos en una página; bloques repetidos sin coordenadas se
dejan ambiguos. Un conflicto de precio/presentación no se resuelve por votación. El JSON
contiene observaciones, errores, conflictos y métricas para Gemini, Luna y su acuerdo.
En `agreement`, `correct_price_recall` cuenta solamente precios con acuerdo A/B; en
cada proveedor cuenta sus precios individuales. `false_price_count` cuenta precios que no
coinciden con una etiqueta. Revisar falsos positivos y omisiones contra la página original.

Con `DATABASE_URL` y `JEV_API_KEY` configuradas y una versión fija del modelo:

```powershell
./.venv/Scripts/python.exe -m scripts.run_jev_pilot shadow --business-id BUSINESS --document-id DOCUMENT --model jev-VERSION-FIJA --output "output/piloto-jev.json"
```

Este comando recupera hasta cinco candidatos por oferta y registra el primer fuzzy,
las opciones presentadas, la respuesta cerrada de Jev, probabilidades, versión, tokens
y errores. No persiste candidatos ni feedback. Los IDs de producto deben cotejarse con
las etiquetas humanas para calcular recall@5 y precisión. Una decisión humana previa
tiene prioridad en la recuperación. El costo/latencia de API y revisión manual requieren
medición operativa aparte; el script no los estima.

## Compuertas de avance

1. No usar los resultados sin etiquetas completas y partición ciega. Comparar extracción
   legacy, Gemini, Luna y acuerdo A/B por proveedor y página; revisar importes falsos y
   duplicados, no solo cantidad de filas.
2. Medir recall@5 antes de atribuir errores a Jev. Si el producto correcto no está entre
   las opciones, cambiar recuperación/normalización y repetir evaluación ciega.
3. Comparar elección Jev con fuzzy y decisión humana en shadow, incluyendo `none`, errores,
   latencia, tokens y subgrupos de marcas/tamaños. No tratar confidence como probabilidad
   calibrada de acierto.
4. Asistido/auto requiere una política aprobada con tamaño de muestra, precisión mínima,
   vetos de presentación, trazas persistidas y rollback. No hay flag de autoactivo ni
   cambios de estado de candidatos en este piloto; falta implementar esa compuerta.
5. Publicación en `supplier_products` requiere confirmación humana separada, deduplicación,
   vigencia/anulación y trazabilidad de documento/página. La comparación entre proveedores
   actual no consume estos resultados shadow. No publicar precios ambiguos.

El flujo `legacy` sigue siendo el único que guarda candidatos. Al recomparar preserva
filas revisadas y sus referencias de feedback; solo reemplaza pendientes. Las
observaciones shadow se escriben únicamente en el JSON solicitado por el operador.
