# Última corrida Vital — estadísticas y diagnóstico

Consultada el 8 de octubre de 2026. Último documento guardado del negocio
`demo-business`: `682edfd1833e4907a506e73fc5a55e5f`. Proveedor Vital 2,
archivo `116561.pdf`, método OpenAI separa los datos. Guardado el 7/10 a las
20:18:47 de Argentina. No se volvió a procesar el archivo ni se llamó a inferencia.

## Resultado

**Fallo completo de lectura.** Ninguna respuesta aportó páginas, bloques o artículos.
El estado del documento es `failed`; el resumen del análisis figura `partial`.
La búsqueda de productos y JEV no llegaron a ejecutarse.

| Métrica | Valor |
|---|---:|
| Duración del análisis | 178,032 s (2 min 58 s) |
| Páginas del PDF | 24 |
| Páginas intentadas | 6 (25 %) |
| Páginas con resultado | 0 (0 %) |
| Páginas intentadas sin resultado | 6 (25 % del documento, 100 % de lo intentado) |
| Páginas nunca enviadas | 18 (75 %) |
| Bloques / ofertas / candidatos guardados | 0 / 0 / 0 |
| Llamadas OpenAI exitosas / fallidas | 0 / 6 |
| Consultas JEV / relecturas | 0 / 0 |
| Errores timeout / conexión | 5 / 1 |
| Usage conocido | Ninguna de las seis llamadas |
| Costo | Desconocido; no equivale a cero |

Los contadores de identidades/bases/bloques pendientes son cero porque no se extrajo
nada que pudiera pasar a esas etapas. No significan que el documento esté resuelto.
Las 24 páginas están marcadas para revisión.

## Intentos por página

| Página | Error guardado | Duración |
|---|---|---:|
| 1 | APIConnectionError | 46,468 s |
| 2 | APITimeoutError | 63,453 s |
| 3 | APITimeoutError | 60,907 s |
| 4 | APITimeoutError | 61,828 s |
| 5 | APITimeoutError | 61,109 s |
| 6 | APITimeoutError | 52,391 s |

Promedio por llamada: 57,693 s. Las llamadas se superponen con hasta dos lectores;
la suma de sus duraciones no es el tiempo total del análisis. Todas son fase inicial,
modelo `gpt-5.6-luna`. No hay respuestas guardadas ni IDs de respuesta ni usage.

## Dónde se interrumpió el flujo

El backend generó seis solicitudes de una página. El ledger registra seis
`reader_failed`. Para páginas 7–24 registra `reading_deadline_exhausted`: quedaron
sin envío. Hay cero bloques, interpretaciones y auditorías de decisión, y conjuntos
vacíos de candidatos/evaluaciones de policy. No es evidencia de un fallo de fuzzy,
de identificación de nombres ni de JEV en esta ejecución.

Configuración efectiva guardada: timeout por lector 60 s, análisis global 180 s,
último inicio de lectura al 80 % del plazo (144 s), dos lectores, tamaño de chunk A
de una página. Cupos: 60 llamadas lector/40 decisiones, de los que sólo se reservaron
seis de lectura. El corte fue temporal, no por agotar el máximo de llamadas.
El código actual usa `max_retries=0`; no hubo retries automáticos ni relecturas.

Los cinco timeouts concuerdan con el límite de transporte aplicado. El cambio para
hacer efectivo ese límite no está funcionando para esta carga. No alcanza para
asegurar que todo se resuelve aumentándolo: también hubo un error de conexión.
El ledger conserva sólo la clase de excepción; falta la causa de transporte para
separar conexión, escritura/upload y espera/lectura de respuesta.

La documentación oficial distingue errores de conexión y timeout y permite configurar
el timeout. [SDK oficial](https://github.com/openai/openai-python#handling-errors),
[timeouts](https://github.com/openai/openai-python#timeouts).
Las causas específicas y cifras de este informe proceden de datos y código locales.

## Comparación con la anterior del mismo método y archivo

| Métrica | Anterior `dbea1644…` | Última `682edfd1…` |
|---|---:|---:|
| Páginas con resultado | 4/24 | 0/24 |
| Ofertas guardadas | 49 | 0 |
| Llamadas OpenAI exitosas / total | 2/4 | 0/6 |
| Duración | 237,125 s | 178,032 s |
| Chunk | 2 páginas | 1 página |

La anterior había obtenido dos respuestas en 121,375 y 111,594 s, superiores a 60 s,
aunque con dos páginas por solicitud. La última termina antes y recupera menos datos;
no demuestra ahorro monetario ni calidad mejorada. No es un experimento controlado:
se modificaron chunk/timeout/orquestación/prompt y puede haber variado la red/proveedor.

## Prioridades que surgen de la evidencia

1. Registrar causas sanitizadas de timeout/conexión y tiempos de upload/espera/parseo,
   sin guardar claves, cabeceras ni cuerpos sensibles en consola.
2. Revisar el presupuesto de lectura de PDFs densos con una página representativa;
   no seguir usando 60 s como garantía de que la lectura puede terminar ni bajar
   aún más el tiempo. Comparar cobertura además de duración.
3. Completar continuación por páginas y estado de fallo en la interfaz. Hoy el
   checkpoint explicita `continuation_available=false` y lista las 24 pendientes.

Snapshots locales: `output/diagnostics/latest-run-2026-10-08-full.json` y
`output/diagnostics/latest-summaries-2026-10-08.json`. No se cambió configuración,
runtime, servidor ni datos comerciales para esta revisión.
