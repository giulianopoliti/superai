# Diagnóstico de la corrida «OpenAI lee y JEV organiza»

Fecha: 7 de octubre de 2026, Argentina.
Documento: `116561.pdf`, 24 páginas, 22.706.664 bytes según el log de carga.
Documento/run: `4a3058e1c8a84cbbaf260f44bec7e83b`.
Negocio consultado: `demo-business`.

## Resultado

La corrida real expuso errores de implementación. La lectura no llegó al final y
la clasificación de gran parte del texto se rechazó antes de consultar JEV.
El HTTP 200 acredita que se guardó el resultado parcial; no acredita una lectura
completa ni ofertas confirmadas.

| Dato observado | Resultado |
|---|---:|
| Páginas del PDF | 24 |
| Páginas con lectura inicial registrada | 10 |
| Páginas que nunca se leyeron | 14, de la 11 a la 24 |
| Bloques de texto conservados | 125 |
| Intentos de clasificación registrados | 95 |
| Rechazos locales por tamaño de mensaje | 82 |
| Llamadas reales a OpenAI | 5, grupos de dos páginas |
| Llamadas reales a JEV | 13, todas de clasificación |
| Respuestas JEV completas / parciales | 8 / 5 |
| Llamadas JEV para decidir catálogo o base pendiente | 0 |
| Interpretaciones preliminares | 16 |
| Fichas guardadas | 9, correspondientes a 7 bloques |
| Identidad / base de precio confirmadas | 0 / 0 |
| Bloques pendientes | 125 |
| Relecturas | 0 |
| Análisis / petición total | 167,625 s / 184,015 s |

Los 125 bloques no equivalen a 125 artículos confirmados: incluyen promociones
sin importe y texto que necesita revisión. No se dispone aquí del PDF original
para evaluar fidelidad visual u omisiones dentro de las páginas transcritas.

## Causas comprobadas

### 1. Mensajes JEV demasiado grandes — crítico

`JevAnalysisClient` limita cada petición a 100.000 bytes. El adaptador repite el
prompt completo en las instrucciones de cada pregunta, junto con numerosas
opciones descriptivas. Se generan preguntas de precio también para números de
tamaño y compra mínima: en el primer bloque se consideran `3`, `$3.649`, `900`
y `$4054.44`, produciendo 37 preguntas.

Reconstrucción offline con bloques y preguntas guardados, retirando motivos e IDs
de ofertas añadidos después: 82 mensajes superan el límite y coinciden exactamente
con los 82 rechazos `jev_request_limit`. Rango reconstruido: 35.291–343.656 bytes.
El primer mensaje mide aproximadamente 128,6 KB. Son tamaños reconstruidos;
las peticiones crudas originales no fueron archivadas. No hubo HTTP en la reproducción.

Código: `app/providers/jev_analysis.py:129`, `:188`, `:491`;
`app/modules/procurement/block_interpretation.py:150`.

### 2. Rechazos locales consumen presupuesto de lectura — crítico

El presupuesto compartido reserva un intento antes de la clasificación y no lo
libera cuando el cliente rechaza el mensaje sin enviarlo. Cinco lecturas más 95
clasificaciones agotan las 100 reservas, aunque sólo hubo 18 llamadas reales.
La clasificación se ejecuta entre grupos de lectura, por lo que sus fallos impiden
enviar las páginas restantes a OpenAI. Los registros de páginas 11–24 contienen
`call_budget_exhausted`; sus `reading_ids` están vacíos. Las 30 clasificaciones
restantes y las decisiones posteriores tampoco pudieron reservar una llamada.

El corte observado fue por llamadas, no por timeout: la duración de análisis está
por debajo del plazo de 180 s. Ese plazo también debe evaluarse con PDFs de 24
páginas; corregir el contador no garantiza terminar todo el PDF dentro de él.

Código: `app/modules/procurement/document_analysis.py:107`, `:724`.

### 3. El nombre correcto no aparece entre las opciones — crítico

OpenAI transcribió muchas ofertas como un fragmento que contiene varias líneas.
El detector de candidatos de texto conserva sólo el segmento anterior al primer
precio; no vuelve a buscar el nombre después del importe.

Por ejemplo, el bloque del aceite contiene `NATURA`, `Aceite girasol` y `x900ml`,
pero sus opciones de nombre son `DESDE 3 UNIDADES` y una condición de vigencia.
JEV no puede elegir un nombre que no está en su lista. En HEINEKEN se guardó
`LLEVANDO POR PACK` como nombre. En otros bloques los nombres arrastran
`PRECIO XLT:`, lo que perjudica la búsqueda del catálogo.

Código: `app/modules/procurement/block_interpretation.py:87`.

### 4. Precio del envase y precio de referencia mezclados — alto

Se guardaron dos fichas del mismo bloque para PAULA y KEVIN: una con el precio
del envase y otra con el precio por litro. KEVIN x150ml figura por `$3.889` y
por `$25.926,67`; el texto identifica el segundo como `PRECIO XLT`.
No son dos ofertas distintas del envase. Esos duplicados ayudan a explicar que
las nueve fichas sólo representan siete bloques.

También aparecen tamaños propuestos como contenido/cantidad. Las validaciones
impidieron confirmar esos datos, pero no resuelven la interpretación inicial.

### 5. Moneda y confirmación impiden comparar — alto

Las 16 interpretaciones tienen moneda `$`, identidad pendiente y precio pendiente.
Las fichas conservan `$`; el matching exige `ARS` para comparar con el catálogo
y registra `currency_not_comparable`. Esto requiere normalización contextual para
folletos argentinos, conservando el símbolo original; no asumir ARS si el documento
indica dólares u otra moneda.

La política de base de precio exige expresiones explícitas como «por unidad» o
«precio unitario». Muchos bloques sólo muestran un precio y una presentación;
siguen desconocidos. Además, la aceptación por probabilidades JEV está desactivada
en la política persistida. En esta corrida ni siquiera hubo decisiones JEV de
identidad/precio: el presupuesto se agotó antes. Aumentar sólo la aceptación
automática no arreglaría estas causas y podría confirmar precios de referencia.

## Consumo informado por los proveedores

| Proveedor | Entrada | Salida | Total |
|---|---:|---:|---:|
| OpenAI | 53.165 | 13.226 | 66.391 |
| JEV | 429.882 | 52.283 | 482.165 |
| Total | 483.047 | 65.509 | 548.556 |

Estos son tokens reportados y guardados, no una factura ni una estimación monetaria.
No se puede concluir ahorro con esta corrida incompleta ni comparar costo contando
igual los tokens de dos proveedores con tarifas distintas. JEV domina el consumo
reportado: la repetición de contexto y preguntas necesita simplificación.

## Registros disponibles y límites

Se consultaron los GET de análisis/matches y la metadata del documento mediante
SELECT filtrado por negocio e ID. Se guardaron copias locales en
`output/diagnostics/jev-run-4a3058e1-{analysis,full,matches,payload-sizes}.json`.
La metadata permite consultar páginas, texto, opciones, respuestas válidas,
probabilidades, estados y llamadas con modelos/usage/duración.

La consola imprime `provider_calls=unknown` porque consulta claves del flujo
anterior, en lugar del ledger nuevo. Hay conteos disponibles, pero el log no los
usa. Las cinco respuestas parciales registran preguntas rechazadas como
`invalid_answer`; no guardan el contenido crudo rechazado ni la condición específica
de validación que falló. No se puede atribuirlas todas a un motivo más preciso.

## Corrección recomendada, en orden

1. Preparar candidatos útiles de nombre, precio publicado, referencia por kg/litro,
   presentación y mínimo, soportando fragmentos con varias líneas sin omitir texto.
2. Hacer compacto el contexto JEV y limitar las preguntas a decisiones necesarias;
   validar tamaño antes de reservar llamadas. Usar lotes acotados si hace falta,
   sin convertir todos los números en ofertas ni elevar el límite sin control.
3. Reservar presupuesto para cubrir todas las páginas; separar límites de lectura
   y clasificación. Los rechazos locales no deben contarse como llamadas externas.
4. Normalizar moneda con contexto y decidir la base de precio sobre evidencia,
   sin confundir precio por litro, mínimo, contenido y pack. Conservar abstención.
5. Registrar etapa, páginas leídas/pendientes, llamadas reales, rechazos locales,
   tamaños y razones específicas de validación; mostrarlos en el resultado parcial.
6. Reproducir estos casos con el texto y las respuestas guardadas antes de otra
   corrida paga. Medir cobertura real y luego precisión de decisiones aceptables.

Este diagnóstico no modifica ofertas ni confirma candidatos. Las pruebas offline
previas demostraban integración y algunas conservaciones; no cubrieron el formato
y volumen que provocaron estos fallos. El método B necesita corrección antes de
considerarlo listo para estos folletos.
