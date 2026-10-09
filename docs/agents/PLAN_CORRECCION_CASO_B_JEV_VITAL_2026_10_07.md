# Caso B — plan de corrección de «OpenAI lee y JEV organiza»

Investigación independiente del 7 de octubre de 2026. Documento `116561.pdf`,
proveedor Vital, `business_id=demo-business`, run/document
`4a3058e1c8a84cbbaf260f44bec7e83b`, método `openai_blocks_jev`.
Este documento propone trabajo futuro: no implementa cambios ni autoriza otra corrida paga.
Gemini permanece suspendido.

## Conclusión y hallazgos por severidad

El método B quedó parcial por decisiones de nuestro backend y adaptador antes de
que JEV pudiera interpretar la mayoría de los bloques. La evidencia acredita
125 bloques leídos en las páginas 1–10, 13 consultas reales a JEV y nueve fichas
pendientes de siete bloques. No acredita lectura de las páginas 11–24 ni precisión
visual de las transcripciones. No permite concluir que JEV sea inútil o que este
método ahorre costo frente al A.

| Prioridad | Ubicación actual | Problema, impacto y corrección propuesta |
|---|---|---|
| P1 / crítico | `app/providers/jev_analysis.py:495`, `:501`, `:579`; `app/modules/procurement/block_interpretation.py:150` | Repetimos el prompt entero y las descripciones de candidatos por pregunta; todos los números reciben nueve preguntas de precio. 82 de 95 solicitudes se rechazan localmente por superar 100.000 bytes. Compactar contexto, reutilizar referencias, seleccionar preguntas y hacer preflight antes de reservar HTTP. |
| P1 / crítico | `app/modules/procurement/document_analysis.py:107`, `:544`, `:704`, `:724` | Una clasificación rechazada antes de HTTP consume una reserva y la clasificación se intercala con la lectura. Cinco lecturas + 95 clasificaciones consumen 100 reservas; las siguientes 14 páginas nunca se envían al lector. Reservar cobertura inicial, separar cuotas de fases y devolver reservas que no alcanzaron el transporte. |
| P1 / crítico | `app/modules/procurement/block_interpretation.py:87` | El candidato de texto se corta al primer importe y no vuelve a considerar el nombre posterior. NATURA/Aceite girasol/x900ml no es seleccionable. Generar spans por líneas y grupos multilínea de cualquier posición, sin alterar la evidencia ni usar condiciones generales como nombre de producto. |
| P1 / alto | `app/providers/jev_analysis.py:332`; `app/modules/procurement/block_interpretation.py:269`, `:477` | No existe rol `reference_price`. JEV elige `current_price` para XLT de PAULA y KEVIN; backend lo convierte en otra oferta. Separar precio publicado, precio anterior y referencia por litro/kg/contenido; conservar citas de todas las categorías, emitir fichas sólo de los importes comerciales vinculados. |
| P1 / alto | `app/modules/procurement/block_interpretation.py:171`, `:395`, `:407`; `app/providers/jev_analysis.py:313` | Candidatos de cantidad incluyen cualquier entero, incluso tamaños y precios. `150ml` y `123ml` terminan también como contenido de 150/123 unidades. Restringir opciones por contexto literal y validar rol/unidad, sin eliminar las ocurrencias originales del ledger. |
| P1 / alto | `app/modules/procurement/block_interpretation.py:382`, `:393`; `app/modules/procurement/decision_policy.py:425` | Si la base queda `unknown`, el assembler omite las referencias que motivaron la propuesta. Sin identidad admisible y sin `price_type` refs, una duda de base no prepara pregunta JEV. Conservar evidencia de base candidata y evidencia de importe separadas, incluso cuando no se confirma; consultar sólo si la pregunta puede resolverse con esa evidencia. |
| P1 / alto | `app/modules/procurement/block_interpretation.py:472`; `app/modules/procurement/matching.py:583` | Todas las fichas conservan `$`. El comparador exige ARS y bloquea las cinco que tienen candidato de catálogo. Normalizar con contexto explícito y auditable; conservar símbolo crudo. Las otras cuatro fichas no llegan a ese gate porque no tienen candidato. ARS por sí solo no confirma identidad ni base. |
| P1 / transversal | `app/modules/procurement/document_analysis.py:397`; `app/providers/documents/openai.py:169` | El timeout del lector recibe el restante global y omite el límite configurado de 60 s. Arreglar reservas no garantiza 24 páginas dentro de 180 s. Hacer efectivo el timeout por transporte y diseñar lectura reanudable antes de prometer cobertura total de esta clase de PDF. Coordinar con Caso A. |
| P2 / medio | `app/modules/procurement/block_interpretation.py:23`, `:27`; `app/modules/procurement/normalization.py:81` | B no detecta `cc` como tamaño aunque el normalizador compartido acepta `cc → ml`; HEINEKEN x710cc no tiene opción de tamaño. Unificar léxico sin interpretar `HEINEKEN 710CM` del catálogo como 710ml: esa grafía requiere revisión de catálogo. |
| P2 / medio | `app/providers/jev_analysis.py:235`, `:249`; `app/main.py:938` | Siete respuestas individuales inválidas en cinco consultas parciales se agrupan como `invalid_answer`, sin crudo rechazado ni razón específica. El log consulta métricas antiguas y puede decir `unknown`. Guardar códigos de validación específicos, tamaños, fases y conteos reales; no exponer claves ni cuerpos en consola. |
| P2 / latente | `app/modules/procurement/block_interpretation.py:478`; `app/modules/procurement/identity.py:52` | Todas las 16 propuestas tienen `brand=null`; B no pregunta marca. La detección de conflicto de marca exige marcas en ambos lados. Una búsqueda con el nombre literal correcto aún propone otras marcas. Mejorar extracción literal de marca y calidad del catálogo antes de habilitar aceptación automática. |

`DecisionThresholds.allow_jev_acceptance=False` (`decision_policy.py:37`, `:258`)
es una política deliberada y conservadora, no un fallo de conexión con JEV. No debe
habilitarse como atajo. Los gates de evidencia, cantidad comercial, tenant y moneda
evitaron que esta salida preliminar produjera comparación de costos o vinculación
confirmada. Deben mantenerse durante la corrección.

## Evidencia y límites de atribución

Fuentes locales congeladas:

- `output/diagnostics/jev-run-4a3058e1-full.json`: metadata y ledger completos.
- `output/diagnostics/jev-run-4a3058e1-analysis.json`: resumen y pendientes.
- `output/diagnostics/jev-run-4a3058e1-matches.json`: nueve resultados guardados.
- `output/diagnostics/jev-run-4a3058e1-payload-sizes.json`: reconstrucción previa, contrastada independientemente.
- `output/diagnostics/catalog-demo-business-2026-10-07.json`: 3.260 productos y cero feedback; exportación posterior, sin garantía de catálogo idéntico al usado en la corrida.
- `output/diagnostics/analysis-settings-safe-2026-10-07.json`: configuración actual segura; tampoco prueba la configuración histórica completa.

Se revisaron `AGENTS.md`, las skills de calidad, backend y prompts, el plan técnico,
la guía de colaboración, los contratos y el diagnóstico previo como hipótesis.
Hay backend y UI actuales; las notas iniciales de repo vacío están desactualizadas.
No se inspeccionaron secretos, no se consultó la DB y no se usaron APIs de inferencia.

| Magnitud del ledger | Resultado comprobado |
|---|---:|
| Páginas registradas / con reading_id inicial | 24 / 10 |
| Páginas nunca leídas | 11–24 |
| Bloques conservados / pendientes | 125 / 125 |
| Bloques con un solo fragmento | 55; varios contienen múltiples líneas |
| Auditorías de clasificación / rechazo local de tamaño | 95 / 82 |
| HTTP reales de OpenAI / JEV | 5 / 13 |
| HTTP JEV completos / parciales | 8 / 5 |
| HTTP JEV de decisión de catálogo/base | 0 |
| Propuestas en ledger / fichas persistidas | 16 / 9 |
| Bloques que originan fichas | 7 |
| Identidades / bases confirmadas | 0 / 0 |
| Relecturas | 0 |
| Tiempo de análisis | 167,625 s |
| Tokens OpenAI reportados: entrada / salida / total | 53.165 / 13.226 / 66.391 |
| Tokens JEV reportados: entrada / salida / total | 429.882 / 52.283 / 482.165 |

Los tiempos de las cinco lecturas fueron 46,437–56,031 s; JEV 0,453–2,640 s por
HTTP. JEV domina tokens reportados; el lector domina latencia observada. Una
extrapolación simple de 12 grupos de dos páginas, dos workers y unos 52 s/grupo da
unos 312 s sólo de lectura. Es una advertencia de capacidad, no una predicción:
contenido, tamaño de grupos y variabilidad real pueden cambiarla.

La metadata tiene el hash del source de la política; coincide exactamente con el
archivo actual (`a79039daf6868c10d813fe088ec28d813397240524ba661a2401013a889cc5c8`).
No hay una instantánea completa del código, configuración, catálogo y cuerpos
HTTP históricos. El PDF original no está disponible en el workspace por ese
nombre. No se puede validar visualmente si `$3.889` es precio unitario, si hubo
tachados, si faltaron bloques o si las condiciones fueron asociadas correctamente.

Responsabilidades diferenciadas:

1. **Lector OpenAI:** conservó texto y grupos visuales en cinco lecturas. Su agrupación en un fragmento multilínea es compatible con partes del contrato actual y debe tolerarse. Hay revisión visual pendiente en páginas 1–2. No hay base para atribuir al lector la omisión de páginas 11–24: el orquestador nunca las envió.
2. **Backend candidato/assembler:** decide qué ocurrencias y opciones puede ver JEV, valida offsets y genera propuestas. Le corresponde el truncamiento de nombre, la expansión indiscriminada de números, las opciones de contenido equivocadas, el léxico `cc`, la falta de marca y la pérdida de referencias de base.
3. **Adaptador/cliente JEV:** repite prompt y descripciones; impone límites locales y valida respuestas. `request_limit` no es una respuesta de JEV ni un HTTP 413. Las preguntas son independientes: no se puede hacer que una use la respuesta de otra dentro de un POST.
4. **Decisiones reales de JEV:** cuando fue consultado, etiquetó XLT como `current_price` en PAULA y KEVIN, y eligió tamaños como conteo de contenido. En perfume KEVIN x100ml etiquetó XLT como `previous_price`; el texto guardado no contiene un marcador de precio anterior. Son errores sobre las opciones disponibles, aunque nuestro contrato omitía un rol de referencia adecuado. Las selecciones de nombre erróneas están condicionadas por una lista que a veces no contenía el producto.
5. **Política y matching:** mantienen abstención, no escritura directa por IA y `business_id`. Una candidatura fuzzy no es vínculo confirmado. En los nueve resultados guardados hay cinco con producto propuesto y cuatro sin candidato; todos quedan pendientes. No hubo una decisión JEV de identidad/base que pueda calificarse como correcta o incorrecta.

Las respuestas inválidas se descartaron; sólo se guardaron las decisiones válidas.
Sin el crudo rechazado no se puede saber si cada `invalid_answer` fue probabilidad
no normalizada, opción extra, elección distinta del máximo, forma inválida u otra
condición. El tamaño exacto del cuerpo original tampoco fue archivado: se informa
una reconstrucción que coincide con los motivos y las preguntas congeladas.

## Ejemplos completos de la corrida y salida segura deseada

### NATURA — nombre después del precio, tamaño y mínimo

```text
DESDE 3 UNIDADES
$3.649
NATURA
Aceite girasol
x900ml.
PRECIO XLT: $4054.44
```

Bloque `p1:0:b0`. Sólo están vinculadas las condiciones legibles «Vigencia: del 05
al 11/10 / SAN JUSTO». La condición larga parcialmente legible «POR BULTO CERRADO»
está conservada en la página, pero **no** está vinculada a este bloque: no se puede
usarla para confirmar pack por conveniencia.

Estado actual: los textos seleccionables son `DESDE 3 UNIDADES` y la vigencia;
las ocurrencias tratadas como posible precio son `3`, `$3.649`, `900`, `$4054.44`.
Se preparan 37 preguntas y 128.624 bytes. El cliente fake verifica cero transportes
y una reserva de presupuesto consumida. No hay decisión real de JEV de este bloque.

Salida deseada a validar con evidencia: candidato literal de nombre
`NATURA\nAceite girasol\nx900ml.`, tamaño 900ml, mínimo 3 unidades con alcance de
oferta si ese alcance se acredita, importe publicado 3649 y referencia 4054.44/l.
El mínimo no es divisor ni prueba de un pack de tres. La base queda pendiente si
no hay evidencia unit/pack suficiente; no se calcula costo comparable. La moneda
se resuelve por una política de contexto con procedencia o permanece pendiente.

En el catálogo actual una propuesta contrafactual con ese nombre literal devuelve
`ACEITE NATURA GIRASOL 900ML` primera, junto con otras marcas. Esto demuestra
recuperabilidad de una candidatura, no identidad confirmada ni corrección del catálogo histórico.

### KEVIN — precio publicado y referencia por litro

```text
KEVIN
Desodorante
corporal dubal
x150ml
PRECIO XLT: $25.926,67
EXCEPTO SALTA
$3.889
```

Bloque `p3:0:b6`. JEV elige `not_price` para 150, `current_price` para 25.926,67 y
para 3.889. Elige el literal de 150 como `retail_contents_count`. El assembler
crea dos propuestas y se guardan dos fichas con el nombre terminando en `PRECIO
XLT:`. Sus precios quedan pendientes; el comparador no activa costos.

Salida deseada: una ficha comercial candidata de 3889 para el envase descrito,
referencia separada 25926.67/l, tamaño 150ml y contenido de piezas desconocido.
Conservar literalmente `dubal` y `EXCEPTO SALTA`; no corregir el texto por intuición.
El nombre seleccionable termina en `x150ml`, separado de la etiqueta XLT. Una
referencia por litro no es precio anterior ni segunda oferta de 150ml. Su relación
aritmética puede ser control de consistencia, nunca fuente para inventar una base.

La reproducción fake con catálogo actual recupera `KEVIN DESODORANTE 150ML`,
pero también marcas alternativas. Sin marca estructurada, equivalencia de variante
acreditada o revisión humana, no debe confirmarse una identidad por similitud.

### PAULA — mismo error más alcance geográfico

```text
PAULA
Desodorante
corporal aura
x123ml
PRECIO XLT: $30.073,17
EXCEPTO SAN JUSTO-BURZACO-
TRONADOR-RESISTENCIA
$3.699
```

Bloque `p3:0:b5`. Se guardan dos fichas, 30073.17 y 3699; 123 aparece también como
contenido. Mantener una ficha candidata de 3699 y referencia separada por litro,
tamaño 123ml, exclusiones completas y contenido de piezas pendiente. No repartir
el precio entre sucursales ni aplicar la condición a toda la página. El catálogo
actual no tiene producto cuyo nombre contenga PAULA; la candidatura recuperada
CIEL no es prueba de identidad. Abstenerse es el resultado correcto si no hay match.

### HEINEKEN — pack sin cantidad y unidad cc

```text
LLEVANDO POR PACK
$4.489
HEINEKEN
Cerveza lata
x710cc.
PRECIO XLT: $6322.54
```

Bloque `p1:0:b12`. El nombre elegido es `LLEVANDO POR PACK`; el detector ofrece
cero tamaños. JEV propone pack, pero no hay cantidad comercial explícita. La
salida segura es nombre literal de HEINEKEN, tamaño 710ml con cita `710cc`, mínimo
y cantidad comercial desconocidos, referencia XLT separada y base pendiente si
«llevando por pack» describe una condición de compra sin indicar total o precio
por envase. No dividir 4489 por 710 ni tratar 710 como latas.

### KIMBIES — contenido interno y precio de referencia por pieza

```text
DESDE 2 UNIDADES
$1.869
KIMBIES
Toalla humeda
x48u
PRECIO XU: $38,94
```

Bloque `p7:0:b0`, rechazado localmente sin JEV. El nombre debe permanecer
seleccionable, 48 corresponde al contenido del envase y 2 al mínimo de envases;
38.94 por toalla es referencia, no otra oferta comercial. No comprar 48 envases
ni dividir 1869 por el mínimo. El catálogo actual recupera `KIMBIES TOALLITAS
HUMEDAS X48U` como candidatura; esto no resuelve por sí solo la base de precio.

## Rediseño propuesto y alternativas medibles

### 1. Evidencia inmutable y candidatos literales útiles

Separar el texto leído de la proyección para interpretación. Conservar fragmentos,
orden, legibilidad, condiciones y offsets originales. Crear segmentos por cada
línea y grupos consecutivos plausibles de nombre/presentación antes y después de
cada importe. Cada candidato nuevo debe ser un span exacto; si se combinan líneas
no contiguas, usar una lista de referencias, nunca una cita inventada. Deduplicar
spans idénticos, no ofertas visualmente repetidas.

Preparar ocurrencias tipadas con contexto cercano: moneda, precio explícito,
referencia XLT/XKG/XU, tamaño, contenido y mínimo. Tipar no equivale a confirmar;
una ambigüedad debe conservarse. Descartar **de las preguntas de precio** tamaños,
porcentajes, fechas y mínimos inequívocos, conservándolos en el ledger. Permitir
precios sin símbolo cuando hay evidencia «precio» o una línea numérica aislada.
Agregar rol de referencia con denominador y cita; no llamar `not_price` a una
referencia que sí importa para auditoría.

Hacer las opciones de conteo específicas del rol: tamaño 150ml no puede ser
contenido de piezas; mínimo 3 no puede ser cantidad comercial; un importe entero
no debe ofrecerse como conteo. Si hay roles superpuestos genuinamente ambiguos,
mantener abstención y razón explícita. Unificar léxico de unidades con la capa
normalizadora. Marca/variante deben apoyarse en texto literal o alias revisados,
sin completarlas desde memoria del modelo.

### 2. Contexto compacto dentro de cada request

El wire actual ya tiene `state` y `questions`: no hace falta asumir un endpoint
conversacional, caché remota, sesiones o una API nueva. Probar reglas breves una
vez en `state.policy_rules`, mapa único de ocurrencias y condiciones vinculadas,
y preguntas que lo referencien explícitamente. Retirar del contexto proyectado
motivos de fases posteriores, propuestas/IDs derivados, condiciones no vinculadas
y duplicaciones de texto que puedan recomponerse. Mantener mapa local entre IDs
cortos del wire y IDs persistidos, con validación de tenant/run/block; nunca confiar
en un ID de un bloque vecino.

Las instrucciones deben distinguir `policy_rules` del documento no confiable;
una orden presente en `fragment.text` no puede alterar reglas. No cambiar reglas
de seguridad por minimizar bytes. La respuesta de cada pregunta debe seguir
siendo independiente, cerrada y validada por Pydantic.

Experimento offline real, sin cambiar runtime: mismas preguntas y opciones,
prompt una vez en `state`, sin descripciones duplicadas en `criteria` porque las
ocurrencias ya están en el estado. Se dejó `full_text` y fragments duplicados para
que el resultado sea conservador. Para los 125 bloques observados:

| Serialización hipotética | Mínimo / mediana / p95 / máximo en bytes | Suma |
|---|---|---:|
| Actual reconstruida | 35.291 / 137.700 / 186.266 / 343.656 | 17.497.033 |
| Compacta con mismas preguntas | 10.985 / 29.845 / 38.637 / 65.252 | 3.792.665 |
| Compacta con sólo preguntas de rol | 5.881 / 9.843 / 11.151 / 15.476 | 1.231.330 |

La compacta con mismas preguntas reduce 78,3% los bytes hipotéticos y deja todos
estos bloques debajo de 100.000. La de sólo roles no produce ofertas completas:
faltaría la asociación de campos. Son tamaños de una reconstrucción, **no**
tokens cobrados, ahorro medido, respuestas de JEV ni calidad demostrada. Las
17.497.033 bytes incluyen solicitudes nunca enviadas. JEV puede procesar el
contexto por pregunta internamente; reducir wire no garantiza la misma reducción
en tokens reportados.

### 3. Preguntas selectivas y lotes acotados

Se generan 4.848 preguntas sobre 503 ocurrencias consideradas precio; sólo 254
son monetarias con símbolo. Evitar las nueve preguntas para cada número. Resolver
exclusiones inequívocas localmente y consultar asociaciones necesarias para cada
importe comercial plausible. Para nombre, tamaño o condición ausentes, registrar
ausencia/pendiente si no hay opción útil; no pagar una selección entre opciones
sin el producto correcto. Omitir preguntas de catálogo para promociones sin importe,
referencias aisladas, nombres ausentes o candidatos incompatibles.

Evaluar tres estrategias offline antes de elegir:

| Alternativa | Beneficio | Riesgo / validación |
|---|---|---|
| Una ronda compacta por bloque, con asociaciones de importes plausibles | Menos cambios; fácil trazabilidad | Repite algunas preguntas inútiles; 125 HTTP superan el presupuesto actual aun si todos caben. No basta como diseño final. |
| Rol primero, asociaciones sólo de actuales aceptables | Reduce universo de selecciones y respuestas | Segunda ronda aumenta HTTP/latencia y no puede depender de otra respuesta en el mismo POST. Agrupar por fase; medir costo total de ambas rondas. |
| Lotes de varios bloques independientes con preguntas selectivas | Amortiza HTTP y contexto estable | Riesgo de cruzar precios/nombres, respuestas parciales y cuerpos grandes. Identificar cada target por bloque; separar/dimensionar por bytes, preguntas y opciones. |

Propuesta inicial: lotes por página con límite dinámico, máximo tentativo cinco
bloques, 64 preguntas y 70.000 bytes serializados (márgenes de diseño, sujetos a
fixtures; límites duros existentes 128/100.000). División determinista antes del
transporte; un bloque grande se divide por grupos de preguntas o queda pendiente
con motivo preciso, nunca se trunca la evidencia ni se eleva silenciosamente el
límite. Preguntar en una sola ronda cuando las exclusiones y asociaciones son
inequívocas; reservar la segunda para ambigüedad con evidencia suficiente.

En decisiones de catálogo transmitir sólo top-k admisible, nombres/marca/tamaño
y evidencia del offer; nunca catálogo completo, costos para resolver identidad ni
acciones de escritura. Agrupar decisiones de varias ofertas con targets exactos
si el contrato lo permite. Mantener aceptación automática deshabilitada mientras
no haya calibración y medición de falsos positivos.

### 4. Cobertura, reservas y ejecución reanudable

Planificar las 24 páginas antes de clasificar. Con grupos de dos, la cuota mínima
de lectura son 12 transportes, reservados para cobertura inicial. La clasificación
no puede usar esa cuota. Separar contadores de intentos locales, transportes
iniciados, reservas en vuelo, rechazos preflight y cuotas por fase. Una reserva
liberada por preflight no reduce una llamada realmente enviada; los HTTP fallidos
y timeouts enviados sí consumen cuota. Hacerlo atómico para dos workers.

Asignación inicial ilustrativa con 100 transportes: 12 lectura + 4 recuperación
visual + 60 clasificación + 24 decisiones. Ajustar al plan de lotes calculado;
si el documento exige más lectura, la cuota de lectura prevalece y se informa el
trabajo semántico que no entra. No inventar que el PDF tiene 300 ofertas: las
otras páginas no fueron observadas. Simular densidades mayor/menor que las 125
observadas antes de fijar el reparto.

Enviar los chunks de lectura tan pronto como haya slot disponible; no retener
el avance de lectura detrás de una cola de clasificación. Conservar joins y
lifetimes de PDFs temporales; un timeout de Future no libera un SDK slot que
continúa activo. Aplicar `min(timeout_lector, restante_global)` al lector y tiempos
acotados a JEV. Los checkpoints deben guardar páginas leídas, IDs/versiones y
pendientes por `business_id`/run/document, sin releer páginas sanas ni mezclar
versiones de prompts al reanudar.

Para esta clase de PDF, si el presupuesto temporal del request no alcanza,
persistir un estado parcial explícito y ofrecer continuación de etapas pendientes
con el mecanismo del backend que se apruebe. No requiere introducir Cloudflare
Workflows/Queues ni otra plataforma. No llamar «completa» a una corrida hasta
tener reporte de las 24 páginas; 24 reportes recibidos tampoco prueban fidelidad
visual, que debe medirse aparte. Un crash o cancelación no debe duplicar fichas,
transportes ni feedback; diseñar idempotencia por fase y evidencia.

### 5. Base, moneda, identidad y estado comercial

Conservar la evidencia candidata de base aun cuando `price_type=unknown`. La
confirmación determinista sólo procede con expresión literal inequívoca y
cantidad comercial acreditada si hay pack. «DESDE 3 UNIDADES» y «LLEVANDO POR
PACK» pueden describir condiciones de acceso a un precio; no prueban que el
importe sea el total de ese conjunto. Catálogo, precio de mercado y XLT no son
fuentes para deducir un pack faltante. Si no hay evidencia, route humano en vez
de repetir una consulta inútil a JEV.

Definir moneda normalizada y procedencia por separado del símbolo original.
USD/US$ explícito o contradicciones prevalecen; `$` aislado requiere contexto
documental o configuración de moneda del negocio/proveedor aprobada y visible,
con razón auditable. La convención ARS del catálogo no convierte automáticamente
el documento. Puede conservarse esta información en el contrato/metadata sin
decidir todavía una migración. Ningún ahorro, margen o recomendación de compra
se activa si identidad, moneda o base siguen pendientes.

Mejorar marca y variantes sin sobreescribir catálogo. Candidatos de otra marca
pueden ser alternativas para revisión, no identidades automáticas. La política
de aceptación probabilística necesita una evaluación separada y corpus etiquetado
antes de habilitarse. `0.95` no es una garantía calibrada y una selección cerrada
de la opción menos mala no prueba que el producto sea correcto.

Resolver identidad y precio de forma independiente: una identidad inequívoca
puede confirmarse aunque la base o moneda estén pendientes. La incertidumbre de
precio bloquea la comparación comercial, no la recuperación ni una confirmación
de identidad respaldada por evidencia. La incertidumbre de identidad tampoco
debe borrar el importe publicado literal.

## Secuencia de implementación y ownership futuro

Reparto sugerido para una implementación posterior autorizada; no se ejecutó
delegación adicional. Un solo responsable por archivo compartido y revisión del
agente principal antes de integrar cada etapa.

| Etapa | Responsable | Archivos propios | Entregable y condición para avanzar |
|---|---|---|---|
| 0. Contratos/fixtures | Coordinador, con revisión A/B | `analysis_contracts.py`, documentación de contratos; un custodio único de fixtures | Separación referencia/comercial, evidencia candidata/confirmada, moneda raw/normalizada y métricas por fase. Congelar gold de texto sin afirmar verdad visual. |
| 1. Candidatos B | Responsable B | `block_interpretation.py`, `tests/modules/procurement/test_block_interpretation.py` | Multilínea, roles numéricos, cc, marca literal, referencias de base retenidas; ningún precio XLT/XU se emite como otra oferta por sí solo. |
| 2. Adaptador JEV | Responsable B | `jev_analysis.py`, prompts JEV, `tests/providers/test_jev_analysis.py` | Serializer compacto, preflight, lotes, targets por bloque, errores específicos, decisiones válidas conservadas cuando otras son inválidas. |
| 3. Orquestación común A/B | Coordinador | `document_analysis.py`, `providers/documents/openai.py`, tests de recuperación/lector | Cuotas por fase y reservas atómicas; cobertura primero, timeout efectivo, estados reanudables e idempotencia. Coordinar los fixes de lector de A en estos mismos archivos. |
| 4. Política/matching común | Coordinador | `decision_policy.py`, `matching.py`, `identity.py`, `normalization.py`, sus tests | Evidencia candidata útil sin relajación; moneda auditable, identidad/alternativa separadas y regresiones compartidas. No dos agentes editando estos archivos. |
| 5. Integración/estado visible | Principal | `main.py`, proyección de metadata existente; UI sólo si se autoriza esa edición | Logs ledger correctos, estado parcial por fase/página; revisión integrada y medición offline aprobada antes de corrida paga. |

La etapa 3 debe empezar junto con la definición de contratos; no esperar a tener
mejor matching para resolver lectura incompleta. La calibración de aceptación
queda fuera de estas primeras correcciones. No instalar dependencias ni crear
migraciones como efecto lateral de este plan.

## Criterios de aceptación y regresiones offline

Corpus de regresión: texto real guardado, decisiones válidas archivadas y
respuestas malformadas artificiales claramente identificadas. Las expectativas
semánticas se revisan como **gold de texto**, no como verificación contra el PDF.

1. **Cobertura y presupuesto:** fake reader de 24 páginas, dos workers, latencias controladas y bloques con los textos reales. Todas las páginas reciben lectura inicial aunque se rechacen 82 clasificaciones localmente; cero HTTP para requests preflight inválidos y cero cargos al contador de transportes. Probar el límite de cuotas sin deadlocks, reservas perdidas o sobrepaso por concurrencia; si expira deadline, la continuación retoma sólo pendientes. Simular lector lento que supera 60 s sin tener que dormir realmente.
2. **Tamaño:** 125 bloques guardados y fixtures de densidad mayor. Cada POST preparado cabe bajo 100.000 bytes/128 preguntas/255 opciones; objetivo de lote ≤70.000 bytes y ≤64 preguntas. Unicode argentino, IDs, condiciones y evidence refs cuentan en bytes serializados. Ningún truncamiento silencioso. Comparar preguntas y bytes por bloque/fase antes/después.
3. **Nombres:** NATURA y HEINEKEN posteriores al primer precio disponibles; KEVIN/PAULA sin `PRECIO XLT:` dentro del nombre. Validar offsets exactos, spans contiguos y referencias compuestas, y evitar vigencia/sucursal como identidad. Repeticiones reales conservan IDs separados; relecturas no duplican ofertas ya asociadas.
4. **Precio y conteos:** en los cinco ejemplos completos, XLT/XU es referencia, tamaño no es cantidad, mínimo no es divisor y las exclusiones no cambian scope. Forzar fake JEV a elegir `current_price` para XLT: el backend conserva la interpretación rechazada, pero no crea otra ficha comercial ni confirma costo. Incluir antes/ahora, porcentajes sin importe, precio sin símbolo, dos variantes con asociaciones ambiguas y condiciones no vinculadas.
5. **Abstención y moneda:** ARS sólo con contexto admitido y procedencia; US$/USD/multimoneda no se convierten a ARS. Base desconocida sigue desconocida con probabilidad alta. Pack sin cantidad conserva pendiente; contenido x48 no habilita división comercial. Precio por referencia no confirma el envase mediante aritmética inversa.
6. **Tenant e identidad:** fake repository con productos de otro negocio, marcas/variantes contradictorias, producto inactivo y feedback incompatible. Ninguna aceptación fuera de `business_id`; shortlist top-k no confirma vínculo. El método B corregido debe recuperar candidatos literales NATURA/KEVIN/KIMBIES en la exportación actual, con abstención de los ambiguos.
7. **Respuestas parciales:** missing choice, opción extra, probabilidades negativas/no finitas/suma incorrecta, máximo distinto, claves duplicadas, envelope/model incorrecto. Guardar códigos distintos y sólo respuestas válidas. La ausencia de respuesta no transforma `none` en valor ni obliga relectura visual. Afirmaciones históricas sobre los siete rechazos se mantienen indeterminadas.
8. **Integración comercial:** tras persistir en un repositorio fake, nueve propuestas actuales continúan sin costos comparables; corregir fuente no retroconfirma la corrida histórica. GET/revisión muestra páginas no leídas y motivos separados. No ejecutar escrituras desde el proveedor ni usar IA para reglas de margen/costo.

Métricas de aceptación sugeridas: cobertura inicial 24/24 en fakes dentro del
plan temporal simulado, cero fallos locales de tamaño sin partición/razón útil,
cero ofertas de referencia en gold, cero cantidades inventadas, cero vínculos
fuera de tenant y cero costos comparables con base/moneda pendientes. Para costo,
comparar tokens de **todos** los HTTP por fase y proveedor sobre la misma cobertura;
tarifas/versiones vigentes deben verificarse cuando se autorice el piloto. No fijar
un porcentaje de ahorro monetario a partir de bytes.

## Observabilidad y medición pendiente

Registrar en metadata, sin claves ni secretos: source/config/prompt/schema/policy
hashes, fingerprint del catálogo consultado, fase, bloque/página, pregunta target,
payload bytes, opciones/preguntas, decisión de partición, razón preflight,
transport_started, reserva liberada/consumida, espera de slot, duración de HTTP,
uso/costo conocido o desconocido, campos pendientes y aceptación de política.
No sumar fallos locales como HTTP ni representar usage desconocido como cero.

Para invalid answers, registrar primero razón exacta y valores relevantes
sanitizados; si se necesita cuerpo de diagnóstico, limitar bytes, separar del log,
proteger por tenant, retención y acceso. No guardar cabeceras de autorización.
El texto puede ser sensible y contener prompt injection; no publicarlo ni tratarlo
como instrucciones. La instrumentación no debe replicar todos los cuerpos en cada
entrada ni agotar el límite de metadata de 4 MB.

Resultado visible sugerido: «Lectura: 10/24 páginas. Organización: 13 consultas
JEV, 82 solicitudes bloqueadas localmente. 9 fichas preliminares; 0 identidades y
0 bases confirmadas. 14 páginas sin leer y 125 bloques requieren revisión».
La cantidad de review pages no sustituye cobertura: el ledger actual marca 16
páginas para revisión, mientras las 125 ofertas/bloques siguen pendientes.
En A, los seis bloques pendientes tampoco representan las 20 páginas sin
resultado; ambos métodos necesitan páginas con resultado frente a total y causa
del corte en la proyección visible, bajo ownership del coordinador.

## Dependencias con Caso A

Compartir sin duplicar investigación: timeout del lector, presupuestos y cobertura,
contratos de evidencia/moneda, validación determinista de precio/base, recuperación
visual, catálogo y gates de identidad. Mantener específica de B la selección de
candidatos textuales, serializer JEV, clasificación de referencias y lotes de
preguntas. No trasladar a B el supuesto de que las 49 ofertas del A son correctas
ni usar su salida como verdad del PDF; cada método conserva su procedencia.

El custodio de archivos comunes integra ambos planes con un corpus compartido;
evitar implementaciones alternativas de normalización o matching. La corrección
de B debe pasar los guards de A y la de A no debe activar aceptación automática
de propuestas B que sólo tengan nombre/precio preliminar.

## Verificaciones realizadas y reproducibilidad

Scratch exclusivo `output/diagnostics/investigar_jev_vital/`:

- `audit_case_b.py` y `audit_case_b_results.json`: 95/95 conjuntos de preguntas iguales a lo guardado; 82/95 exceden 100.000 bytes y coinciden uno a uno con `jev_request_limit`; todos los tamaños coinciden con la reconstrucción previa. Transporte fake de NATURA nunca ejecutado; reserva permanece en 1, contador HTTP en 0.
- `audit_policy_matching.py` y `audit_policy_matching_results.json`: repositorio fake sobre la exportación actual; cinco de 16 propuestas toman ruta `needs_jev`, dos `needs_visual_review`, nueve `needs_human_review`. Las cinco `needs_jev` preparan identidad y base aun sin `price_type` refs gracias a identidad admisible; otras dudas de precio sin ese respaldo no preparan JEV. No se infiere que el catálogo histórico produjo exactamente esos mismos shortlists.
- Tests existentes focalizados: **143 passed**, una advertencia de deprecación de Starlette/httpx; sin instalación ni cambios para resolverla. Estos tests prueban guards actuales, no el diseño propuesto ni la fidelidad de la corrida real.

Comandos ejecutados desde la raíz, sin red de inferencia ni DB:

```powershell
.venv/Scripts/python.exe -B output/diagnostics/investigar_jev_vital/audit_case_b.py
.venv/Scripts/python.exe -B output/diagnostics/investigar_jev_vital/audit_policy_matching.py
.venv/Scripts/python.exe -B -m pytest tests/providers/test_jev_analysis.py tests/modules/procurement/test_block_interpretation.py tests/modules/procurement/test_document_decision_policy.py -q -p no:cacheprovider --basetemp output/diagnostics/investigar_jev_vital/pytest_tmp
```

Se consultaron únicamente páginas públicas oficiales para las consideraciones
de OpenAI: el PDF incorpora texto e imágenes al contexto, lo que afecta tokens;
el ahorro de caching debe medirse sobre el modelo/configuración reales y no
trasladarse automáticamente a JEV. [File inputs](https://developers.openai.com/api/docs/guides/file-inputs),
[Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).
No se invocó inferencia, upload, reprocesamiento, instalación, migración ni envío
a otras conversaciones. Archivos entregados: este plan y los cuatro artefactos
de scratch anteriores; no se modificó runtime, tests, env ni datos comerciales.
