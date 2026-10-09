# Orquestación de agentes: dos métodos de extracción

Fecha: 6 de octubre de 2026.

Estado: componentes y recorrido A/B integrados y verificados localmente. Los seis
chats existentes entregaron implementación y pruebas; el coordinador revisó e
integró sus entregas. Se corrigieron pérdidas de variantes en relecturas, citas
de porcentaje confundidas con precio, auditoría JEV ausente y regresiones de
matching legacy. La evaluación de precisión, omisiones, costo y latencia con
documentos reales continúa pendiente.

El flujo actual está descrito en
[EXTRACCION_OPENAI_Y_JEV.md](EXTRACCION_OPENAI_Y_JEV.md). Las secciones de diseño
y reparto siguientes conservan la planificación y hallazgos originales; su lenguaje
de propuesta no indica que los componentes sigan pendientes. El cierre local al
final de este documento prevalece sobre estados intermedios de los informes.

Plan funcional: [PLAN_DOS_METODOS_EXTRACCION.md](PLAN_DOS_METODOS_EXTRACCION.md).

## Responsables

### Ejecución en los chats existentes

Esta distribución reemplaza el ownership de implementación anterior: el agente
principal coordina y revisa; el chat de backend es el único dueño de contratos
compartidos y su integración. Todos trabajan en el mismo checkout y preservan
los cambios locales anteriores.

| Chat / ID | Rol y archivos exclusivos |
|---|---|
| Etapa 0 — QA y línea base OpenAI/JEV / `01a10e8a-96af-7fa0-8298-32fbe5e93416` | QA y medición: `scripts/benchmark_procurement.py`, `tests/scripts/test_benchmark_procurement.py`, fixtures bajo `tests/fixtures/procurement_analysis/`, pruebas nuevas `tests/integration/test_document_analysis_acceptance.py`, `docs/ETAPA_0_LINEA_BASE.md`, `docs/agents/QA_IMPLEMENTACION_DOS_METODOS.md` |
| Etapa 1 — Extracción y evidencia estructurada / `01a10e8a-9bfe-7e72-840f-b7e97a3b738c` | Lectores OpenAI: `app/providers/documents/openai.py`, `base.py`, prompts nuevos `supplier_offer_structured_v2.md` / `supplier_offer_blocks_v1.md`, `tests/providers/test_openai_document_extraction_v2.py`, `docs/agents/OPENAI_IMPLEMENTACION_DOS_METODOS.md` |
| Etapa 2 — Reglas y candidatos del catálogo / `01a10e8a-a0f3-7b13-8667-0dcac286031e` | Reglas: `matching.py`, `identity.py`, nuevo `decision_policy.py`, pruebas nuevas de política/feedback y `docs/agents/POLITICA_IMPLEMENTACION_DOS_METODOS.md` |
| Etapa 3 — JEV selectivo y evaluación… / `01a10e8a-a927-7291-bd52-4f65ffff6476` | JEV: nuevo `app/providers/jev_analysis.py`, nuevo `block_interpretation.py`, prompts JEV nuevos, pruebas nuevas propias y `docs/agents/JEV_IMPLEMENTACION_DOS_METODOS.md`; conservar `jev.py` legacy |
| Etapa 4 — Orquestación y relectura puntual / `01a10e8a-afde-7b00-988a-98f81c02bf29` | Backend: `schemas.py`, nuevo `analysis_contracts.py`, `supplier_offers.py`, `main.py`, `settings.py`, `.env.example`, `providers/documents/dual.py`, nuevo servicio de análisis, pruebas nuevas de contratos/API/recuperación, `docs/CONTRATOS_DOS_METODOS.md`, `docs/agents/BACKEND_IMPLEMENTACION_DOS_METODOS.md` |
| Etapa 5 — Frontend: selector OpenAI/JEV / `01a10e8a-b949-70b2-a17a-4a1903dd360a` | UI: `app/static/index.html`, `app.js`, `styles.css`, pruebas nuevas UI y `docs/agents/FRONTEND_IMPLEMENTACION_DOS_METODOS.md` |

La etapa 4 publica primero contratos ejecutables y `docs/CONTRATOS_DOS_METODOS.md`.
Hasta que estén disponibles, los demás avanzan sobre prompts, fixtures, revisión y
partes independientes: no inventan esquemas alternativos ni editan contratos ajenos.
Cada chat entrega archivos cambiados, pruebas reales y dependencias pendientes.
El coordinador activa seguimientos sobre los mismos chats al cerrar dependencias.

Activación comprobada: se enviaron instrucciones de implementación a los seis chats
y una consulta conjunta de estado confirmó sus seis turnos `inProgress`. Backend
comenzó contratos; OpenAI y JEV preparan prompts/transporte; reglas comenzó fixes
independientes; frontend y QA revisan contratos y preparan sus entregas. Esta
comprobación acredita inicio, no pruebas completadas ni integración finalizada.

| Responsable | Preparación iniciada | Responsabilidad al implementar |
|---|---|---|
| Coordinador principal | Contratos, dependencias y revisión de entregas | API, esquemas comunes, integración, persistencia y verificación final |
| `openai_extraction` | Auditar los lectores y diseñar evidencia A/B | OpenAI con campos separados; OpenAI con bloques sin clasificar; prompts y pruebas del lector |
| `jev_decisions` | Auditar JEV, matching y reglas de precio | Clasificar fragmentos B y elegir candidatos/base del precio; validación y abstención |
| `frontend_methods` | Auditar formularios, resultados e historial | Elegir método, mostrar disponibilidad y conservar pendientes e historial |
| Revisor de calidad, en una segunda ronda | Reutilizar un agente con una tarea nueva después de cerrar su entrega | Revisar una parte que no haya implementado; reportar fallas al coordinador |

No crear infraestructura ni elegir otros modelos de agentes para esta división.
Los agentes de desarrollo son distintos de OpenAI y JEV usados por el producto.

## Primera ronda: diseño basado en el código real

Los tres agentes trabajan simultáneamente en revisiones acotadas. Cada uno escribe
un único informe independiente bajo `docs/agents/`; el coordinador integra las
decisiones y corrige inconsistencias antes de asignar código compartido.

Entregas:

- [OpenAI](agents/OPENAI_DOS_METODOS_HANDOFF.md).
- [JEV](agents/JEV_DOS_METODOS_HANDOFF.md).
- [Frontend](agents/FRONTEND_DOS_METODOS_HANDOFF.md).

Esta ronda cumple la división y preparación solicitadas. La implementación se
organiza en las rondas siguientes; no se confunde una propuesta revisada con una
funcionalidad terminada.

## Contratos comunes para la implementación

### Selección y disponibilidad

- Multipart existente: añadir `analysis_method`; valores `openai_structured`
  (default) y `openai_blocks_jev`.
- Conservar `extraction_provider` legacy. Gemini permanece suspendido.
- Capacidad propuesta: `GET /procurement/supplier-offers/analysis-methods`.
  Declararla antes de rutas que puedan capturar `analysis-methods` como un ID.
- Disponibilidad = implementación integrada + configuración de proveedores.
  Una clave de JEV no basta para anunciar B como operativo.
- Método inválido o B indisponible: rechazar antes de lectura paga.
- Mantener `import_result`, `comparison` y `extraction_warnings`; añadir un resumen
  `analysis` y `pending_blocks`. Son contratos propuestos, aún no endpoints activos.
- Histórico: guardar método realmente ejecutado en metadata del documento;
  documentos anteriores sin ese dato muestran «Método no registrado».
- El resumen usa `run_id`, `method`, `status`, conteos separados de bloques,
  ofertas y revisiones, duración/usage/costo nullable. Persistir la misma fuente
  bajo `document.metadata.analysis`. Los IDs de evidencia son opacos para la UI;
  no extraer página o intento dividiendo strings.
- Método desconocido: 422. Método conocido indisponible: 503, con `detail` español
  compatible con el frontend existente. HTTP 200 solo indica resultado guardado;
  UI consulta estado y pendientes para decidir «Guardado» o «Revisar».

### Lectura y conservación

- Un wire V2 de lectura separado de los esquemas legacy. A/B comparten páginas,
  bloques y fragmentos ordenados. A añade propuestas de campos con referencias;
  B entrega texto para clasificar. Ausencia de datos se expresa con `null`.
- Backend asigna IDs por ejecución, página, intento y orden. Una relectura es
  otra versión: conservar ambas y validar asociaciones; no unir por nombre solo.
- Backend recompone el texto canónico a partir de los fragmentos recibidos,
  conservando su orden y formato de unión. Esto no acredita fidelidad perfecta
  de la lectura al PDF; la cobertura se evalúa aparte.
- Campo interno único: `full_text`; la respuesta pública lo proyecta como `text`.
  El `raw_text` legacy de documento no se convierte en otro nombre para el bloque.
  Inicialmente las condiciones aplicables son referencias explícitas a fragmentos
  de la misma página. Condiciones de alcance desconocido se conservan pendientes;
  no implementar propagación automática a todo el documento en esta entrega.
- JEV selecciona categorías y candidatos existentes del mismo bloque; no genera
  precios/cantidades ni escribe en DB. Referencias y números se validan en backend.
- Mantener `offer_price` obligatorio en la ficha comercial actual. Guardar bloques
  sin precio/nombre utilizable en evidencia pendiente, sin importes ficticios.
- Toda evidencia válida recibida termina asociada a fichas o pendientes explícitos.
  La evidencia inválida también se conserva con motivo; no se presenta como ficha.
- Resumen cuenta bloques vigentes, no todas sus versiones históricas. Una versión
  anterior sale del conteo vigente solo con supersesión/asociación validada; si la
  relectura resolvió un pendiente, ese pendiente no se vuelve a contar. Si no se
  puede demostrar la asociación, mantener el conflicto pendiente y señalar la
  incertidumbre de cobertura, sin deduplicar por nombre.
- Metadata puede ser el soporte inicial: verificar límites, lectura histórica,
  volumen y persistencia de documento sin fichas antes de adoptarla. No crear
  migraciones para resolverlo por conveniencia.

### Política compartida

- Estados independientes de identidad y base del precio. Elegir producto no
  confirma si el precio es por unidad o pack.
- Selección pack: `unit`, `pack` o `unknown`, con referencia a evidencia.
  Cantidad comercial, contenido del envase y compra mínima son campos distintos.
- Compra mínima necesita `value`, `unit`, `scope` y referencias: «2 bultos» no
  puede convertirse en un número 2 sin contexto. Ese objeto reemplaza la propuesta
  numérica aislada de los informes. Unidad/alcance inciertos quedan pendientes.
- Catálogo recuperado únicamente para `business_id`; candidatos internos múltiples
  aunque la respuesta visible solicite uno. Revalidar feedback y presentación.
- Confianza sola no autoriza división de precio ni recomendación automática.
- `none` (ningún candidato) e `insufficient` (evidencia insuficiente) permanecen
  distintos; error de proveedor es un tercer estado. Validar preguntas/opciones,
  probabilidades y referencias antes de usar las respuestas de JEV.
- Texto completo pero decisión incierta: revisión humana. Información visual
  faltante/contradictoria recuperable: habilitar recuperación bajo presupuesto.
- Un registro común por ejecución limita a una relectura adicional por página;
  incluye recuperación del lector y dudas de packs. Retirar el camino paralelo
  de revisión visual al activar esa política, sin apagarlo antes de reemplazarlo.
- Registrar llamadas, duración y usage por intento, también los descartados.
  Usage ausente es desconocido, no cero; registrar costo solo con tarifa identificada.
- Falla JEV después de leer: conservar texto y pendientes, sin pasar silenciosamente
  de B a A. No releer imágenes para arreglar una caída de JEV.
- Sacar trabajo bloqueante prolongado del endpoint async preservando el alcance de
  sesiones/repositorios; no compartir sesiones DB entre workers concurrentes.

## Hallazgos integrados de la revisión paralela

1. **Conservación primero:** el esquema actual obliga precio y las promociones
   incompletas pueden quedar solo como warnings. Agregar ledger común A/B antes
   de construir B, con persistencia de documentos sin fichas utilizables.
2. **Cobertura de B:** su lectura inicial no produce `items`. Adaptar la decisión
   de recuperación a bloques y señales visuales; no usar `items=[]` ni warnings
   semánticos como motivo automático de otra llamada visual.
3. **Precio y producto separados:** matching puede recomendar compra por score
   aunque la identidad siga sin aceptar; feedback puede omitir revalidación de
   presentación. El coordinador debe resolver esos gates al conectar JEV.
4. **Evitar costos repetidos:** revisión visual de packs y recuperación de lectura
   tienen caminos separados. La integración reemplaza esa separación con el ledger
   común antes de describir el sistema como «una sola relectura».
5. **Dos cargas coherentes:** frontend captura ambos archivos, nombres, negocio y
   método antes del primer envío. Bloquea operaciones simultáneas desde ambos
   formularios y conserva enlace al primero si falla el segundo.
6. **Pendientes visibles:** los mapas de mejores precios excluyen ofertas sin
   vínculo/precio comparable. El panel de pendientes y sus conteos deben provenir
   del ledger, también en historial y comparación de dos proveedores.
7. **Disponibilidad honesta:** el selector de B depende de integración y claves,
   no solo de configuración. Las etiquetas de Gemini solo siguen en documentos
   históricos que realmente tuvieron ambos lectores.

## Orden de las rondas de código

| Ronda | Trabajo simultáneo | Condición para avanzar |
|---|---|---|
| 0. Preparación actual | Tres auditorías; coordinador define contrato y mapa de archivos | Informes revisados y decisiones comunes coherentes |
| 1. Base común | Coordinador añade esquemas/evidencia/métricas; agentes preparan fixtures/prompt/UI con contratos congelados | Compatibilidad legacy y pendientes sin precio verificadas |
| 2. Componentes | OpenAI implementa lectores; JEV implementa servicios con fakes del lector; frontend implementa selector contra contrato de API | Pruebas de cada componente sin red y archivos sin colisión |
| 3. Integración | Coordinador conecta API, presupuesto de recuperación, catálogo y persistencia | A/B ejecutables y capacidades reflejan disponibilidad real |
| 4. Revisión cruzada | Revisor independiente revisa backend; agente JEV revisa conservación; agente OpenAI revisa UI | Fallas corregidas y pruebas completas del recorrido |
| 5. Evaluación real | Corpus etiquetado, comparación A/B y medición | Informe real antes de afirmar ahorro o cambiar default |

Las dependencias se prueban con proveedores falsos mientras otra parte se construye.
Frontend puede avanzar con fixtures; B solo se habilita cuando la ruta real funciona.
La comparación de dos PDF conserva una misma selección; no ejecuta A y B automáticamente.

## Ownership de código: quién modifica qué

| Responsable | Archivos exclusivos en la ronda de implementación |
|---|---|
| Backend, delegado por coordinador | `app/main.py`, `app/settings.py`, `app/modules/procurement/schemas.py`, `app/modules/procurement/analysis_contracts.py`, `app/modules/procurement/supplier_offers.py`, `app/providers/documents/dual.py`, servicio de análisis nuevo y pruebas API/recuperación propias |
| OpenAI | `app/providers/documents/openai.py`, `app/providers/documents/base.py`, prompts exclusivos de lectura A/B y sus pruebas de proveedor |
| Reglas de negocio | `app/modules/procurement/matching.py`, `identity.py`, nuevo `decision_policy.py` y pruebas nuevas propias |
| JEV | Nuevo `app/providers/jev_analysis.py`, `block_interpretation.py`, prompts JEV y sus pruebas propias |
| Frontend | `app/static/index.html`, `app/static/app.js`, `app/static/styles.css`, verificaciones propias de UI |
| QA | Benchmark, fixtures comunes y pruebas de aceptación independientes; no modifica runtime de otros agentes |

`app/providers/jev.py`, `pack_review.py` y pruebas legacy se asignan explícitamente
al iniciar su modificación; un módulo nuevo puede evitar mezclar piloto existente
y flujo nuevo. Backend conserva el esquema común; reglas conserva matching; el
coordinador revisa e integra las entregas sin editar sus archivos en paralelo.

Si un agente necesita cambiar un archivo de otro, entrega una propuesta al dueño.
El coordinador revisa entregas y pruebas antes de integrar. No sobrescribir trabajo
previo del repositorio ni ejecutar instalaciones, migraciones o escrituras en la DB
del negocio para esta preparación.

## Criterios de aceptación y control del cierre

- Ambos métodos entregan el mismo contrato final y guardan texto y método ejecutado.
- Ofertas sin coincidencia y bloques sin precio permanecen visibles.
- Casos de precio tachado, promo sin precio, envase x48, pack x6 y compra mínima
  no se mezclan ni generan divisiones no confirmadas.
- JEV parcial, respuesta inválida y proveedor caído conservan evidencia pendiente.
- Recuperación no vuelve a facturar la misma página desde otra ruta de revisión.
- Pruebas de API y repositorios aislados verifican default, capacidad B, rechazo
  temprano, histórico, cobertura, múltiples candidatos y aislamiento por negocio.
- Verificación UI: elección por teclado, captura de ambas cargas al submit,
  pendientes, mensajes de resultado parcial y escape de texto extraído.
- Cierre local y evaluación real se reportan separados. No afirmar ahorro de costo,
  latencia o ausencia de omisiones con pruebas falsas o conteos del modelo.

## Referencias de proveedores

Los informes de agentes contienen propuestas de detalle. Ante diferencias de
nombres/alcances, las decisiones integradas en este documento prevalecen; los
esquemas ejecutables se congelan y verifican en la ronda 1.

OpenAI admite outputs sujetos a un esquema, pero el esquema no certifica los
valores comerciales: [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
JEV usa decisiones sobre entradas textuales y opciones cerradas:
[System One](https://docs.typesafe.ai/concepts/system-one) y
[Choice](https://docs.typesafe.ai/primitives/choice).

## Cierre local del coordinador — 6 de octubre de 2026

Los seis chats entregaron sus componentes y el coordinador verificó su integración.
Se reutilizaron los chats autorizados y sus modelos configurados. El backend
publica los contratos comunes; OpenAI, reglas y JEV se conectan mediante la factory
real, y el frontend consume esos contratos. Las dependencias de integración que
figuraban pendientes en informes intermedios quedaron resueltas.

La configuración local `.env.local` habilita `DOCUMENT_ANALYSIS_V2_ENABLED=true`.
Una construcción en proceso nuevo confirmó servicio disponible y capacidades
`openai_structured` y `openai_blocks_jev` disponibles. Esta comprobación no hizo
llamadas de extracción ni abrió sesiones de la DB del negocio. No se modificaron
claves o modelos. Los procesos abiertos necesitan volver a cargar la configuración.

Verificación conjunta final:

- `.venv/Scripts/python.exe -m pytest -q`: **549 passed**, una advertencia
  preexistente Starlette/httpx. Proveedores externos simulados y DB SQLite aislada.
- `node --test tests/frontend/analysis_ui.test.cjs`: **9 passed**.
- Ruff sobre todos los archivos Python del cambio y pruebas nuevas: sin errores.
- `git diff --check` sobre archivos existentes afectados: sin errores de whitespace.
- La verificación de navegador del agente frontend comprobó selector, teclado,
  escape de evidencia y ancho móvil de 320 px contra fixtures; no equivale a probar
  una extracción paga desde el navegador.

Correcciones finales incorporadas: conservación de todas las variantes ante
relecturas degradadas; precio porcentual/anterior y citas truncadas; clasificación
de base de precio con contexto; auditoría de preguntas/opciones/probabilidades;
catálogo recuperado por lote; lecturas iniciales concurrentes limitadas a dos;
política de identidad/precio centralizada en matching, también en recomparaciones
y correcciones humanas; monedas distintas de ARS quedan en revisión.
El coordinador retiró el gate duplicado que sólo se ejecutaba al cargar documentos
y añadió aislamiento del flag local en `tests/conftest.py`.

### Hallazgo previo del catálogo, fuera de esta implementación

Una corrida intermedia terminó con 547 aprobadas y un fallo en
`test_catalog_sync_reports_latest_load_and_only_changes_modified_products`.
La prueba pasó al ejecutarla aislada y la corrida final completa quedó verde.
El origen se reprodujo por separado en SQLite: dos importaciones con el mismo
`completed_at` se desempatan por ID descendente; un UUID no representa el orden
de carga. El repositorio puede mostrar la anterior como última carga del catálogo.
No se modificó esa prueba para ocultar el fallo ni el repositorio previo: queda
registrado como bug intermitente del catálogo, independiente del análisis A/B.
La corrida verde no demuestra que ese bug esté corregido.

### Lo que continúa pendiente

Evaluar documentos reales con etiquetas humanas y registrar usage/billing para
comparar precisión, omisiones, costo y latencia. La aceptación por probabilidades
JEV sin validación determinista sigue desactivada. No se implementó un visor de
toda su auditoría ni un editor detallado de bloques pendientes. La persistencia
usa commits existentes, sin transacción global ni clave idempotente para un nuevo
POST; un guardado interrumpido requiere revisión. No hubo instalaciones,
migraciones, commits, llamadas pagas ni escrituras en la DB del negocio.

Flujo vigente: [EXTRACCION_OPENAI_Y_JEV.md](EXTRACCION_OPENAI_Y_JEV.md).
