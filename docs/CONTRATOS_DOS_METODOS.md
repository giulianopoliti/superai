# Contratos ejecutables de análisis A/B

Estado: contratos compartidos listos y construcción A/B integrada con componentes presentes.
6 de octubre de 2026. V2 conserva opt-in explícito; ver activación al final.
Fuente única: `app/modules/procurement/analysis_contracts.py`. No crear DTOs alternativos.
Versión pública `supplier_offer_analysis_v1`; wire `supplier_offer_reader_v2`.

## Lectores (etapa 1)

`AnalysisDocumentReader.read(*, request: DocumentReadRequest) -> DocumentReadResult`.
Request: document_path, run_id, reading_id, analysis_method, phase initial/reread,
source_filename, start_page/end_page originales, supplier_hint, timeout_seconds.
El lector devuelve páginas **locales** en `extraction`; el integrador remapea una vez.
Result: extraction A/B nullable y call obligatorio (también fallos/refusal/incomplete).

Wire A: `ReaderStructuredExtractionV2` → `ReaderStructuredPageV2` →
`ReaderStructuredBlockV2` → proposals `ReaderOfferProposalV2`.
Wire B: `ReaderBlocksExtractionV2` → `ReaderPageV2` → `ReaderBlockV2`.
B no tiene proposals ni campos comerciales. Todos los campos wire son requeridos,
nullable donde corresponde, extra forbid. No cambiar wire legacy ni precio obligatorio final.

Campos comunes: supplier_name, pages, warnings; página: page_number nullable,
page_conditions, blocks, coverage. Bloque: fragments, page_condition_indices, warnings.
Fragmento: text literal, legibility readable/partial/unreadable. Coverage:
estimated_offer_blocks nullable, needs_visual_review, warnings.
A añade raw_name/brand/unit_size/unit/offer_price/currency/tax_included nullable,
price_type unit/pack/unknown, package_quantity/retail_contents_count nullable,
minimum_purchase objeto nullable, confidence_score nullable, field_evidence, warnings.
Compra mínima: value nullable, unit nullable, scope offer/page/unknown, refs.
Refs wire: scope block/page_condition, fragment_index **base cero**, quote exacta.
Field evidence: field cerrado y refs; referencias duplicadas/ajenas se validan en backend.

## Evidencia interna y JEV (etapas 2/3)

`OfferTextBlock`: block_id, run_id, reading_id, page_number nullable, order_on_page
base cero, full_text, fragments, page_conditions, condition_refs, proposals,
warnings/reason_codes, derived_offer_ids, supersedes_block_id nullable.
`EvidenceFragment`: fragment_id, text, order base cero, legibility.
full_text = unión con newline de fragments en orden; no otro texto libre del lector.
IDs asignados por backend por run/reading/página/orden; UI los trata como opacos.
Condiciones solo misma página y referidas explícitamente. Relectura conserva ambos
bloques; supersesión exige asociación validada, nunca nombre/fuzzy solamente.

`ValueCandidate`: candidate_id, block_id, fragment_id, start/end, literal, kind
number/money/quantity/size/text, value Decimal nullable, unit nullable.
Validar pertenencia y text[start:end]==literal; aparición no implica rol comercial.
`ChoiceQuestion`: question_id, options únicas, prompt.
`ChoiceDecision`: question_id, choice, confidence, probabilities, model, prompt_version.
Distribución finita 0..1, suma tolerancia 0.001; confianza es independiente.
Proveedor valida preguntas/opciones/modelo exactos respecto al request.

`BlockClassifier.classify_block(*, request: BlockClassificationRequest)
 -> BlockClassificationResult`.
Request: business_id, block, candidates, questions. Result: run_id, block_id,
decisions, classifications, calls, reason_codes. Classification: fragment_id,
category (lista cerrada del plan), decision.

`BlockAssembler.assemble_interpretation(*, business_id: str, block: OfferTextBlock,
 classification: BlockClassificationResult | None, candidates: list[ValueCandidate])
 -> BlockInterpretationResult`.
Result: block_id, offers, reason_codes, needs_visual_review. A consume proposals sin
classification; B requiere clasificación o devuelve pendiente conservando texto.
`OfferInterpretation`: offer_id/block_id/business_id; campos comerciales nullable,
minimum_purchase con value/unit/scope/refs; field_evidence dict de refs internas;
identity_status confirmed/pending/no_match, price_status confirmed/pending,
product_id nullable, reason_codes, needs_visual_review. No inferir pack de xN.

`OfferDecisionProvider.decide_offer(*, request: OfferDecisionRequest)
 -> OfferDecisionResult`. Request: business_id, block, offer, candidates
`IdentityCandidate`, questions. Result: offer_id, decisions, calls, reason_codes.
`AnalysisPolicy.validate_decision(*, request: OfferDecisionRequest,
 decision: OfferDecisionResult | None) -> PolicyResult` (offer, route, reason_codes).
`AnalysisPolicy.prepare_request(*, request: OfferDecisionRequest) -> OfferDecisionRequest`
construye preguntas antes de llamar JEV; el servicio no mantiene un builder alternativo.
Rutas resolved/needs_jev/needs_visual_review/needs_human_review.
Matching: `ProductMatchService.retrieve_analysis_candidates(*, business_id: str,
supplier_id: str, offers: list[OfferInterpretation], internal_top_k: int = 5,
feedback_conflicts: dict[str,list[str]] | None = None) -> dict[str,list[IdentityCandidate]]`.
El callback del servicio recibe business_id, offers (lote), supplier_name, internal_top_k.
Lookup del proveedor existente sólo lectura y tenant-safe; proveedor nuevo tiene feedback
vacío sin crear filas durante lectura. Catálogo local por ejecución, cargado una vez para
recuperación provisional. Límite público sigue independiente.
Precio publicado usa `decision_policy.published_price_evidence_valid(*, offer, block)`;
no repetir regex sobre una cita aislada en el servicio. Este helper valida contexto/spans.

## Métricas, ledger y público (backend/UI/QA)

`ProviderCallRecord`: call_id, run_id, reading_id, provider, model, prompt_version,
schema_version, analysis_method, phase initial/reread/classification/decision,
start_page/end_page, response_id, duration_seconds nullable, status
success/error/refusal/incomplete, safe_error nullable, usage nullable, cost nullable.
`TokenUsage`: input_tokens, cached_input_tokens, output_tokens,
reasoning_output_tokens, total_tokens todos nullable. Subtotales no se suman otra vez.
Call_id deduplica agregación de TODOS los intentos. Sin usage/tarifa: costo null.
`AnalysisCost`: amount, currency, tariff_id, estimated.

`AnalysisLedger`: run_id/business_id/method, blocks (todas versiones), interpretations,
pending_blocks, pages, calls, invalid_evidence, page_conditions, decision_audit, policy.
`PageRecoveryRecord`: page_number, initial_done, reread_count 0/1, reason_codes,
reading_ids, status, attempt_reasons (causas iniciales y posteriores conservadas).
`DecisionAuditRecord`: run_id, reading_id, block_id, offer_id nullable, phase,
questions (opciones exactas), decisions (probabilidades/confianza/modelo/prompt),
classifications, call_ids, reason_codes, status success/partial/error. No duplica texto
del bloque por pregunta. Excepciones también generan call/audit con usage desconocido.
Policy registra implementación, versión, thresholds, hash del source y fingerprint
de configuración para reproducir decisiones. No guardar claves ni pensamiento privado.

GET /procurement/supplier-offers/analysis-methods → `AnalysisMethodsResponse`:
contract_version, default_method, methods [{id,available,unavailable_reason_code,
unavailable_reason}]. A disponible puede seguir legacy; B requiere integración y claves.
POST multipart añade analysis_method default openai_structured; extraction_provider
legacy separado. Método desconocido 422; B indisponible 503 antes de lectura.
Respuesta añade analysis nullable y pending_blocks a los tres campos legacy.
`AnalysisSummary`: contract_version, run_id, method, status completed/partial,
page_count, observed_block_count, interpreted_offer_count, pending_block_count,
unlinked_offer_count, identity_review_offer_count, price_review_offer_count,
review_page_numbers, duration_seconds/usage/cost nullable. Conteos pueden solaparse.
`PendingBlock`: block_id, page_number nullable, order_on_page, text, reason_codes, reason.
GET /procurement/supplier-offers/{document_id}/analysis?business_id=... →
`DocumentAnalysisDetail`: document_id, analysis nullable, pending_blocks.
Histórico sin summary devuelve null; no inventar método desde provider o selector.
Persistir misma fuente en metadata.analysis y metadata.analysis_ledger;
el backend valida tamaño antes de cualquier escritura. Detalle ajeno/inexistente 404.

## Validación publicada

Pruebas offline: `tests/modules/procurement/test_analysis_contracts.py` verifica
wire cerrado con claves requeridas, A sin precio, B sin propuestas, precio final
obligatorio, texto canónico, refs de condiciones y distribuciones/confianza separadas.
Resultados reales se publican en BACKEND_IMPLEMENTACION_DOS_METODOS.md.
Resultado inicial contratos: 10 passed. Suite backend/ampliada de cierre: 134 passed;
las cifras y comandos están en BACKEND_IMPLEMENTACION_DOS_METODOS.md.
Pruebas falsas no certifican fidelidad visual, calibración JEV ni ahorro medido.

## Activación y límites actuales

`DOCUMENT_ANALYSIS_V2_ENABLED=false` conserva el flujo legacy y revisión de packs existente.
Con `true`, factory construye lector V2 + ensamblador por método + política + recuperación
por lote + JEV textual, si los componentes/configuración están disponibles.
B requiere clave OpenAI, clave JEV, modelo JEV fijo, prompts disponibles, factory integrada
y supports real. GET capacidades no consulta APIs ni abre sesiones DB.
Sin JEV, A puede operar con política determinista y pendientes; B queda indisponible.
Faltan claves: motivos openai_not_configured/jev_not_configured antes de method_disabled
o method_not_integrated. No modificar .env real desde estos agentes.

V2 usa workers de lectura inicial configurados (1–2), bloques de hasta dos páginas,
registro/interpretación/DB secuenciales en el worker de FastAPI. Una relectura máxima
por página comparte presupuesto y no ejecuta reviewer de packs. Una lectura más débil
no puede retirar variantes ni degradar identidades/base confirmadas: queda pendiente,
manteniendo la versión anterior. Se conservan versiones completas en ledger.
status partial incluye pendientes de identidad/precio además de bloques/cobertura.

Métricas agregan cada call_id una vez; ausencia de usage/tarifa es null. Presupuestos
operativos actuales son llamadas/plazo. Costo monetario requiere tarifas/estimaciones
identificadas; no anunciar un techo USD garantizado con datos desconocidos.
Metadata se valida en bytes UTF-8 antes de escrituras y nunca se trunca silenciosamente.
JSON/JSONB del modelo admite el ledger; SQLite de fixture verifica roundtrip y documentos
sin fichas. Límite configurable (default 4 MB) es política de aplicación, no una medición
del límite/latencia de la DB de producción.

Persistencia repetida de la misma extracción V2/run_id devuelve el documento guardado
sin duplicarlo y rechaza evidencia diferente o run de otro negocio. Una persistencia
interrumpida queda con ledger en documento y requiere revisión; no hay transacción
global/resume automático. Dos POST que generan run_id nuevos son ejecuciones distintas;
no se añadió idempotency key HTTP ni caché persistente entre ejecuciones.
