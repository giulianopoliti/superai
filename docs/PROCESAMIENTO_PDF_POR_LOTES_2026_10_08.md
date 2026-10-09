# Procesamiento de PDFs por lotes del backend

Implementado el 8 de octubre de 2026 para los dos métodos V2.

## Problema corregido

En la corrida `d579186114ec43379f82e3845acd066d`, el backend dejó de enviar
las páginas 5–24 al vencer su plazo de inicio de lecturas a los 144 segundos.
OpenAI nunca recibió esas páginas. Las 1 y 4 sí fueron enviadas y devolvieron
`APITimeoutError`; la causa concreta de esos timeouts no consta en el histórico.
Las 2 y 3 fueron exitosas. El corte de cola fue una limitación de nuestra
orquestación, distinta de los errores de transporte.

## Nuevo comportamiento

El backend corta físicamente el PDF antes de enviar contenido a OpenAI:

- A: PDF de una página por solicitud. Con dos lectores, un lote cubre dos páginas.
- B: PDF de hasta dos páginas por solicitud. Con dos lectores, un lote cubre
  hasta cuatro páginas.
- Con un lector, cada lote contiene una sola solicitud. Si el cupo de lectura
  es menor que los slots, se reduce también la cantidad de solicitudes del lote.

Para un PDF de 24 páginas y dos lectores se ejecutan 12 lotes en A o 6 en B.
No se pide a OpenAI dividir el documento. Se envía cada PDF recortado y el backend
conserva la correspondencia entre páginas locales y páginas del original.

Los transportes de cada lote terminan antes de empezar el siguiente. Al avanzar,
se renuevan deadlines y cupos, incluido el límite interno del cliente JEV, sin
reiniciar los contadores acumulados ni perder la evidencia anterior. Se conserva
la concurrencia máxima de dos lectores y una tarea JEV.

Las solicitudes iniciales de cada lote se intentan aunque se agote el tiempo
durante ese lote. Un timeout o fallo temprano no borra resultados ni bloquea
lecturas posteriores. Las clasificaciones/decisiones que excedan el presupuesto
quedan pendientes y no se presentan como confirmadas. Las relecturas siguen
siendo limitadas y opcionales dentro del presupuesto final disponible.

`DOCUMENT_ANALYSIS_TIMEOUT_SECONDS` y `DOCUMENT_ANALYSIS_MAX_CALLS` se aplican
por lote. El tiempo y el número total de llamadas de un documento pueden superar
esos valores. El timeout individual de transporte OpenAI se conserva.

## Auditoría y límites

La metadata `analysis_execution` usa `bounded_batch_pipeline_v2`, incluye
`budget_scope=batch`, cantidad de lotes y rangos/duración/reservas de cada uno.
Los totales de llamadas, reservas y usage agregan todos los lotes. Un usage
desconocido de una llamada fallida sigue haciendo desconocido el total; no se
reemplaza por cero ni se pierde al renovar el presupuesto.

Los eventos `batch_started` y `batch_finished` complementan los logs por lector
y JEV. Los resultados guardados anteriores no cambian. El proceso debe cargar
el código actualizado para usar este recorrido.

La importación continúa siendo una petición HTTP síncrona. No se implementaron
jobs durables ni continuación de un proceso interrumpido; un fallo del servidor
o límites externos de HTTP siguen siendo límites distintos. No se realizó una
nueva corrida paga en esta tarea.

## Verificación

Se verifican PDFs de 24 páginas con ambos métodos, lectores lentos que consumen
más que el presupuesto de cada lote, fallos iniciales, uno/dos lectores y un
cupo de una sola llamada. Se comprueba que las 24 páginas se intentan, que el
fallo temprano permanece en el ledger, que los archivos temporales sobreviven
a las llamadas activas, que se conservan todos los totales y que JEV renueva
su cupo por lote sin reiniciar el contador total.

La suite seleccionada de orquestación, JEV, lector OpenAI, API y aceptación
pasó 165 pruebas. Ruff verificó los archivos Python modificados.
