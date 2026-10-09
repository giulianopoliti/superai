# Plan de acción: extracción con OpenAI y decisiones selectivas con JEV

Fecha: 5 de octubre de 2026.
Estado: plan de implementación; la integración automática con JEV aún no existe.

Ampliación del 6 de octubre de 2026:
[PLAN_DOS_METODOS_EXTRACCION.md](PLAN_DOS_METODOS_EXTRACCION.md) define dos
estrategias seleccionables en el frontend existente. Ese documento reemplaza el
supuesto de un único método y autoriza planificar el selector solicitado por el
usuario. Las etapas y asignaciones siguientes son la base anterior; deben
coordinarse con las nuevas antes de activar implementación.

## 1. Objetivo y límites

Reducir costo total por documento y tiempo hasta obtener ofertas utilizables,
sin aumentar errores de identidad, precios por pack ni omisiones de ofertas.

Flujo objetivo: **OpenAI extrae → backend valida y recupera candidatos → JEV
interpreta ambigüedades → backend acepta o deriva → relectura visual puntual
cuando falta información → revisión humana de lo pendiente**.

- Gemini permanece suspendido.
- OpenAI conserva la responsabilidad de lectura visual; JEV recibe texto/JSON.
- Backend consulta DB, valida, calcula y persiste; los modelos no acceden a la DB.
- Identidad del producto y base del precio son decisiones independientes.
- Conservar evidencia, importe publicado y decisiones humanas anteriores.
- El catálogo no prueba si el precio de una oferta es por unidad o por bulto.
- Probabilidades de JEV y confianza de OpenAI no equivalen a verificación visual.
- Código, contratos y campos en inglés; mensajes operativos en español.
- Mantener aislamiento por `business_id`, proveedores detrás de interfaces y
  reglas deterministas en `app/modules/procurement/`.
- No crear frontend, dependencias o migraciones para las primeras etapas.

## 2. Punto de partida verificado

En la implementación actual, `auto` y `openai` usan un único lector OpenAI,
bloques de hasta dos páginas y dos bloques concurrentes. Los reintentos internos
del SDK están desactivados. Hay relecturas por página para recuperar fallos,
cobertura baja, numeración inválida o advertencias.

La revisión visual de packs agrupa hasta ocho productos de una página; todavía
se ejecuta antes de guardar las ofertas y comparar con el catálogo. El matching
es determinista. `JevHttpChooser` y el piloto `shadow` existen, pero JEV no participa
en la importación normal. La petición HTTP espera todo el proceso.

El log histórico de casi 28 minutos corresponde al flujo dual anterior. Sirve
para identificar problemas, pero la comparación de ahorro debe hacerse contra
el flujo OpenAI actual, sobre los mismos documentos y con calidad equivalente.

## 3. Política de decisiones propuesta

| Situación | Responsable y acción |
|---|---|
| Identidad confirmada previamente y aún compatible | Backend aplica feedback, sin JEV |
| Código exacto validado o identidad inequívoca bajo reglas existentes | Backend resuelve identidad |
| Varios productos plausibles, con datos suficientes | JEV elige entre candidatos, `none` o `insufficient` |
| No aparece un candidato correcto o plausible | Backend amplía recuperación una vez; después pendiente |
| Leyenda explícita y consistente sobre base del precio | Backend valida interpretación; sin segunda lectura obligatoria |
| Evidencia textual suficiente pero interpretación ambigua | JEV elige `unit`, `pack` o `unknown` |
| Precio por pack con cantidad explícita y válida | Backend calcula precio por unidad |
| Precio por pack con cantidad ausente o contradictoria | No dividir; conservar importe y dejar base pendiente |
| Leyenda o precio ilegible/ausente | Relectura visual acotada de la página |
| JEV incierto con evidencia completa | Pendiente humano; no releer automáticamente |
| JEV falla, devuelve esquema inválido o queda fuera de plazo | Mantener resultados claros; ambiguos pendientes |
| Relectura no resuelve o contradice una lectura previa | Guardar ambas evidencias y marcar revisión |

Resolver identidad no confirma la base del precio, ni viceversa. Los candidatos
pendientes no deben convertirse en compras ni en precios comparables confirmados.
Las preguntas de JEV sobre una oferta son independientes: la base del precio debe
referirse a la evidencia de esa oferta, no a un producto elegido en otra pregunta.

## 4. Etapas de implementación

### Etapa 0 — Línea base y conjunto de evaluación

Acciones:

1. Seleccionar documentos representativos: folletos con fotos, listas con texto,
   packs reales, compra mínima, contenido del envase, marcas/variantes similares
   y páginas poco legibles.
2. Etiquetar manualmente identidad, precio publicado, base del precio, cantidad
   comercial y evidencia/página. Incluir ofertas omitidas, no solo las extraídas.
3. Separar casos de ajuste y evaluación; evitar que páginas del mismo folleto
   aparezcan en ambos grupos. Reservar proveedores o formatos distintos.
4. Medir el flujo OpenAI actual: extracción, revisión de packs, matching y total.
   Registrar tokens reales, llamadas, errores, costo según tarifas identificadas
   y pendientes humanos. Distinguir costo observado de costo estimado.
5. Separar una evaluación con extracciones congeladas, que aísle el efecto de JEV,
   de una prueba completa, que incluya variación en la lectura de OpenAI.

Archivos: extender `scripts/run_jev_pilot.py`, `app/modules/procurement/jev_pilot.py`
y documentación de evaluación. No ejecutar llamadas pagas solo por crear este plan.

Cierre: referencia reproducible, etiquetas humanas y métricas comparables.

### Etapa 1 — Evidencia estructurada en la primera extracción

Acciones:

1. Extender los contratos de extracción con un objeto tipado `price_evidence`:
   `price_basis_text`, `commercial_quantity_text`, `commercial_quantity`,
   `minimum_purchase_text`, `minimum_purchase_quantity`, `retail_contents_text`,
   `retail_contents_count` y `legibility`.
2. Los textos son citas breves visibles, vinculadas a la oferta y página. Un campo
   ausente se representa explícitamente; no completar por conocimiento del catálogo.
3. Versionar el prompt para extraer evidencia sin explicaciones largas. Diferenciar
   cantidad comercial, contenido del envase y compra mínima.
4. Cambiar el default implícito `price_type=unit` por ausencia explícita de certeza
   en el contrato de lectura, conservando compatibilidad con documentos históricos.
5. Conservar evidencia y versión del prompt en metadata existente si el recorrido
   extracción → servicio → repositorio lo permite; verificar lectura posterior.
   No crear migración sin identificar primero una necesidad de almacenamiento.

Archivos: `app/modules/procurement/schemas.py`,
`app/providers/documents/base.py`, `app/providers/documents/openai.py`,
`app/assistant/prompts/supplier_offer_chunk_extractor.md` y
`app/modules/procurement/supplier_offers.py`.

Cierre: JSON validado, evidencia por oferta, compatibilidad de lectura histórica
y casos como toallitas x48 o compra mínima de seis sin divisiones erróneas.

### Etapa 2 — Servicio determinista de decisión y recuperación

Acciones:

1. Crear un servicio `decision_policy.py` que produzca rutas explícitas:
   `resolved`, `needs_jev`, `needs_visual_review`, `needs_human_review`.
2. Mantener estados separados para identidad y precio, con razones tipadas.
3. Recuperar inicialmente hasta cinco candidatos por oferta, respetando tenant,
   feedback humano y vetos de marca, variante y presentación.
4. Separar el número de candidatos internos del límite de candidatos mostrados
   o guardados: el default actual de un candidato no debe limitar las opciones de JEV.
5. Evitar JEV para identidades ya confirmadas y compatibles. No utilizar similitud
   de precio para decidir identidad ni historial de embalaje para imponer la base.
6. Preparar un registro provisional con ID estable de oferta, sin publicar decisiones
   comerciales hasta concluir su validación.

Archivos: nuevo `app/modules/procurement/decision_policy.py`,
`matching.py`, `identity.py`, `pack_review.py` y `pricing.py`.

Cierre: tabla de decisiones cubierta por pruebas; casos claros no hacen llamadas
a JEV; casos sin evidencia no se resuelven por probabilidades o regex de “xN”.

### Etapa 3 — Proveedor JEV selectivo y evaluación shadow

Acciones:

1. Extender `app/providers/jev.py` detrás de una interfaz de proveedor. Reutilizar
   validación HTTP y añadir respuestas tipadas para identidad y base del precio.
2. Versionar prompts separados para selección de producto y base del precio.
   Opciones: IDs + `none`/`insufficient`; `unit`/`pack`/`unknown`.
3. Enviar ambas preguntas en una solicitud cuando ambas estén pendientes; enviar
   solo la pregunta necesaria en los demás casos. No enviar el catálogo completo.
4. Si se agrupan ofertas, usar IDs estables, opciones por oferta y límites de tamaño;
   probar que no se mezclen candidatos, evidencia ni respuestas entre registros.
5. Validar versión fija del modelo, preguntas esperadas, IDs permitidos, probabilidades
   finitas entre 0 y 1 y suma compatible con 1. Registrar `probabilities` y
   `confidence` por separado; no reemplazar una por la otra.
6. Medir tiempo y usage. Acotar concurrencia, plazo y presupuesto de llamadas.
   No encadenar reintentos ilimitados ante un proveedor no disponible.
7. Ejecutar primero en `shadow`: guardar evaluación aislada, sin cambiar decisiones
   activas ni suprimir todavía el flujo vigente de revisión.

Archivos: `app/providers/jev.py`, nuevos contratos de proveedor y prompts
`jev_price_basis.md`, `jev_product_choice.md`, `app/settings.py`,
`.env.example`, piloto y pruebas de proveedor.

Cierre: respuestas inválidas no llegan a decisiones comerciales; informe comparativo
de precisión, cobertura, latencia y costo. No adoptar un umbral por intuición.

### Etapa 4 — Integración y relectura visual única por página

Acciones:

1. Extraer la orquestación del endpoint hacia un servicio de análisis de documentos,
   conservando el contrato público y las comprobaciones de `business_id`.
2. Ejecutar la política y JEV antes de la revisión visual de packs. Revisar visualmente
   solo información faltante o inconsistente que una nueva lectura pueda recuperar.
3. Unificar la recuperación de extracción y la revisión de packs en un registro de
   páginas: causas, intentos, evidencia recuperada y presupuesto consumido.
4. Releer una página una vez después de la lectura inicial, agrupando todas sus
   dudas conocidas. Reutilizar información recuperada en etapas posteriores; si
   aparece otra duda después de agotar el intento, marcar pendiente.
5. Limitar costo y tiempo por documento. Al alcanzar el límite, entregar resultados
   válidos parciales y pendientes explícitos, sin ocultar páginas incompletas.
6. Identificar oferta por documento/página/ID de extracción, no solo por nombre.
   Mantener ofertas repetidas y preservar lecturas contradictorias en auditoría.
7. Mantener el cálculo de precios, ahorros y recomendaciones en servicios existentes.

Archivos: `app/main.py`, nuevo servicio de análisis,
`app/providers/documents/dual.py`, `pack_review.py`, `chunking.py`,
servicios de precio y persistencia de ofertas/candidatos.

Cierre: una relectura máxima por página y ejecución; tests que demuestren reducción
de llamadas y ninguna aceptación causada únicamente por alta confianza del modelo.

### Etapa 5 — Activación selectiva, medición y rollback

Acciones:

1. Añadir modos configurables `disabled`, `shadow`, `assisted`, `selective`.
   Mantener por defecto el flujo vigente mientras se completa la evaluación.
2. `assisted` muestra sugerencias trazables para revisión; `selective` permite aceptar
   únicamente casos dentro de políticas y umbrales medidos. Esta etapa no exige
   una nueva interfaz: puede exponer estados mediante los contratos existentes.
3. Definir y registrar umbrales separados para identidad y base del precio, probabilidad
   de la opción elegida, separación respecto de alternativas, calidad de evidencia
   y vetos deterministas. Validarlos en el conjunto reservado.
4. Evaluar cada decisión por separado: un pack no confirmado no invalida una identidad
   correcta, pero impide usar un precio unitario calculado como confirmado.
5. Activar primero por una selección de documentos/proveedores, con rollback a revisión
   OpenAI existente. Sin clave o disponibilidad de JEV, conservar comportamiento útil
   y dejar pendientes claros; no multiplicar llamadas visuales por una caída de JEV.

Metas iniciales propuestas, a confirmar con la línea base:

- Reducir al menos 50% las llamadas de revisión visual de packs.
- Reducir costo total por documento, incluyendo JEV y las pruebas adicionales de lectura.
- Reducir mediana y percentil 95 del tiempo total frente al flujo OpenAI actual.
- No degradar recuperación de ofertas ni precisión frente a las etiquetas humanas.
- Ninguna división de precio sin cantidad comercial validada; ningún error de pack
  crítico en la evaluación reservada. Reportar tamaño de muestra y límites estadísticos:
  cero errores observados no garantiza cero errores en producción.
- Medir tasa de revisión humana para evitar que el ahorro de API esconda trabajo manual.

Cierre: informe de resultados y política versionada de aceptación. Si no cumple calidad
o ahorro, conservar modo asistido/shadow y ajustar recuperación o evidencia.

## 5. Pruebas obligatorias

- Envase x48, pack real de seis, compra mínima, descuentos sin precio absoluto,
  cantidad ausente y contradicciones texto/catálogo.
- Candidato fuera del conjunto, opción `none`/`insufficient`, baja probabilidad,
  probabilidades inválidas, respuesta incompleta y timeout de JEV.
- Feedback humano prioritario y ningún candidato de otro `business_id`.
- Identidad resuelta con precio pendiente y situación inversa.
- Una sola relectura de página, recuperación compartida, límite de llamadas y
  resultados parciales visibles al agotar presupuesto.
- Conservación de precio original, ofertas repetidas, evidencia histórica e
  idempotencia de persistencia al reintentar una operación.
- Pruebas unitarias/API con proveedores falsos, sin red. Evaluación real separada
  y explícita sobre documentos etiquetados.

## 6. Optimizaciones posteriores

Solo después de medir el flujo anterior:

1. Reducir costo CPU del matching: precalcular normalización e índices de recuperación
   por negocio. Medir recall de candidatos antes de atribuir errores a JEV.
2. Cachear decisiones reutilizables con `business_id`, hash del documento/evidencia,
   versión de catálogo/feedback, prompt, esquema, política y modelo. Nunca reutilizar
   una base de precio por nombre de producto en documentos distintos.
3. Importación en segundo plano con estados, progreso y reanudación. Elegir persistencia
   y mecanismo de ejecución antes de diseñar migraciones o infraestructura; esto mejora
   experiencia y recuperación, pero no reduce tokens por sí mismo.
4. Evaluar texto nativo/OCR para formatos estructurados. Mantener visión cuando se
   necesite ubicación de precios, bloques y leyendas; no agregar dependencias sin medición.

No incorporar ahora clasificación general de mensajes, nuevos canales, frontend,
pgvector, colas o workflows externos: no son necesarios para esta mejora.

## 7. Orden y primera entrega

Orden: **0 → 1 → 2 → 3 → 4 → 5**. Las optimizaciones posteriores no bloquean
la integración mínima. Mantener ejecución en un solo agente salvo delegación explícita.

Primera entrega: línea base, contratos con evidencia, prompt versionado y política
determinista probada. Segunda entrega: JEV shadow con informe. Tercera entrega:
integración selectiva y eliminación de revisiones visuales redundantes.

## 8. Agentes responsables y coordinación

El usuario autorizó crear chats por etapa el 5 de octubre de 2026. Cada agente es
un rol de trabajo dentro de un chat del proyecto, con el modelo configurado por
el usuario. No se necesita un modelo diferente por rol. Este chat mantiene la
responsabilidad de arquitectura, revisión e integración como Tech Lead.

| Etapa | Agente responsable | Skills principales | Entrega |
|---|---|---|---|
| 0 | QA y evaluación | `stock-ai-quality-review`, `stock-ai-architecture` | Instrumentación reproducible, catálogo de casos y línea base |
| 1 | Extracción y contratos de IA | `stock-ai-prompts`, `stock-ai-backend-core` | Evidencia estructurada, prompt y compatibilidad histórica |
| 2 | Reglas de negocio y matching | `stock-ai-backend-core`, `stock-ai-quality-review` | Política determinista y candidatos internos |
| 3 | Integración JEV | `stock-ai-prompts`, `stock-ai-quality-review` | Proveedor tipado y evaluación shadow |
| 4 | Orquestación backend | `stock-ai-architecture`, `stock-ai-backend-core` | Servicio de análisis y relectura única por página |
| 5 | QA y activación | `stock-ai-quality-review`, `stock-ai-architecture` | Informe final, modos de activación y rollback |

### Límites de edición durante implementación

- Etapa 0: inicialmente `scripts/benchmark_procurement.py`,
  `tests/scripts/test_benchmark_procurement.py` y `docs/ETAPA_0_LINEA_BASE.md`.
  Los scripts y contratos compartidos se proponen antes de modificarlos.
- Etapa 1: schemas y servicio de ofertas, base y lector OpenAI, prompt de extracción
  y pruebas relacionadas. Revisar persistencia con la skill de datos si hace falta;
  no cambiar repositorios o crear migraciones como extensión implícita.
- Etapa 2: `decision_policy.py`, `matching.py`, `identity.py`, reglas de packs/precios
  y pruebas de esos módulos. Consume los contratos cerrados en etapa 1.
- Etapa 3: proveedor JEV, contratos propios, prompts JEV y pruebas del proveedor.
  Los cambios al piloto se coordinan después de cerrar etapa 0; configuración
  compartida se reserva a la integración de etapas 4–5.
- Etapa 4: endpoint, nuevo servicio de análisis, orquestador de bloques y relecturas,
  proveedor de revisión visual y pruebas API. Consume etapas 1–3 cerradas.
- Etapa 5: configuración, pruebas de activación y reporte de evaluación. Cambios
  adicionales a política o endpoint se coordinan con sus responsables.

### Inicio de chats y dependencias

Chats creados en el proyecto local:

| Etapa | Chat ID |
|---|---|
| 0 | `01a10e8a-96af-7fa0-8298-32fbe5e93416` |
| 1 | `01a10e8a-9bfe-7e72-840f-b7e97a3b738c` |
| 2 | `01a10e8a-a0f3-7b13-8667-0dcac286031e` |
| 3 | `01a10e8a-a927-7291-bd52-4f65ffff6476` |
| 4 | `01a10e8a-afde-7b00-988a-98f81c02bf29` |
| 5 | `01a10e8a-b949-70b2-a17a-4a1903dd360a` |

Se crea un chat por etapa. Etapa 0 comienza con instrumentación local y datos
sintéticos claramente identificados; la línea base real solo puede cerrarse con
documentos/etiquetas reales y mediciones observadas. No presentar simulaciones
como precisión, ahorro o costo medido.

Etapas 1–5 comienzan con revisión de solo lectura, preparación de implementación
y propuesta de pruebas. No editan archivos durante esa preparación y terminan
con un resumen de dependencias; no se quedan esperando en bucles ni arrancan
la implementación automáticamente. La implementación se activa mediante un
mensaje explícito en su chat cuando se integra la etapa anterior.

El orden de implementación sigue siendo 0 → 1 → 2 → 3 → 4 → 5; la preparación
puede avanzar simultáneamente. No editar dos veces el mismo archivo en paralelo.
Preservar los cambios locales existentes: no resetear, limpiar, commitear ni
reorganizar trabajo ajeno. Cada entrega enumera archivos, pruebas y limitaciones.
El Tech Lead revisa el resultado antes de habilitar la etapa siguiente.

No instalar dependencias, crear migraciones, hacer llamadas pagas o escribir en
la DB durante la preparación. Esta coordinación no crea una automatización ni
un seguimiento permanente: los próximos avances se gestionan en estos chats.

## 9. Referencias

- Marco del proyecto: [PLAN_ACCION_TECNICO.md](PLAN_ACCION_TECNICO.md).
- Flujo vigente: [EXTRACCION_OPENAI_Y_JEV.md](EXTRACCION_OPENAI_Y_JEV.md).
- Piloto existente: [JEV_PILOT_OPERACION.md](JEV_PILOT_OPERACION.md).
- [JEV: entrada textual y decisiones](https://docs.typesafe.ai/concepts/system-one).
- [Choice: opciones, probabilidades y preguntas múltiples](https://docs.typesafe.ai/primitives/choice).
- [OpenAI: archivos como entrada](https://developers.openai.com/api/docs/guides/file-inputs).
