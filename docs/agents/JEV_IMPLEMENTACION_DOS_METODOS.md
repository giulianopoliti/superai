# Entrega JEV y ensamblador B

Fecha: 6 de octubre de 2026. Estado: componentes implementados y verificados offline;
esta entrega no certifica integración API, persistencia ni calidad con documentos reales.

## Archivos de ownership cambiados

- `app/providers/jev_analysis.py`
- `app/modules/procurement/block_interpretation.py`
- `app/assistant/prompts/jev_fragment_classification.md`
- `app/assistant/prompts/jev_offer_decision.md`
- `tests/providers/test_jev_analysis.py`
- `tests/modules/procurement/test_block_interpretation.py`
- `docs/agents/JEV_IMPLEMENTACION_DOS_METODOS.md`

No se editaron `jev.py`, piloto legacy, settings, contratos comunes, política, matching,
lector OpenAI, frontend ni archivos de otros agentes. Se preservaron cambios locales.
No hubo instalaciones, migraciones, llamadas de inferencia reales ni DB del negocio.
Skills utilizadas: `stock-ai-prompts` y `stock-ai-quality-review`.

## Interfaces exactas publicadas

Se consumen exclusivamente los DTOs de `analysis_contracts.py`.

```python
# app.modules.procurement.block_interpretation
detect_value_candidates(*, block: OfferTextBlock) -> list[ValueCandidate]
validate_value_candidates(*, block: OfferTextBlock, candidates: list[ValueCandidate]) -> None

DeterministicBlockAssembler().assemble_interpretation(
    *, business_id: str, block: OfferTextBlock,
    classification: BlockClassificationResult | None,
    candidates: list[ValueCandidate],
) -> BlockInterpretationResult

# app.providers.jev_analysis
build_classification_questions(
    *, block: OfferTextBlock, candidates: list[ValueCandidate],
) -> list[ChoiceQuestion]

prepare_block_request(
    *, business_id: str, block: OfferTextBlock,
    candidates: list[ValueCandidate] | None = None,
) -> BlockClassificationRequest

JevAnalysisProvider.classify_block(
    *, request: BlockClassificationRequest,
) -> BlockClassificationResult

JevAnalysisProvider.decide_offer(
    *, request: OfferDecisionRequest,
) -> OfferDecisionResult
```

`prepare_block_request` detecta ocurrencias si no se proporcionan; si el backend
proporciona candidatos, conserva sus IDs y valida literal/offset/valor/unidad.
Para el callback del servicio backend usar `build_classification_questions` con
los candidatos efectivos; no necesita un business_id ficticio para armar preguntas.
El ensamblador es exclusivamente B. A pertenece al `StructuredProposalAssembler`
del backend: no se interpretan sus propuestas como una clasificación B.

Construcción por ejecución:

```python
client = JevAnalysisClient(
    api_key=api_key, model="jev-1.13.0", timeout_seconds=20,
    max_concurrency=2, max_calls=64, max_questions=128,
    max_request_bytes=100_000, max_response_bytes=1_000_000,
)
provider = JevAnalysisProvider(
    client=client, analysis_method=method, deadline=absolute_monotonic_deadline,
)
```

Compartir ese cliente entre workers de la misma ejecución. El proveedor queda ligado
al primer `(business_id, run_id)` y rechaza reutilización entre tenants/ejecuciones.
El backend debe verificar que el bloque pertenece al ledger del tenant: el DTO
`OfferTextBlock` no contiene business_id. No hay caché ni accesos a repositorios.

## Preguntas y conservación

Una solicitud de clasificación por bloque agrupa categorías y selección de campos.
IDs internos: `fragment:{index}`; `value:{index}:{field}`. El index de value refiere
a `price_occurrences(block, candidates)`, no a un ID opaco partido por separadores.
El ID de pregunta no se usa como contexto implícito: todas incluyen instrucciones
autosuficientes con bloque, ocurrencia y evidencia explícitos.

Por ocurrencia se consultan `role`, `raw_name`, `unit_size`, `package_quantity`,
`retail_contents_count`, `minimum_purchase`, `minimum_scope`, `price_type` y
`basis_evidence`. Rol: current_price/previous_price/not_price/uncertain.
Campos de selección: IDs existentes + none/insufficient. Base: unit/pack/unknown.
`basis_evidence` elige un fragmento existente del bloque o condición de página
explícitamente vinculada, + none/insufficient. No agrega una llamada por campo.

Decisiones posteriores usan preguntas `{offer_id}:identity` y
`{offer_id}:price_basis` (también se admiten los nombres simples). Identidad admite
candidate_id del request + none/insufficient; precio unit/pack/unknown. IDs de
otra oferta, candidatos de otro tenant y referencias alteradas se rechazan antes
del HTTP. Producto elegido y base del precio son respuestas independientes.

No se generan nombres/números: nombre se copia del span elegido; valores se
copian del candidato parseado localmente. Variantes/precios se vinculan uno a uno,
sin producto cartesiano. Nombres iguales con precios múltiples quedan pendientes.
mixed/other/uncertain mantienen texto y clasificación. Bloques sin ficha mantienen
su evidencia en el ledger del caller y un motivo pendiente; no se inventa precio 0.

Se conserva el precio publicado y referencias de precio anterior. Cuando existe
`ahora/actual/hoy` inmediatamente antes del importe, se conserva esa cita exacta
para que la política real distinga actual de anterior. Nunca se usa anterior como
fallback. No se divide precio en este módulo.

Para la base se valida la leyenda seleccionada y su compatibilidad con la línea
del importe; leyendas contradictorias mantienen unknown. Si el caller pide un
subconjunto sin basis_evidence, solo se usa evidencia explícita de la propia línea.
Las referencias de base pueden apuntar a otra línea o condición vinculada de página.

Cantidad numérica `6` se cita junto a su contexto local `6 botellas` o `pack x6`
si existe. Se rechaza convertir mínimo/contenido en cantidad comercial. Coincidencias
de spans entre cantidad comercial, contenido y mínimo quedan pendientes. Compra mínima
conserva valor/unidad/alcance/referencias; unidad ausente no se inventa.

Todo OfferInterpretation sale con identidad/precio pending. La política confirma
por separado y el backend persiste. El ensamblador no llama visión ni cambia B a A.

## Validación, errores y métricas

- HTTP stdlib, texto/JSON, endpoint fijo, modelo semver fijo; sin SDK/dependencias
  nuevos, retries ni redirects ocultos.
- Modelo exacto, envelope, preguntas desconocidas, claves JSON duplicadas,
  opciones cerradas y pertenencia de refs. Pregunta faltante conserva las demás
  respuestas independientes válidas; no se completa con defaults comerciales.
- Probabilidades numéricas finitas 0..1, claves exactas y suma con tolerancia
  **0.001**, alineada al contrato compartido. Choice debe tener probabilidad máxima,
  admitiendo empates. Confidence se registra separada, sin exigir igualdad.
- Usage ausente/inválido = desconocido, nunca 0. Todo intento HTTP produce call
  con ID único, fase, modelo, versiones, duración, estado y error seguro. Incluso
  respuesta inválida/refusal conserva usage cuando puede parsearse. Refusal no
  documentado como envelope Choice se registra como error, sin decisiones.
- Respuestas parciales/probabilidades inválidas → call incomplete y motivos por
  pregunta. Timeout/HTTP/envelope inválido → call error. No se incluyen cuerpos,
  claves ni textos completos de excepciones en errores.
- Cotas de bytes/preguntas/llamadas y espera del semáforo; resultados fuera del
  deadline se descartan. Agotamiento antes del HTTP no inventa un call pagado.
- Timeout de socket no equivale a cancelación dura de DNS/SO: el deadline limita
  aceptación y espera, pero no garantiza abortar inmediatamente un bloqueo de SO.
  El integrador debe pasar el deadline absoluto de su presupuesto común, además
  de configurar el timeout de cada operación. No hay una cota exacta de tokens
  previa a la llamada: bytes/preguntas no se presentan como tokens facturados.

## Verificación real ejecutada (offline)

```powershell
.venv/Scripts/python.exe -m pytest tests/providers/test_jev_analysis.py tests/modules/procurement/test_block_interpretation.py tests/modules/procurement/test_analysis_contracts.py tests/providers/test_jev.py tests/modules/procurement/test_jev_pilot.py -q
```

Resultado final: **102 passed**, una advertencia preexistente de
Starlette/httpx; no se instaló una dependencia para resolverla.

```powershell
.venv/Scripts/python.exe -m ruff check app/providers/jev_analysis.py app/modules/procurement/block_interpretation.py tests/providers/test_jev_analysis.py tests/modules/procurement/test_block_interpretation.py
```

Resultado final: **All checks passed!** Se aplicó Ruff format solo a los cuatro
archivos Python de ownership. Una línea larga detectada durante implementación
se corrigió antes del check final.

Las pruebas cubren respuestas inválidas/parciales, abstenciones separadas, aliases,
tokens, deadlines, concurrencia con cuatro workers/cota dos, no retries, tenant,
IDs de oferta/bloque, candidatos alterados, mixed/other/uncertain, offsets y cifras
argentinas ($12.000, $1.234,56, 1,5L), variantes y precios anteriores/actuales.
Incluyen **política real**: base en otra línea o condición vinculada de página,
pack x6 seleccionado como número o cantidad, contenido x48 y mínimo 6 sin división,
contradicción de bases y mínimo seleccionado como pack mantienen pending.

## Dependencias y límites pendientes de integración

1. Backend integra callbacks publicados, conserva resultados/clasificaciones/calls
   en ledger y persiste todos los bloques, incluidos los que no producen fichas.
2. Backend pasa deadline común y método real; configurar clave no demuestra B listo.
3. Matching/política proporcionan candidatos compatibles del negocio y controlan
   feedback, confirmaciones, precio comparable y aceptación calibrada.
4. Parser es deliberadamente local/conservador. No resuelve tax_included/brand por
   generación; conserva esos textos para revisión. Currency `$` sigue siendo `$`,
   con motivo currency_pending; no se inventa ARS/USD. Nombres que requieran una
   composición no representada por spans existentes permanecen pendientes.
5. Hay más preguntas por bloque con varias apariciones. Al superar la cota se
   conserva pendiente; no se trunca el texto ni se ejecuta otro método silencioso.
6. No se evaluó fidelidad visual, recall de catálogo, precisión, costo ni ahorro
   con documentos reales. Integración API/frontend y evaluación A/B no se declaran
   completas como parte de esta entrega de componentes.

## Fuentes oficiales consultadas

- [TypeSafe API](https://docs.typesafe.ai/api): endpoint, criterios y answers por ID.
- [Choice](https://docs.typesafe.ai/primitives/choice): distribución, elección máxima
  y confidence distinta de probabilidad elegida.
- [Primitives](https://docs.typesafe.ai/primitives): independencia y contexto explícito;
  los IDs de preguntas no llegan al modelo.
- [Models](https://docs.typesafe.ai/models): texto/JSON y fijación de versión.

Las cotas locales y tolerancia 0.001 son decisiones del contrato del proyecto;
no se atribuyen a garantías de precisión o red del proveedor.
