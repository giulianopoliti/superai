# QA independiente y medición: implementación A/B

Fecha: 6 de octubre de 2026. Estado: avance ejecutable offline; cierre de integración
y evaluación humana pendientes. Skills aplicadas: stock-ai-quality-review y
stock-ai-architecture. Trabajo en checkout compartido, limitado al ownership QA.

## Hallazgos actuales accionables

### P1 — Moneda ausente se convierte en ARS y permite precio confirmado

Archivos: `app/modules/procurement/document_analysis.py:525` y
`app/modules/procurement/decision_policy.py:341` (snapshot de reanudación).
Reproducción offline con el helper `synthetic_offer()` de aceptación: currency=None,
pero la política devuelve price_status=confirmed y comparable_unit_price=2000.00.
Otra ejecución con FakeAssembler conserva currency=None en interpretación y proyecta
currency=ARS en la ficha mediante `offer.currency or "ARS"`. Impacto: evidencia
incompleta puede convertirse en una moneda comercial confirmada sin procedencia;
comparar importes de monedas diferentes o desconocidas puede ser incorrecto.
Fix propuesto: mantener motivo/estado de moneda pendiente y bloquear comparación
confirmada, o exigir una política explícita de moneda del negocio con procedencia
registrada antes de usar el default legacy. No completar silenciosamente la evidencia.

### Plazo: propagación corregida; cancelación dura fuera del cierre local

Archivo: `app/modules/procurement/document_analysis.py`, `AnalysisBudget.reserve`
y llamada `classify_block`; builder `app/main.py:114`.
El snapshot inicial solo transmitía tiempo restante al reader. Probe anterior con reloj monotónico falso: presupuesto 1 segundo,
clasificación que retorna a los 10 segundos, ficha todavía incorporada y duration=10.
El backend actualizado ahora asigna el deadline común a clasificador y proveedor
de decisión. La brecha de propagación ya no se mantiene como hallazgo abierto.
Una implementación falsa que ignora deadline no demuestra falla del cliente concreto.
Quedan fuera de esta aceptación la cancelación de sockets y una garantía de plazo
de pared sobre APIs reales; los límites del cliente se cubren en sus tests offline.
No prometer cancelación dura de HTTP: la prueba solo demuestra falta de propagación
y chequeo del plazo común, no un hang del proveedor real.

### Control transitorio de feedback comparable: última corrida verde

Archivo/prueba: `tests/modules/procurement/test_analysis_candidate_feedback.py:141`;
recorrido `app/modules/procurement/matching.py:_match_item_with_feedback` alrededor de :386.
Un control ampliado intermedio falló en `test_comparable_feedback_is_not_confirmed_identity`,
porque candidate.reasons es `[confirmed_by_feedback]` y ya no contiene
`feedback_not_identity`. Impacto: la suite de componentes no está verde y las marcas
de alternativa comercial/identidad requieren reconciliación con el contrato nuevo.
La política V2 inspeccionada sí distingue `human_accepted_alternative`; esta falla
**no demuestra por sí sola** aceptación incorrecta de identidad en producción.
El dueño reconcilió esos motivos y el control ampliado posterior vuelve a pasar.
QA no modificó ese test ni runtime del otro agente. El resultado intermedio queda
registrado para no usar un verde antiguo como evidencia durante cambios concurrentes.

## Hallazgos iniciales: corrección verificada durante integración

Los siguientes párrafos registran el primer snapshot. En la reanudación, el backend
ya corrigió ambos: `test_summary_with_identity_pending_is_partial` pasa y el test
de excepción de clasificador verifica call de error ligado a run/reading, usage=None.
No se mantienen como bugs abiertos en el snapshot más reciente.

### P1 — Resumen completed con decisiones todavía pendientes

Archivo: `app/modules/procurement/document_analysis.py:398` (snapshot inspeccionado).
El cálculo de status considera bloques/páginas/evidencia inválida/presupuesto,
pero omite las revisiones de identidad y precio de ofertas utilizables. Un proveedor
falso con una oferta sin producto produjo `status=completed` y
`identity_review_offer_count=1`. Impacto: consumidores pueden presentar análisis
completo aunque siga requiriendo decisión humana. Fix propuesto al dueño: incluir
los contadores de revisión en el estado parcial, o definir un estado separado de
lectura terminada que la UI no confunda con decisiones completas; probar identidad
pendiente y precio pendiente por separado.

### P1 — Excepciones de JEV pueden quedar fuera del registro de llamadas

Archivo: `app/modules/procurement/document_analysis.py:267-272`; recorrido de
decisión análogo alrededor de `:300`. Se reserva presupuesto, pero si el componente
lanza excepción antes de retornar `.calls`, solo se agrega un reason code. Reproducción
con FakeClassifier que lanza TimeoutError: una invocación del clasificador y ledger
con **solo fake-openai**, sin intento JEV. Impacto: métricas/plazos/costo de fallos
pueden estar incompletos y no parecerlo. Fix propuesto: colector de intentos o
resultado obligatorio con call_id/status/duración/usage nullable que sobreviva al
fallo; registrar el intento de orquestación sin atribuir HTTP facturable cuando no
se sabe si el transporte comenzó. Verificar binding run/reading/método de calls
antes de agregarlos. El adaptador JEV concreto ya devuelve calls para un TimeoutError
de transporte: la aceptación cubre ese caso correcto, pero no elimina el agujero
de excepciones en el límite de componente.

Los hallazgos iniciales se reprodujeron ejecutando offline los fakes de la nueva suite mediante
`runpy.run_path`; no se modificó runtime ajeno ni se agregó un skip/xfail para declarar
cerrada la funcionalidad. Son hallazgos del snapshot concurrente; el coordinador
debe confirmar su estado al integrar y agregar las regresiones de la corrección.

## Archivos cambiados

- `scripts/benchmark_procurement.py`: compatibilidad legacy, adaptador de ledger
  publicado, eventos locales V2, deduplicación por call_id, variantes de lectura,
  versiones, usage nullable, costo declarado/atribuido, bloques vigentes/históricos,
  estados y evaluación de identidad/precio independientes.
- `tests/scripts/test_benchmark_procurement.py`: regresiones del benchmark.
- `tests/fixtures/procurement_analysis/cases.json`: 24 casos **inventados**, sin
  etiquetas humanas ni documentos reales. `human_labeled=false` explícito.
- `tests/fixtures/procurement_analysis/telemetry.json`: captura de A/B **sintética**,
  con duración/tokens ficticios para verificar el parser; no permite inferir ahorro.
- `tests/integration/test_document_analysis_acceptance.py`: aceptación independiente
  de contratos, servicio y política disponibles; fakes de lectura/ensamblado y HTTP.
- `docs/ETAPA_0_LINEA_BASE.md`: uso nuevo, conservación de utilidad anterior y límites.
- Este informe: `docs/agents/QA_IMPLEMENTACION_DOS_METODOS.md`.

## Matriz de casos: qué se comprobó y qué sigue abierto

| Escenario del corpus | Comprobación ejecutada | Brecha de aceptación |
|---|---|---|
| Artículo sin precio / descuento sin importe | Servicio A/B conserva texto literal, pendiente y método sin precio ficticio; una lectura. API + SQLite aislado persiste PDF pendiente y conserva histórico en A/B | Extracción visual real; UI completa |
| Sin catálogo | Fakes preservan oferta; feedback/candidatos y tenant en controles del componente | Endpoint A/B y UI deben mostrar no vinculado |
| Pack x6 / envase x48 | Normalizador usa cantidad comercial explícita y no contenido; política exige evidencia | Interpretación real de fragmentos x48 en ambos métodos |
| Mínimo 2 bultos | Contrato conserva value/unit/scope/refs; política rechaza usar mínimo como cantidad del pack | Ensamblado y comparación API de ese documento |
| Actual/tachado | Escenario y valores esperados registrados | Aserción independiente del ensamblador con dos precios; no certificada aquí |
| Variantes en un bloque | Escenario con dos ofertas/un bloque | Recorrido real de separación y asociaciones, no certificado aquí |
| Página vacía real | Fake wire declara página sin bloques y no dispara relectura A/B | Lectura del PDF real |
| Ofertas repetidas | Servicio A/B mantiene IDs distintos y dos ofertas iguales | Asociación humana entre lecturas corregidas |
| Ilegibilidad | Texto pendiente se conserva; falta visual declarada consume como máximo una relectura | Fidelidad de transcripción; señales visuales reales |
| Referencias/IDs ajenos | Contrato rechaza condición ajena; política rechaza tenant ajeno; servicio no publica oferta de assembler ajeno | IDs de todas las capas y API de histórico |
| JEV parcial/falla/probabilidades inválidas | Cliente real con HTTP falso rechaza respuesta; servicio B + proveedor/assembler reales preserva texto y registra intento | Prueba de respuesta parcial con un eje válido y otro ausente al persistir |
| Feedback conflictivo | Controles disponibles de matching usan repositorio falso y preservan feedback conflictivo | Propagación completa del conflicto en respuesta A/B |
| Tenant | Política con candidato ajeno y servicio con oferta ajena no publican identidad; API/histórico rechaza tenant ajeno con SQLite aislado | Identidad JEV y restantes endpoints integrados |
| Relectura única | Registro común del servicio A/B hace initial+reread, conserva ambas versiones; benchmark cuenta solo bloque vigente | Rutas legacy/packs/API y relectura de una imagen cuyo texto cambia |
| Identidad/precio independientes | Política confirma identidad con precio pendiente; benchmark tiene accuracy separada y states vigentes | Gates comerciales y presentación en UI integrada |

No se compara la ejecución completa con los campos `expected` de cada escenario
por el solo hecho de cargar su JSON. Las brechas de la tabla evitan atribuir cobertura
a un fixture que todavía no tiene aserción de runtime correspondiente.

## Pruebas y medición ejecutadas

Comando del control disponible (sin red ni DB del negocio):

```powershell
.venv/Scripts/python.exe -m pytest tests/scripts/test_benchmark_procurement.py tests/integration/test_document_analysis_acceptance.py tests/providers/test_jev_analysis.py tests/providers/test_openai_document_extraction_v2.py tests/modules/procurement/test_analysis_contracts.py tests/modules/procurement/test_document_decision_policy.py tests/modules/procurement/test_analysis_candidate_feedback.py -q
```

Resultado: **174 passed** en el snapshot probado. Una advertencia existente de
Starlette/httpx en conftest. La selección incluye tests de otros agentes en solo
lectura; QA no los modificó. No se ejecutó la suite global ni una importación paga.
La suite QA propia contiene 29 pruebas de benchmark y 30 casos de aceptación
parametrizados. No hay skip/xfail ni tests que dependan de claves reales. La CLI
tiene un test `python -S` que confirma ejecución sin paquetes de terceros.

### Control final de reanudación (sustituye conteos anteriores)

La suite QA propia pasó **63 casos: 30 benchmark + 33 aceptación**. Se agregaron
API real + servicio real + SQLite en memoria con lectura/ensamblado falsos, para
ambos métodos y PDF generado por fixture; persiste documento sin fichas, GET conserva
summary/pendientes, otro tenant obtiene 404 y ninguna ruta ejecuta reviewer legacy.
Los GET no vuelven a invocar lector. También se agregaron regresiones de los dos
hallazgos iniciales corregidos y export directo V2 sin fila legacy fantasma.

```powershell
.venv/Scripts/python.exe -m pytest tests/scripts/test_benchmark_procurement.py tests/integration/test_document_analysis_acceptance.py -q
.venv/Scripts/python.exe -m pytest tests/scripts/test_benchmark_procurement.py tests/integration/test_document_analysis_acceptance.py tests/providers/test_jev_analysis.py tests/providers/test_openai_document_extraction_v2.py tests/providers/test_analysis_recovery.py tests/modules/procurement/test_analysis_contracts.py tests/modules/procurement/test_document_decision_policy.py tests/modules/procurement/test_analysis_candidate_feedback.py tests/modules/procurement/test_block_interpretation.py tests/test_document_analysis_api.py -q
.venv/Scripts/python.exe -m ruff check scripts/benchmark_procurement.py tests/scripts/test_benchmark_procurement.py tests/integration/test_document_analysis_acceptance.py
.venv/Scripts/python.exe -m ruff format --check scripts/benchmark_procurement.py tests/scripts/test_benchmark_procurement.py tests/integration/test_document_analysis_acceptance.py
```

Último resultado ampliado: **248 passed, 1 failed**, en
`test_comparable_feedback_is_not_confirmed_identity`, descrito arriba; una advertencia
Starlette/httpx. La corrida previa tuvo 245 passed, pero modificaciones concurrentes
posteriores impiden usar ese verde como cierre actual. Ruff limpio en ownership QA.
No se habilitó un skip ni se editó la regresión de otro agente para ocultar la falla.

### Cierre QA de conexión real de componentes (sustituye resultados intermedios)

Se añadieron cinco recorridos con `build_document_analysis_service` **real**, flag
true y claves falsas: A/B claro, A/B claro más bloque sin precio, B con timeout JEV.
Solo se reemplaza `OpenAISupplierOfferDocumentProvider.read` y el transporte HTTP;
no se sustituye servicio, builder, ensamblador, política, matching ni repositorio.
DB SQLite en memoria con un producto correcto y otro idéntico de tenant ajeno.
Fixture autouse fuerza flag false, claves ausentes, scheduler off y SessionLocal=None
como default; cada test integrado activa explícitamente flag true y la DB aislada.
Un transporte HTTP sin fake explícito falla inmediatamente.

Se verifica por POST: identidad y precio confirmados para la oferta clara,
producto/candidatos del tenant correcto, importe y base unit, oferta guardada,
pending sin importe ficticio, summary idéntico a metadata persistida, conteos acordes
al benchmark/ledger, histórico igual y 404 para tenant ajeno. GET capacidades e
histórico no invocan lectura/HTTP. A claro no llama JEV; B usa clasificación textual.
Reviewer legacy prohibido en todos esos recorridos. Falla B conserva texto/método,
call error ligado al run y usage desconocido, sin fallback A.

Backend ya publica `decision_audit`: el recorrido B comprueba preguntas/opciones,
IDs de bloque/run/reading, call_ids existentes de JEV, probabilidades, modelo,
confianza separada (fixture 0.99 frente a probabilidad 1), y persistencia íntegra
de la auditoría en SQLite. Timeout conserva preguntas, call_id y motivo de fallo.
La pérdida de auditoría ya no queda pendiente en este snapshot.

Regresiones independientes adicionales que pasan después de corrección backend:
la cita `10` de “Arroz 10% descuento por unidad” no confirma precio absoluto;
relectura con precio ausente no borra oferta válida previa; dos variantes A100/B200
seguidas por lectura A100 mantienen **ambas** sin duplicar A; pack confirmado x6
seguido por base unknown conserva pack/cantidad/precio y marca la lectura degradada
pendiente. El benchmark también rechaza retirar la versión útil por una nueva sin
precio. Conservación no demuestra calidad de lectura visual ni calibración JEV.

La suite propia ahora contiene **31 pruebas benchmark y 42 casos de aceptación**.
Ruff check/format limpio. La corrida ampliada de referencia con los mismos comandos
arriba volvió a verde (**291 passed**, una advertencia Starlette/httpx), antes de
añadir los últimos dos casos de variantes/pack, que pasaron en aceptación completa
con **42 passed**. No hubo cambios propios de runtime ni skips/xfails.
Resultado final ampliado ejecutado: **298 passed**, una advertencia Starlette/httpx,
3.25 s informados por pytest para esa selección. Ruff: **All checks passed**, tres
archivos ya formateados. Las cifras intermedias anteriores son historia del checkout
compartido, no bugs activos. Ese tiempo es duración de tests, no latencia de modelos.

Ruff check y format se ejecutan únicamente sobre los tres Python del ownership QA.
El benchmark se ejecutó sobre la captura sintética de telemetry con procedencia
explícita. A contiene 100 tokens input (40 cached ya incluidos) y 20 output
(5 reasoning ya incluidos), total 120; B contiene dos calls, uno con usage ausente.
Estos números son **valores de fixture**, no mediciones de modelos reales. A tiene
dos bloques vigentes y una versión histórica; sus dos candidatos no aumentan el
denominador de la evaluación. No hay costo disponible en esa captura.

## Dependencias y cierre pendiente

1. Revisión del coordinador e integración estable de runtime/API/UI/persistencia;
   resolver o documentar explícitamente moneda ausente. Plazo, feedback, auditoría,
   porcentajes y pérdidas de relectura recibieron correcciones y pruebas offline.
2. Verificación final del coordinador sobre todos los recorridos/API/UI. Ya se
   probaron aislamiento, parciales, pendientes, ledger/histórico y ausencia de revisor
   legacy con builder real. Restan documentos históricos variados y visualización
   completa de pendientes/decisiones en UI contra esa integración.
3. Corpus humano etiquetado, hash por documento y grupo/split reservado; ofertas
   omitidas incluidas y adjudicación de identidad/base/cantidad/mínimo/contenido.
4. Capturas canónicas por método/run, versión de código/prompt/schema/política/modelo,
   catálogo/feedback congelados y TODOS los intentos con usage real cuando exista.
5. Billing atribuible o tarifa identificada por fecha/modelo/componente; separar
   costo observado, estimado y desconocido antes de calcular comparación.

No se instalaron dependencias, crearon migraciones, usaron DB del negocio, enviaron
mensajes a otros chats ni crearon agentes. Gemini sigue suspendido. Resultado local
ejecutable no equivale a integración de producto cerrada ni a ahorro/precisión/latencia
real. La línea base humana continúa abierta.
