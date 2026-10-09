# Corrida d579: corte de páginas y observabilidad de ambos métodos

Consulta del 8 de octubre de 2026. Se verificó por API local que
`d579186114ec43379f82e3845acd066d` es el último documento de `demo-business`,
guardado a las 15:55:36 de Argentina. El informe anterior sobre `682edfd1…`
corresponde a otra corrida. No se volvió a ejecutar inferencia ni se modificaron
resultados comerciales o configuración durante esta revisión.

## Resultado y causa comprobada

El backend dejó de enviar páginas por el plazo de lectura, no por falta de
división del PDF ni por agotar llamadas. De las 24 páginas, solo 2 y 3 aportaron
resultado. Las 1 y 4 fallaron; las 5–24 nunca se enviaron.

| Página | Resultado | Duración de llamada |
|---|---|---:|
| 1 | APITimeoutError | 123,922 s |
| 2 | Éxito | 116,157 s |
| 3 | Éxito | 95,235 s |
| 4 | APITimeoutError | 52,984 s |
| 5–24 | `reading_deadline_exhausted` | Sin llamada |

Configuración guardada: análisis de 180 s, último inicio de lectura a los 144 s
(80 %), dos lectores, una página por solicitud en A. Solo se reservaron 4 de las
60 llamadas de lectura y 9 de las 40 de decisión. No hubo relecturas.

Las solicitudes 1/2 arrancaron en paralelo; al liberarse lectores se iniciaron
3/4. Cuando volvió a existir lugar ya había vencido el plazo de inicio. La cola
restante se marcó pendiente y se vació. La corrida duró 222,422 s porque el
orquestador espera las llamadas activas antes de eliminar los PDFs temporales:
180 s es un presupuesto para iniciar tareas, no una cancelación estricta de toda
la petición.

El timeout de transporte guardado es 60 s, pero tres llamadas duraron más.
Ese valor no acredita un límite total de pared de 60 s. No hay evidencia suficiente
para atribuir la diferencia a red, preparación, SDK o espera del proveedor: el
histórico solo conserva la clase de excepción, sin causa ni desglose de etapas.
El código actual configura `max_retries=0`; no corresponde asumir retries ocultos
solo por observar duraciones largas. La documentación oficial explica configuración
de timeout, excepciones, retries y request IDs:
[SDK Python de OpenAI](https://developers.openai.com/api/reference/python).

## Páginas, bloques y ofertas son conteos distintos

El PDF sí se divide en solicitudes antes de leerlo. Los bloques comerciales se
obtienen de la respuesta de OpenAI; no existen bloques recuperados de páginas
que nunca se enviaron. En A se planearon 24 solicitudes de una página. Las dos
respuestas útiles devolvieron 21 bloques: se guardaron 13 ofertas y 8 bloques
sin oferta utilizable. Estos 8 pendientes no representan las 20 páginas omitidas.

Hubo 9 llamadas exitosas a JEV de decisión y 4 decisiones diferidas por
`decision_deadline_exhausted`. Éxito de transporte no implica confirmación comercial.
La política guardada deshabilita aceptación automática de elecciones JEV
(`allow_jev_acceptance=false`). También existen propuestas sin candidatos, citas
de precio inválidas y bases desconocidas. Las 13 bases necesitan revisión; 9
identidades están pendientes, y ninguna de las 13 ofertas tiene vínculo confirmado.
Las 6 coincidencias del resumen son sugerencias de matching, no vínculos aceptados.

## Comparación de los dos métodos actuales

| Etapa | OpenAI separa los datos (A) | OpenAI lee y JEV organiza (B) |
|---|---|---|
| División | 1 página por solicitud por defecto | 2 páginas por solicitud |
| Lectura | OpenAI devuelve bloques y propuestas con citas | OpenAI devuelve bloques y fragmentos |
| Organización | Backend valida propuestas OpenAI | Backend detecta opciones; JEV clasifica; backend arma campos |
| Catálogo/precio | Reglas y JEV selectivo | Mismas reglas y JEV selectivo |
| Cupo sobre 100 llamadas | Lectura 60 / clasificación 0 / decisión 40 | Lectura 35 / clasificación 40 / decisión 25 |
| Inicio máximo con 180 s | Lectura 144 / decisión 176,4 s | Lectura 144 / clasificación 165,6 / decisión 176,4 s |
| Concurrencia | 2 lectores OpenAI + 1 tarea JEV | Igual; clasificación y decisión comparten el worker JEV |

Ambos métodos tienen el mismo riesgo de dejar páginas sin enviar. B además necesita
una o más llamadas de clasificación por bloque; 40 llamadas no garantizan clasificar
un folleto de cientos de bloques. El cliente puede dividir preguntas en varias
peticiones, que también consumen ese cupo. JEV no recibe el PDF ni puede recuperar
el contenido de páginas omitidas.

La última corrida B guardada sigue siendo `4a3058e1…`, del 7 de octubre a las
11:23:33 de Argentina. Usó una implementación anterior: leyó 10 páginas y dejó
14 sin enviar por agotar reservas compartidas. Hubo 82 rechazos locales por tamaño
de mensaje entre 95 clasificaciones; solo 13 consultas JEV reales y ninguna de
decisión. Su diagnóstico está en `DIAGNOSTICO_CORRIDA_JEV_2026_10_07.md`.
El código actual separa cupos y hace preflight antes de reservar transporte;
esa corrida antigua no valida las correcciones ni el rendimiento actual de B.

## Logs añadidos en esta revisión

Se emiten con nivel INFO en `uvicorn.error`, correlacionados por `run_id`:

- `document_analysis started`: método, páginas, solicitudes, workers, cupos y plazos.
- `reader_started` / `reader_finished`: rango, intento, duración, estado y bloques.
- `reading_stopped`: razón exacta, tiempo transcurrido, rangos sin enviar y reservas.
- `jev_scheduled` / `jev_finished` / `jev_deferred`: fase, bloque, estado y motivos.
- `document_analysis finished`: cobertura, fallos, pendientes, ofertas y llamadas.
- `openai_reader started/finished`: timeout efectivo, bytes del archivo, duración,
  clase de excepción y causa inmediata, request/response ID validado y usage conocido.

No se registran prompts, texto comercial, claves, cabeceras o mensajes crudos de
excepciones. Los logs necesitan que el proceso cargue el código actualizado y no
reconstruyen etapas de corridas pasadas. Todavía no desglosan upload/espera/parseo.

Se probaron ambos métodos con un reloj simulado que excede el plazo de lectura
y el global, conservando respuestas activas, marcando páginas omitidas y verificando
los eventos. Los tests del lector verifican timeout efectivo, causa y sanitización.

## Corrección funcional pendiente

Agregar logs explica el corte, pero no completa la cobertura. Para PDFs largos,
conviene separar la lectura inicial completa del presupuesto de organización y
decisión, procesar por lotes con checkpoints y permitir continuar páginas/tareas
pendientes. Hoy `continuation_available=false`; recargar el PDF inicia otra corrida.
Aumentar llamadas no resuelve este corte temporal, y aumentar solamente el timeout
del lector puede empeorarlo si el plazo global sigue igual. Los límites deben
dimensionarse y medirse juntos sobre documentos representativos.
