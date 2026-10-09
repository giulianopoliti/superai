# Implementación backend A/B

6 de octubre de 2026. Primera entrega: contratos compartidos listos.
Fuente: `../CONTRATOS_DOS_METODOS.md` y `analysis_contracts.py`.

Ejecutado offline: `.venv/Scripts/python.exe -m pytest tests/modules/procurement/test_analysis_contracts.py -q`
Resultado: **10 passed**, una advertencia Starlette/httpx preexistente.
No instalaciones, migraciones, llamadas pagas ni DB del negocio.

## Cierre local de integración

Factory real A/B conectada a los componentes presentes de etapas 1/2/3: OpenAI.read,
StructuredProposalAssembler / DeterministicBlockAssembler, DocumentDecisionPolicy,
prepare_request, build_classification_questions, JevAnalysisProvider y recuperación
de candidatos por lote. No schemas alternativos. V2 permanece false por defecto;
root puede activar DOCUMENT_ANALYSIS_V2_ENABLED=true en configuración local después
de revisión. No se modificó configuración real, claves ni modelos.

API añade analysis_method multipart, capacidades de solo lectura y detalle histórico
tenant-safe. Conserva import_result/comparison/warnings, añade analysis/pending_blocks.
B falla temprano si falta integración/configuración; caída posterior conserva B parcial,
sin fallback A. Histórico anterior/local_text no recibe un método inventado.
El endpoint es sync: FastAPI ejecuta la orquestación bloqueante fuera del event loop;
sesiones se abren/cerran en ese worker. Lectores concurrentes sólo transportan HTTP,
no comparten DB ni ledger. Hasta dos tareas iniciales activas; executor termina antes
de borrar PDFs temporales, sin reemplazos por timeout de Future.

Ledger conserva bloques/versiones/condiciones, propuestas inválidas, pendientes,
preguntas/opciones/respuestas/probabilidades JEV y todos los intentos, incluidos fallos.
Binding run/reading/method/phase/rango se comprueba antes de agregar métricas.
Metadata completa y item_count se validan en UTF-8 antes de writes; no se trunca.
Proveedor se consulta sin escribir durante análisis; proveedores nuevos tienen feedback
vacío. Catálogo provisional se carga una vez y recuperación recibe varias ofertas juntas.
Matching público sigue separado del top-k interno de cinco. V2 no recupera cantidades
o presentación por regex cuando la interpretación dejó null.

Recuperación conjunta una vez por página, sin pack reviewer V2. Supersesión exige
asociación de texto/condiciones y cobertura compatible de todas las interpretaciones
anteriores, preservando precio, variantes y decisiones confirmadas. Versión más débil
queda pendiente, sin publicar duplicados espurios. La evidencia del precio usa helper
compartido de etapa 2; no hay validador de regex paralelo para porcentajes/precios anteriores.
status partial incluye revisiones de identidad y precio.

## Archivos cambiados por este chat

- app/modules/procurement/analysis_contracts.py (nuevo)
- app/modules/procurement/document_analysis.py (nuevo)
- app/modules/procurement/schemas.py
- app/modules/procurement/supplier_offers.py
- app/main.py
- app/settings.py
- .env.example
- tests/modules/procurement/test_analysis_contracts.py (nuevo)
- tests/providers/test_analysis_recovery.py (nuevo)
- tests/test_document_analysis_api.py (nuevo)
- docs/CONTRATOS_DOS_METODOS.md (nuevo)
- docs/agents/BACKEND_IMPLEMENTACION_DOS_METODOS.md (nuevo)

dual.py queda legacy y sin cambios: V2 reemplaza su orquestación mediante el servicio
nuevo; no cambia pruebas/comportamiento dual histórico. No se editaron archivos de otros
agentes, frontend, repositorios, migraciones, prompts/lector ni matching/política/JEV.
Se preservaron cambios locales anteriores; no resets, limpieza ni commits.

## Pruebas reales ejecutadas

1. Primera entrega: contratos 10 passed.
2. `.venv/Scripts/python.exe -m pytest tests/modules/procurement/test_analysis_contracts.py tests/providers/test_analysis_recovery.py tests/test_document_analysis_api.py tests/integration/test_document_analysis_acceptance.py tests/test_procurement_api.py tests/modules/procurement/test_supplier_offers.py tests/providers/test_dual_document_extraction.py tests/providers/test_pack_review.py -q`
   Resultado: **134 passed**, una advertencia preexistente Starlette/httpx.
3. Después de agregar fingerprint de política:
   `.venv/Scripts/python.exe -m pytest tests/test_document_analysis_api.py tests/providers/test_analysis_recovery.py -q`
   Resultado: **26 passed**, misma advertencia.
4. Ruff sobre archivos Python de ownership: sin errores después de corregir imports.
   Se formatearon solamente los archivos nuevos; no se reformateó el repo.

Las pruebas API usan la factory real y sustituyen sólo lectura/transporte remoto con
fakes; ensamblador, política, lookup, matching y persistencia SQLite de fixture son reales.
Verifican A/B, parciales sin fichas, historial ajeno 404, precio original, nulls V2,
idempotencia de la misma extracción/run, límite antes de escrituras, razones de capacidad,
caída JEV y ausencia de reviewer extra. Recuperación prueba simultaneidad con Barrier,
peak=2, remapeo único, múltiples variantes, degradaciones, relectura vacía y usage único.

Durante el trabajo hubo un fallo legacy en recálculo de corrección y dos fallos B en
evidencia de base del precio, en componentes de etapas 2/3. Dejaron de reproducirse tras
sus actualizaciones; la suite de 134 pasó. No se corrigieron sus archivos desde este chat.

## Dependencias y límites pendientes

- Integración local construida y probada; root conserva revisión cruzada, full suite,
  activación local del flag y validación UI. No afirmar cierre global/producción.
- No se midieron calidad, ahorro ni latencia con documentos reales/etiquetas humanas.
- Usage/costo desconocidos siguen null. Hay límites de llamadas/plazo; no techo USD
  garantizado sin tarifas/estimaciones. Clasificación/decisiones JEV quedan secuenciales.
- Persistencia sigue usando commits existentes; no transacción global. Reintento de
  extracción completada con mismo run es idempotente; persistencia interrumpida requiere
  revisión, y un nuevo POST genera otro run (sin idempotency key HTTP).
- JSON/JSONB y límites se verificaron con SQLite de fixture, no DB del negocio.
  Un ledger mayor al máximo produce error explícito antes de writes; no almacenamiento
  alternativo ni truncación. No instalaciones ni migraciones.
