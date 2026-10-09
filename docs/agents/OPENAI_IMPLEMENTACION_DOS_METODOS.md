# Implementación de lectores OpenAI A/B

Fecha: 6 de octubre de 2026.
Estado: componente lector V2 implementado y verificado offline; integración de
servicio/API/persistencia/UI y evaluación con documentos reales no certificadas.

## Entrega y contratos consumidos

Se consumen exclusivamente las clases publicadas por backend en
`app/modules/procurement/analysis_contracts.py` y la firma congelada en
`docs/CONTRATOS_DOS_METODOS.md`. No se creó un esquema alternativo ni se modificó
el wire legacy. Versiones: `supplier_offer_reader_v2` y prompts
`supplier_offer_structured_v2` / `supplier_offer_blocks_v1`.

`OpenAISupplierOfferDocumentProvider` mantiene su constructor existente y
`extract_supplier_offer(...)`. Añade:

```python
reader.read(request=request)  # DocumentReadRequest -> DocumentReadResult
```

El request selecciona `openai_structured` o `openai_blocks_jev`, el archivo de chunk,
run/reading, fase, nombre fuente, rango original y timeout de la llamada. El lector
devuelve `ReaderStructuredExtractionV2` o `ReaderBlocksExtractionV2` con páginas
locales, sin asignar IDs de bloques/fragmentos ni convertir páginas originales.

El servicio integrador debe ejecutar una sola conversión local → original, generar
IDs, reconstruir `full_text` desde fragmentos y validar cobertura/referencias antes
de derivar fichas. El proveedor conserva páginas y referencias inválidas que el wire
permite, para que el backend registre su evidencia y motivo pendiente; un resultado
de lectura `success` significa JSON válido, no precio o identidad confirmados.

## Transporte y fallos

Ambas opciones y el lector legacy comparten `_inline_response()`: PDF o imagen
inline, una generación por invocación, modelo configurado sin cambios y
`max_retries=0`. No hay uploads/remociones remotos ni relecturas decididas aquí.
V2 usa `responses.create` con schema JSON estricto generado por el mismo helper
Pydantic del SDK instalado. Se captura la respuesta cruda antes de ejecutar
`model_validate_json`, evitando perder usage si el JSON no valida. Legacy mantiene
`responses.parse` y su conducta previa.

`DocumentReadResult.call` siempre se devuelve en V2, también ante excepción de
transporte, refusal, incomplete, respuesta no completada, texto ausente o JSON/schema
inválido. Contiene call_id único, run/reading/fase, método, páginas, proveedor/modelo,
prompt/schema, duración y response_id/usage cuando existen. Una excepción devuelve
envelope con extraction=null; el servicio debe agregar `call` al ledger antes de
decidir recuperación o lanzar su propio error. No hay colector global mutable.

Usage ausente permanece null; detalles ausentes también. Cached pertenece a input
y reasoning a output: no se suman otra vez ni se inventa total. Cost permanece null
sin tarifa. Error seguro usa códigos/clase de excepción, sin copiar mensajes,
contenido de rechazo, cuerpos, credenciales o texto documental. Duración mide el
intento del lector con monotonic, incluyendo preparación y validación.

Los prompts requieren conservar sin clasificación destructiva texto mixto, precio
actual/anterior, contenido, cantidades y mínimos. A admite varias propuestas por
bloque y ofertas sin precio; mínimo conserva value/unit/scope/refs. B no devuelve
propuestas. Fragmentos mantienen orden/espacios; bloques ilegibles y promociones
sin importe siguen representados. No se pide duplicar full_text en la salida.

## Archivos cambiados por este agente

- `app/providers/documents/openai.py`: transporte compartido y método read V2.
- `app/providers/documents/base.py`: captura de usage y envelope de respuesta V2.
- `app/assistant/prompts/supplier_offer_structured_v2.md`: prompt nuevo A.
- `app/assistant/prompts/supplier_offer_blocks_v1.md`: prompt nuevo B.
- `tests/providers/test_openai_document_extraction_v2.py`: 23 casos parametrizados offline.
- `docs/agents/OPENAI_IMPLEMENTACION_DOS_METODOS.md`: este informe.

base/openai ya tenían cambios locales: se preservaron sus clases/funciones legacy
y lectura inline. No se atribuyen al agente todos los cambios de esos archivos
contra HEAD. No se modificaron schemas.py, dual.py, main.py, settings.py,
supplier_offers.py, repositorios, contratos compartidos ni pruebas legacy.

## Verificación realizada

Comandos ejecutados con el entorno existente, sin instalaciones:

```powershell
.venv/Scripts/python.exe -m pytest tests/providers/test_openai_document_extraction.py -q
# 3 passed

.venv/Scripts/python.exe -m pytest tests/providers/test_openai_document_extraction_v2.py tests/providers/test_openai_document_extraction.py -q
# 24 passed en la primera pasada (21 casos V2 + 3 legacy).

.venv/Scripts/python.exe -m ruff format app/providers/documents/openai.py app/providers/documents/base.py tests/providers/test_openai_document_extraction_v2.py
# 3 files reformatted, solo ownership asignado.

.venv/Scripts/python.exe -m pytest tests/providers/test_openai_document_extraction_v2.py tests/providers/test_openai_document_extraction.py tests/providers/test_document_chunking.py tests/providers/test_dual_document_extraction.py tests/providers/test_document_fallback.py tests/providers/test_offer_reconciliation.py tests/providers/test_pack_review.py -q
# 78 passed después de añadir dos pruebas V2 adicionales.

.venv/Scripts/python.exe -m ruff check app/providers/documents/openai.py app/providers/documents/base.py tests/providers/test_openai_document_extraction_v2.py
# All checks passed!

.venv/Scripts/python.exe -m ruff format --check app/providers/documents/openai.py app/providers/documents/base.py tests/providers/test_openai_document_extraction_v2.py
# 3 files already formatted
```

La última suite verifica transporte inline PDF/imagen, schemas cerrados con campos
requeridos y Decimal numérico, selección de prompt/formato, páginas locales 1–2
para rango original 7–8, evidencia A/B, mínimo con unidad/alcance/refs, versiones de
intento, métricas de éxito/fallos, detalles nullable y un objeto Response real del
SDK construido offline. También conserva pruebas de bloques/reconciliación/review
legacy. Warning observado: deprecación Starlette/httpx existente; no se instalaron
dependencias para silenciarlo. Lint y formato finales aprobados.

## Dependencias y pendientes del integrador

- Conectar `read` al servicio/API V2; registrar calls también cuando extraction=null.
- Convertir páginas una vez; validar duplicados/rango/páginas faltantes, citas,
  índices, cantidades y asociaciones sin descartar evidencia inválida.
- Asignar IDs y reconstruir full_text en backend; conservar múltiples versiones y
  ofertas repetidas. Estas responsabilidades no se trasladaron al lector.
- B requiere clasificador/ensamblador JEV; A requiere validar sus propuestas.
- Presupuesto compartido de recuperación, catálogo por business_id, persistencia,
  pendientes visibles y capacidades/UI pertenecen a otros componentes.
- La salida completa de un response incomplete no se acepta como lectura exitosa;
  se conserva diagnóstico/usage, sin intentar rescatar un JSON truncado como ficha.
- Pruebas falsas comprueban conservación de datos recibidos, no calidad de visión
  ni cumplimiento real del prompt. Corpus etiquetado y evaluación A/B siguen pendientes.

Sin dependencias nuevas, migraciones, llamadas pagas o escrituras en DB del negocio.
Gemini permanece suspendido. No se declara ahorro ni integración de producto completa.

## Fuentes oficiales consultadas

- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
- [File inputs](https://developers.openai.com/api/docs/guides/file-inputs).
- SDK ya instalado: `openai/types/responses/response_usage.py`,
  `openai/lib/_parsing/_responses.py` y `openai/resources/responses/responses.py`.
