# Implementación de reglas y candidatos comunes A/B

Fecha: 6 de octubre de 2026.
Estado: componentes implementados y pruebas offline aprobadas; backend ya integró
recuperación y política. Activación y evaluación real pertenecen a backend/QA;
este informe no las certifica.

## Archivos cambiados por este agente

- `app/modules/procurement/matching.py` — extender cambios locales existentes.
- `app/modules/procurement/identity.py` — extender archivo local existente.
- `app/modules/procurement/decision_policy.py` — nuevo.
- `tests/modules/procurement/test_analysis_candidate_feedback.py` — nuevo.
- `tests/modules/procurement/test_document_decision_policy.py` — nuevo.
- `docs/agents/POLITICA_IMPLEMENTACION_DOS_METODOS.md` — nuevo.

No editar schemas, proveedores, orquestador, repositorios, pricing, pack_review ni
tests legacy/API. Skills aplicadas: stock-ai-backend-core, stock-ai-quality-review
y stock-ai-architecture. No instalaciones, migraciones, llamadas pagas, escrituras
en DB del negocio, commits, reset, nuevos chats, subagentes ni mensajes a otros chats.

## Firmas integrables (fuente ejecutable)

Todos los DTOs nuevos provienen de `analysis_contracts.py`; no hay modelos paralelos.

```python
ProductMatchService.retrieve_analysis_candidates(
    *, business_id: str, supplier_id: str,
    offers: list[OfferInterpretation], internal_top_k: int = 5,
    feedback_conflicts: dict[str, list[str]] | None = None,
) -> dict[str, list[IdentityCandidate]]

build_offer_decision_request(
    *, business_id: str, block: OfferTextBlock, offer: OfferInterpretation,
    candidates: list[IdentityCandidate],
    policy: DocumentDecisionPolicy | None = None,
) -> OfferDecisionRequest

DocumentDecisionPolicy(*, thresholds: DecisionThresholds | None = None)
DocumentDecisionPolicy.prepare_request(
    *, request: OfferDecisionRequest,
) -> OfferDecisionRequest
DocumentDecisionPolicy.validate_decision(
    *, request: OfferDecisionRequest, decision: OfferDecisionResult | None,
) -> PolicyResult

comparable_unit_price(
    *, offer: OfferInterpretation, block: OfferTextBlock,
) -> Decimal | None

observed_offer_price(
    *, offer: OfferInterpretation, block: OfferTextBlock,
) -> Decimal | None
published_price_evidence_valid(
    *, offer: OfferInterpretation, block: OfferTextBlock,
) -> bool
```

Recuperación V2 recibe interpretaciones provisionales, incluso sin precio. Consulta
catálogo una vez y feedback una vez por lote del mismo proveedor; no crear un
SupplierOfferItem con cero para poder comparar. Devuelve hasta cinco candidatos por
oferta, sin modificar el default público `max_candidates_per_item=1`. Conserva claves
de ofertas sin coincidencia con listas vacías; IDs repetidos se rechazan. Ofertas
repetidas con IDs distintos se conservan. Scoring y ranking no usan precios ni costos.

Pasar un `feedback_conflicts={}` por ejecución y adjuntar sus códigos a la
interpretación antes de construir el request, especialmente
`feedback_product_unavailable`: una aceptación cuyo producto ya no está disponible
no debe sustituirse por el ganador fuzzy. No modificar ni borrar la regla original.

Existe además `retrieve_identity_candidates(*, business_id, offer_items,
internal_top_k=5) -> list[ProductMatchCandidate]` para ítems legacy/provisionales con
precio real. `retrieve_pilot_candidates` reutiliza ese recorrido. No persistir
automáticamente los cinco candidatos internos en el reporte público.

El builder emite preguntas `{offer_id}:identity` con IDs + `none`/`insufficient` y
`{offer_id}:price_basis` con `unit`/`pack`/`unknown`, únicamente cuando hacen falta.
Resultados claros, falta de cantidad con base pack explícita, conflictos humanos y
faltantes visuales no generan preguntas innecesarias. Si `questions=[]`, no llamar JEV.
El caller conserva el request exacto enviado y lo entrega a `validate_decision`.

## Reglas implementadas

- Feedback ordenado por fecha, validado por proveedor/nombre y productos del tenant.
  Rechazos vigentes excluyen productos; aceptaciones incompatibles conservan el
  candidato y razones de conflicto, sin reemplazo silencioso. Varias aceptaciones
  quedan pendientes. Marca/variante/presentación explícitamente contradictorias,
  producto inactivo y campos de tamaño inconsistentes bloquean identidad automática.
- Corrección humana compatible recalcula costos y conserva `auto_accepted`, incluso
  con score bajo de la sugerencia anterior. La señal `corrected_by_user` proviene del
  servicio de revisión existente; no es un dato confiable a aceptar desde un modelo.
- Una alternativa comparable aceptada por una persona sigue siendo una relación
  válida: puede mostrar comparación informativa compatible, pero no es identidad
  exacta y su recomendación automática permanece `review`. Diferentes marcas pueden
  ser una alternativa legítima; presentación incompatible sigue vetando comparación.
  V2 la distingue con `human_accepted_alternative`.
- Identidad pendiente no produce `buy` ni `do_not_buy`. Se limpian cálculos anteriores
  al recalcular para que un conflicto no reutilice ahorros viejos.
- Un sufijo comercial terminal ` X6` se separa de identidad solo con pack/cantidad
  validados y tamaño minorista explícito compatible. En legacy, PACK es el contrato
  explícito de base; en V2 la política comprueba precio/base/cantidad por evidencia.
  `unknown`, contenido x48 y compra mínima no habilitan ese recorte.
- Identidad y precio mantienen estados independientes. `none`, `insufficient`, error,
  respuesta inválida y preguntas faltantes tienen códigos distintos. Las respuestas
  inválidas de JEV no eliminan resultados deterministas claros.
- Referencias exactas de este bloque o condiciones explícitamente referidas de la
  misma página; offsets corresponden al literal, citas sin offsets son únicas. No
  usar referencias ajenas, alteradas, parciales o ilegibles para confirmar campos.
- Nombre no respaldado se vuelve `None`; presentación no respaldada se vuelve
  `unit_size=None, unit=None`. Importe positivo sin cita/valor consistente o que solo
  corresponde al precio anterior se vuelve `offer_price=None`. La propuesta original
  se conserva en el input/ledger: la política trabaja con una copia profunda.
- Precio observado válido con base pendiente conserva su importe y sale como
  `price_type=unknown`. Nunca convertirlo en ficha de precio confirmado por un default.
- Precio actual y anterior se distinguen por cita y contexto cercano al span: en
  `Antes $100 ahora $90 por unidad`, el span `$90` está respaldado por `ahora`;
  `$100` no. Coexistencia sin etiqueta suficiente queda pendiente.
- Las citas estrechas también validan límites/contexto originales: `10` dentro de
  `$100`, `2` dentro de `2.25L`, `10` en `10%`, contenido x48 y mínimo seis no son
  precios observados. Los helpers públicos de precio observado/validación booleana
  usan exactamente la misma regla y no requieren resolver la base del precio.
- Precio comparable se calcula con Decimal solo después de confirmar precio,
  base y cantidad comercial. Cantidades de contenido/mínimo, spans compartidos,
  decimales no enteros, `xN` aislado y probabilidades altas no habilitan división.
- Revisión visual únicamente por evidencia visual recuperable explícita/ilegible.
  Texto completo incierto, errores de proveedor y abstenciones derivan a humano.
  `visual_attempt_exhausted` impide solicitar otra recuperación. El ledger/orquestador
  debe aplicar el presupuesto real; esta política no ejecuta ni cuenta llamadas.

`DecisionThresholds` permite configurar probabilidad de identidad/precio, confianza,
margen y `allow_jev_acceptance`. Esta última es False por defecto. Los números
iniciales son controles para evaluación, no umbrales calibrados en catálogo argentino.
La política usa reglas conservadoras de evidencia; no resuelve toda expresión
semántica ambigua solo por una opción JEV muy probable.

## Verificación real ejecutada

Comando de regresión final (entorno existente; sin instalar):

```powershell
.venv/Scripts/python.exe -m pytest tests/modules/procurement/test_matching.py tests/modules/procurement/test_analysis_candidate_feedback.py tests/modules/procurement/test_document_decision_policy.py tests/modules/procurement/test_automatic_identity.py tests/modules/procurement/test_review_feedback.py tests/modules/procurement/test_pricing.py tests/modules/procurement/test_jev_pilot.py tests/test_procurement_api.py::test_procurement_api_corrects_match_and_recalculates_recommendation -q
```

Resultado final: **115 passed**, un warning preexistente de Starlette/httpx. Incluye suites
legacy de matching, identidad y feedback; la corrección API usa SQLite en memoria.
Las pruebas nuevas usan repositorio falso, DTOs y decisiones JEV falsas. Una prueba
combina el ensamblador B real con decisiones falsas y spans actual/anterior.

```powershell
.venv/Scripts/ruff.exe check app/modules/procurement/matching.py app/modules/procurement/identity.py app/modules/procurement/decision_policy.py tests/modules/procurement/test_document_decision_policy.py tests/modules/procurement/test_analysis_candidate_feedback.py
```

Resultado: **All checks passed**. Durante la implementación hubo fallos intermedios:
corrección humana, relación alternativa y sufijo de pack. Se corrigieron en ownership
propio; no se borraron ni modificaron tests legacy para ocultarlos. No quedan fallos
en el comando final indicado; no se ejecutó toda la suite del proyecto desde este chat.

## Dependencias integradas y límites pendientes

Las dependencias de backend de la primera entrega ya se integraron: `main.py` usa
`retrieve_analysis_candidates`; `document_analysis.py` prepara las preguntas mediante
`prepare_request`, usa `published_price_evidence_valid` y conserva evidencia/pendientes
bajo el registro común de recuperación. El informe de backend documenta sus pruebas
API, persistencia en SQLite de fixture y relecturas. No siguen bloqueando esta entrega.

El coordinador informó una suite global de 528 pruebas aprobadas y Ruff verde antes
del cierre de moneda siguiente; no es una ejecución realizada por este chat.
Activación, UI y evaluación de documentos reales siguen a cargo de los responsables.
Vetos de variantes y expresiones comerciales son conservadores y acotados; ampliar
con casos etiquetados. No se midieron precisión, cobertura, costo, ahorro ni calibración
real. No hay dependencias nuevas.

## Cierre adicional: comparación de moneda

El catálogo legacy no tiene moneda por costo/precio: se usa la convención ARS. En
`_with_commercials`, una moneda de oferta normalizada con strip/upper distinta de
ARS agrega `currency_not_comparable`, deja `recommendation=review` y limpia diferencia
de costo, porcentaje y margen. El gate precede heurísticas de packs y comparaciones;
preserva importe, moneda e identidad, sin conversiones ni cambios de esquema.

Se modificaron exclusivamente `matching.py`, `test_analysis_candidate_feedback.py`
y este informe. Las pruebas cubren USD 1 frente a costo ARS 100, moneda con espacios,
EUR/$ sin equivalencia asumida, borrado de cálculos previos, ausencia de llamada a
heurística de packs y continuidad del cálculo ARS.

```powershell
.venv/Scripts/python.exe -m pytest tests/modules/procurement/test_analysis_candidate_feedback.py tests/modules/procurement/test_matching.py tests/modules/procurement/test_automatic_identity.py tests/modules/procurement/test_review_feedback.py -q
.venv/Scripts/ruff.exe check app/modules/procurement/matching.py tests/modules/procurement/test_analysis_candidate_feedback.py
```

Resultado real: **53 passed**, una advertencia preexistente de Starlette/httpx;
Ruff: **All checks passed**. Sin instalaciones, migraciones, conversiones,
llamadas pagas ni DB del negocio. No se volvió a ejecutar la suite global desde este chat.

## Cierre adicional: gate V2 al recomparar

`_with_commercials` centraliza las decisiones de `metadata.analysis_v2.interpretation`
para que el servicio, recomparación y corrección no dependan de un gate exclusivo del
POST inicial. El coordinador retiró ese duplicado de `main.py`; este agente no editó main.

- Identidad pending/no_match, metadata V2 inválida o confirmación para otro producto:
  `auto_accepted=False`, recomendación review y cálculos vacíos. Una nueva coincidencia
  fuzzy o cambio del catálogo no resuelve esas decisiones comerciales por sí mismo.
- Identidad confirmada para este producto y compatible se conserva aunque el precio
  esté pendiente: `auto_accepted=True` representa únicamente identidad; no habilita
  ahorro ni margen si precio no está confirmado.
- Override humano: señales backend `corrected_by_user`/`confirmed_by_feedback`, relación
  de identidad exacta o mismo producto con otro nombre, sin vetos de tenant, presentación,
  marca/variante explícita ni múltiples aceptaciones. Permite reemplazar una decisión
  antigua de identidad; una alternativa comparable no se convierte en identidad exacta.
  Confirmar identidad humana no confirma base/cantidad/precio automáticamente.
- Excepción para precio pendiente: metadata existente pack_review confirmed, decisión
  validada por PackReviewDecision, confianza/evidencia presentes y base/cantidad iguales
  a los campos guardados. El importe positivo observado en interpretation debe coincidir
  con el importe de oferta. Un status suelto, decisión inválida o importe ausente no
  habilitan la comparación. No se inventó otro contrato de confirmación manual;
  una revisión manual debe actualizar decisiones validadas mediante su servicio dueño.
- Gates de presentación y moneda se mantienen. Los ítems sin metadata V2 siguen
  el recorrido legacy. Se limpian cálculos previos antes de evaluar cualquier gate.

Archivos de este cierre: matching.py, test_analysis_candidate_feedback.py y este informe.
Las nuevas pruebas comparan el mismo documento dos veces, cambian costos/catálogo,
mantienen no_match/pending, prueban otro producto confirmado, override compatible,
precio pendiente independiente, review inválida/válida y moneda/presentación vetadas.

```powershell
.venv/Scripts/python.exe -m pytest tests/modules/procurement/test_analysis_candidate_feedback.py tests/modules/procurement/test_matching.py tests/modules/procurement/test_automatic_identity.py tests/modules/procurement/test_review_feedback.py tests/test_document_analysis_api.py tests/test_procurement_api.py -q
.venv/Scripts/ruff.exe check app/modules/procurement/matching.py tests/modules/procurement/test_analysis_candidate_feedback.py
```

Resultado real final: **98 passed**, una advertencia preexistente Starlette/httpx;
Ruff **All checks passed**. Incluye API A/B y API procurement, con proveedores falsos
y SQLite de fixture. No se ejecutó una suite global desde este chat ni se hicieron
instalaciones, migraciones, llamadas pagas o escrituras en la DB del negocio.
