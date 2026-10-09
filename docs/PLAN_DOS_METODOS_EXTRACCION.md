# Plan de acción: dos métodos de lectura, seleccionables en la interfaz

Fecha: 6 de octubre de 2026.
Estado: plan pendiente de implementación; no representa funcionalidades activadas.

La división solicitada en agentes y la revisión paralela quedaron documentadas en
[ORQUESTACION_DOS_METODOS.md](ORQUESTACION_DOS_METODOS.md), con responsables,
ownership, contratos comunes y orden de integración. Esa preparación no activa A/B.

## 1. Qué vamos a construir

Al cargar un PDF, permitir elegir entre:

- **OpenAI separa los datos:** lee cada oferta y entrega nombre, marca,
  presentación, precio y evidencia separados. JEV ayuda con decisiones ambiguas.
- **OpenAI lee y JEV organiza:** OpenAI transcribe ofertas en bloques con sus
  textos y relaciones visibles. JEV clasifica los fragmentos; el backend arma
  los campos y valida. JEV también ayuda con decisiones ambiguas del catálogo.

Ambos métodos guardan la información original y producen resultados con la misma
forma para consultar ofertas, revisar coincidencias y comparar proveedores.
El método elegido no puede borrar artículos por no encontrar un producto o precio.

Este plan amplía [PLAN_OPENAI_JEV_SELECTIVO.md](PLAN_OPENAI_JEV_SELECTIVO.md).
Conserva sus reglas de calidad, evidencia, aislamiento y medición, y reemplaza
su supuesto de una única estrategia de extracción. La solicitud actual autoriza
planificar la modificación del frontend existente para elegir ambos métodos;
no requiere crear una aplicación nueva ni adoptar Next.js u otra tecnología.

## 2. Estado actual inspeccionado

| Parte | Qué existe | Qué falta |
|---|---|---|
| Interfaz | Formularios de uno o dos PDF en `app/static/index.html` | Selector de método y estado por método |
| Envío de archivos | `analyzePdf()` en `app/static/app.js` envía `extraction_provider=auto` | Enviar método seleccionado y conservarlo en el resultado |
| Lectura | Lector OpenAI con respuesta estructurada; hasta dos páginas por bloque | Evidencia explícita mejorada para A y contrato de bloques para B |
| Orquestación | `ChunkedDocumentExtractionProvider`, recuperación por página | Estrategias intercambiables y política común de relectura |
| Packs | OpenAI revisa hasta ocho ofertas de una página por llamada | Decisión textual de JEV y revisión visual solo cuando sea útil |
| Catálogo | Matching determinista y feedback humano | Recuperar varios candidatos internos y JEV selectivo |
| JEV | Chooser de candidatos y piloto shadow | Clasificador de fragmentos y decisión de base del precio |
| Persistencia | Ofertas/documentos con metadata | Historial de método, bloques sin resolver y decisiones trazables |
| Medición | `scripts/benchmark_procurement.py` y 14 pruebas de esa herramienta | Usage real de proveedores y evaluación humana de ambos métodos |

La revisión del 6 de octubre encontró 159 pruebas aprobadas en proveedores,
procurement, API y benchmark; no fueron pruebas pagas contra APIs reales.
Los chats de etapas 1–5 prepararon propuestas, sin implementar la integración.
Gemini continúa suspendido. La línea base real permanece abierta.

## 3. Diseño de las dos opciones

### Método A — `openai_structured`

1. OpenAI lee las páginas y entrega campos separados.
2. Conservar nombre original, citas breves de precio/cantidades, página y procedencia.
3. El backend normaliza y resuelve casos inequívocos bajo la política compartida.
4. Si hay candidatos similares, JEV elige producto o se abstiene.
5. Si la base del precio es ambigua pero la evidencia alcanza, JEV elige
   `unit`, `pack` o `unknown`. Cantidad y cálculos se validan en backend.
6. Releer visualmente solo información faltante o inconsistente recuperable.

Ventaja esperada: aprovechar el lector existente y evitar decisiones extra sobre
campos que ya están claros. Riesgo: OpenAI puede separar o interpretar mal un campo;
debe conservarse la evidencia para corregirlo.

Durante la construcción, el default sigue usando el comportamiento OpenAI vigente.
No describir A como completamente integrado hasta conectar evidencia y política.

### Método B — `openai_blocks_jev`

1. OpenAI detecta bloques de oferta y transcribe su texto fielmente, incluidos
   precios tachados, condiciones, contenido, promociones y partes ilegibles.
2. Conservar bloques completos y fragmentos ordenados, con página e IDs estables.
   No pedir que los fragmente en palabras sueltas ni que omita textos que no entiende.
3. El backend detecta candidatos de número/moneda/unidad como apariciones dentro
   del texto. Un número todavía no se considera precio o cantidad comercial.
4. JEV clasifica fragmentos viendo el bloque entero y las condiciones de página.
5. Cuando hay varios valores posibles, JEV selecciona un fragmento/candidato existente,
   por ejemplo precio actual frente a precio anterior. No genera valores nuevos.
6. El backend arma la ficha desde los fragmentos seleccionados, manteniendo citas
   y procedencia. Si no hay datos suficientes, conserva el bloque como pendiente.
7. Aplicar el mismo matching, decisiones de identidad/base del precio y presupuesto
   de revisión que en A.

Ventaja esperada: separar lectura visual e interpretación, con revisión textual de JEV.
Riesgos: más texto de salida, más preguntas, mala segmentación o asociaciones incorrectas.
No asumir que este método será más barato o rápido; es una hipótesis de evaluación.

### Cómo usar JEV sin perder la relación entre los datos

JEV elige entre opciones; no es un generador libre de fichas ni números. Categorías
iniciales: `product_name`, `brand`, `variant`, `retail_size`, `retail_contents`,
`commercial_quantity`, `current_price`, `previous_price`, `minimum_purchase`,
`promotion`, `tax_condition`, `other`, `uncertain`, `mixed`.

Ejemplo: “6 botellas de 1,5 L — $12.000 por bulto, mínimo 2 bultos” contiene varios
roles. No forzar una única categoría para toda la línea: conservarla como `mixed`
y distinguir apariciones/candidatos existentes. Cada campo derivado referencia
fragmentos del mismo bloque. Si hacen falta partes más finas, usar segmentos locales
con posiciones que reconstruyan el texto original; ninguna palabra se elimina.

La opción `other` conserva información que no pertenece a los campos habituales.
`uncertain` conserva incertidumbre. Las probabilidades corresponden a las opciones
de cada pregunta, no a una garantía de que la página se leyó completa.

Las preguntas son independientes. Una pregunta sobre precio actual debe referirse
al bloque y sus candidatos, no suponer que conoce la respuesta de otra pregunta.
Empezar con decisiones agrupadas por oferta; evaluar agrupación de varias ofertas
con IDs y contextos aislados antes de adoptarla para reducir llamadas.

## 4. Contratos y datos que debemos conservar

### Contrato público de selección

- Añadir `analysis_method` al formulario multipart del endpoint
  `POST /procurement/supplier-offers/from-document`.
- Valores: `openai_structured` y `openai_blocks_jev`.
- Default: `openai_structured`, compatible con clientes actuales que envían `auto`.
- Mantener `extraction_provider` como parámetro legacy separado del método;
  `auto`/`openai` siguen leyendo con OpenAI. Gemini sigue rechazado.
- Método desconocido: error explícito, sin ejecutar proveedores.
- B sin JEV disponible/configurado: deshabilitarlo en interfaz y rechazar la petición
  antes de llamar OpenAI. Si JEV falla después, guardar evidencia pendiente y explicar
  el resultado parcial; no cambiar silenciosamente a A.
- Añadir una capacidad de backend de solo lectura para informar métodos disponibles
  y razones de indisponibilidad. Nunca exponer claves en navegador.

### Contratos internos nuevos

`OfferTextBlock`: ID generado por backend, página original, orden en página,
texto completo, fragmentos ordenados y referencias a condiciones generales.
Los IDs locales de OpenAI se validan y se convierten a IDs estables de ejecución.
No confiar en coordenadas precisas generadas por el modelo; si se agregan recortes
visuales más adelante, validar su correspondencia con la página real.

`FragmentClassification`: fragmento/candidato, categoría elegida, distribución de
probabilidades, confianza, modelo y pregunta/prompt versionados.

`OfferInterpretation`: campos derivados, citas/referencias, estados independientes
de identidad y precio y motivos de pendientes. Las cantidades o precios ausentes
se representan como ausentes, nunca como cero ni valores supuestos.

`AnalysisSummary`: método realmente ejecutado, ID de ejecución, páginas, bloques
observados, ofertas utilizables, pendientes, avisos, duraciones y usage/costos
cuando estén disponibles. `import_result` y `comparison` siguen compatibles;
los bloques sin ficha comercial aparecen además como pendientes explícitos.

### Evitar pérdida de artículos

- Guardar toda evidencia recibida antes de filtrar o persistir fichas finales.
- El modelo actual exige precio por oferta; no insertar bloques sin precio en ese
  modelo usando importes ficticios. Mantenerlos en un registro de evidencia pendiente
  por documento hasta tener precio válido o revisión humana.
- Verificar que metadata existente soporta este registro y los límites de tamaño.
  Si no alcanza, elegir persistencia específica antes de diseñar migraciones.
- A debe conservar también bloques incompletos/pendientes: el número de artículos
  sin precio no puede desaparecer al comparar ambos métodos.
- Cada bloque queda representado por ofertas derivadas o un pendiente explícito;
  un bloque con varias variantes puede producir más de una oferta, con referencias.
- Revisar cobertura por página; los conteos estimados por OpenAI no son verdad humana.
- Sin candidato del catálogo, la oferta se conserva como no vinculada.
- No deduplicar artículos solo por nombre: pueden repetirse en otras páginas o promociones.
- Guardar versiones e historia de correcciones; distinguir precio actual y anterior.
- Contenido x48, pack x6 y compra mínima son datos distintos; nunca dividir sin
  confirmar base del precio y cantidad comercial.

## 5. Flujo común y relecturas

1. Validar archivo, tenant, método y disponibilidad de proveedores.
2. Identificar ejecución/documento y leer bloques de hasta dos páginas con OpenAI.
3. Conservar resultado de lectura y convertirlo mediante estrategia A o B.
4. Aplicar política compartida y consultar candidatos del catálogo del negocio.
5. Consultar JEV únicamente por decisiones pendientes; en B, clasificación inicial
   es parte obligatoria de esa estrategia.
6. Agrupar información visual recuperable faltante/contradictoria por página.
7. Releer como máximo una vez por página después de la lectura inicial, sin volver
   a cobrar revisiones separadas de packs sobre la misma página. Si aparece una
   duda posterior al intento, queda pendiente.
8. Revalidar y persistir evidencia, ofertas y candidatos conservando resultados parciales.
9. Responder con método real, pendientes, razones y métricas disponibles.

Backend decide la ruta y calcula precios; JEV no consulta DB ni escribe datos.
Probabilidad baja con texto completo deriva a revisión, no automáticamente a visión.
Presupuestos de llamadas/plazo por ejecución son comunes y se registran por etapa.
Conservar feedback humano compatible y revalidar presentación al reutilizarlo.

La ejecución sigue sin exigir colas o infraestructura nueva. Al integrar, evitar
trabajo síncrono prolongado dentro del event loop del endpoint `async`; ejecutar
la orquestación bloqueante en el mecanismo de ejecución apropiado de FastAPI.
Una importación durable en segundo plano queda como mejora posterior independiente.

## 6. Elección en el frontend existente

Añadir un selector **“Método de lectura”** en la sección de carga, visible tanto
para “Un PDF” como para “Dos PDF”. Opciones:

1. **OpenAI separa los datos** — “Lee y organiza la oferta; JEV ayuda con las dudas.”
2. **OpenAI lee y JEV organiza** — “Lee los textos de cada oferta y JEV los clasifica.”

- No llamar “más barato” o “más rápido” a ninguna sin mediciones reales.
- Para dos proveedores, tomar la selección al enviar el formulario y aplicarla
  a ambos archivos; mantenerla fija durante toda esa operación.
- La selección de método es distinta de comparar uno o dos proveedores. La opción
  actual “Dos PDF” no es una comparación A/B de algoritmos.
- Mostrar método ejecutado en resultado y detalle histórico; no inferirlo a partir
  de la selección actual ni etiquetar documentos antiguos con un método inventado.
- Mostrar por separado ofertas vinculadas, no vinculadas y bloques pendientes.
  Evitar que `bestOffersByProduct()` o el resumen de dos proveedores escondan
  pendientes por no tener producto/precio comparable.
- B muestra indisponibilidad antes del envío. Si hay resultado parcial, explicar
  qué falta sin afirmar que el análisis terminó completo.
- Usar etiquetas accesibles, teclado y mensajes españoles; escapar todo texto del
  documento mostrado en HTML, incluidos los fragmentos sin clasificar.

Archivos previstos: `app/static/index.html`, `app/static/app.js` y ajustes mínimos
en `app/static/styles.css`. Reutilizar estructura actual; no rehacer la interfaz.

No ejecutar ambos métodos automáticamente al subir un PDF. El usuario elige uno.
Para evaluar el mismo PDF con ambos se harán dos ejecuciones identificadas, sin
duplicar silenciosamente relaciones, feedback ni precios activos del proveedor.

## 7. Etapas, responsables y archivos

| Etapa | Responsable | Entrega y archivos principales | Depende de |
|---|---|---|---|
| 0. Contratos y medición | Tech Lead + QA | Contratos A/B, IDs, ledger de pendientes; completar benchmark/usage | Inventario actual |
| 1. Método A | Extracción IA | `schemas.py`, `base.py`, `openai.py`, prompts estructurados, conservación en `supplier_offers.py` | 0 |
| 2. Método B | Extracción IA + JEV | Nuevos contratos/lector de bloques, `jev_fragment_classification.md`, clasificador en `app/providers/jev.py`, ensamblador en procurement | 0 y contratos de 1 |
| 3. Decisiones comunes | Backend + JEV | `decision_policy.py`, candidatos internos, identidad y base del precio, prompts compartidos | 1–2 |
| 4. Integración API | Backend | Servicio de análisis, `main.py`, `settings.py`, capacidad de métodos, registro común de relecturas y persistencia | 1–3 |
| 5. Selector y resultados | Frontend | Formularios, envío, disponibilidad y detalle de ambos métodos en `app/static/` | Contrato API de 4 |
| 6. Comparación y cierre | QA | Pruebas completas, evaluación A/B etiquetada, informe de costo/latencia/calidad | 4–5 |

Los roles pueden ser los agentes de los chats existentes, pero sus instrucciones
del plan anterior no se consideran actualizadas automáticamente. No se crean ni
activan nuevos chats como parte de escribir este plan. Antes de delegar ejecución,
asignar ownership según los contratos nuevos; no editar schemas, proveedor JEV,
endpoint o configuración desde dos chats simultáneamente.

La preparación de pruebas y diseño de interfaz puede avanzar en paralelo. La
integración espera contratos acordados; solo quien integra modifica archivos comunes.
Modelo actual de OpenAI y Gemini suspendido se mantienen durante la comparación.
No instalar dependencias ni crear migraciones por conveniencia del experimento.

## 8. Pruebas y criterios de cierre

### Funcionales, sin llamadas pagas

- API acepta A/B, conserva el método real, default legacy e informa indisponibilidad.
- Esquemas de OpenAI cerrados y válidos; validación estricta de respuestas JEV.
- Clasificación de fragmentos múltiples, precios tachados y actual, contenido x48,
  bulto x6, precio por envase con mínimo seis y descuentos sin precio absoluto.
- IDs desconocidos/duplicados, candidatos de otro bloque/negocio, palabras no
  clasificadas y respuesta parcial: conservar originales y pendientes.
- Bloques sin precio y varias variantes en el mismo bloque sin ofertas inventadas.
- Igual contrato final para A/B, historial y feedback humano preservados.
- Relectura compartida una vez por página; presupuestos agotados y caída de JEV.
- Persistencia aislada en fixtures/repositorios de prueba: no tocar DB del negocio.
- Interfaz envía el método elegido, lo fija para dos archivos y representa pendientes.
- Verificación en navegador de selector, mensajes, accesibilidad y escape HTML.

### Evaluación real separada

Usar los mismos PDFs, catálogo, feedback y condiciones de ejecución para ambos
métodos, con etiquetas humanas por página/oferta y casos reservados por proveedor.
Separar extracciones congeladas para comparar interpretación de ejecuciones completas
para comparar lectura, tiempo y costo total; no reutilizar resultados de A como si
hubieran sido una lectura independiente de B. Registrar uso de caché y variabilidad.

Medir: ofertas omitidas, bloques pendientes, precio actual correcto, cantidades
comerciales, confusión pack/contenido, precisión/recall de identidad y candidatos,
tiempo p50/p95, llamadas y tokens OpenAI/JEV, costo total y trabajo humano.
No usar solo cantidad de ofertas como prueba de precisión.

El precio por token de JEV es bajo, pero B puede añadir más preguntas y más texto
de salida OpenAI. Comparar costos reales de todo el documento con tarifas y modelos
identificados; no concluir ahorro por el precio de JEV aislado.

Cierre: ambas opciones utilizables y visibles, ninguna pérdida silenciosa de
información recibida, ninguna división sin evidencia/cantidad validada y reporte
comparativo. Conservar ambas para elección del usuario; seleccionar el default
futuro solo con resultados. Los casos fuera de confianza permanecen pendientes.
No afirmar que no hay omisiones en producción a partir de conteos del modelo.

## 9. Primera entrega y referencias

Primera entrega concreta: contratos de bloques/evidencia, ledger de pendientes,
usage por llamada y método A compatible. Después implementar B y las decisiones
comunes; conectar API y frontend al estar operativas las dos rutas. El selector no
debe ofrecer una ruta ficticia mientras B está pendiente.

- [Plan base de JEV selectivo](PLAN_OPENAI_JEV_SELECTIVO.md).
- [Flujo actualmente implementado](EXTRACCION_OPENAI_Y_JEV.md).
- [Línea base aún abierta](ETAPA_0_LINEA_BASE.md).
- [JEV: datos textuales y decisiones](https://docs.typesafe.ai/concepts/system-one).
- [JEV Choice: categorías y selección entre candidatos](https://docs.typesafe.ai/primitives/choice).
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
