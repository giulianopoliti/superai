# Plan integrado de corrección: las dos corridas de Vital

**Estado posterior:** se implementó y verificó una primera entrega local.
Consultar [correcciones y pendientes](CORRECCIONES_VITAL_2026_10_07.md).
El documento siguiente conserva el diagnóstico y el plan original; no implica
que todas sus etapas estén terminadas.

7 de octubre de 2026. Investigación y planificación; no modifica el funcionamiento
ni los documentos guardados. Dos investigadores independientes arrancaron sin
historial de conversación, uno por método. El coordinador integra sus hallazgos.

## Evidencia de las dos corridas

Negocio: `demo-business`. Proveedor: Vital 07/10. Archivo: `116561.pdf`, 24 páginas.

| Resultado guardado | OpenAI separa los datos (A) | OpenAI lee y JEV organiza (B) |
|---|---:|---:|
| Documento/run | `dbea1644fa0b4b7f8c021b0b63abb415` | `4a3058e1c8a84cbbaf260f44bec7e83b` |
| Páginas con resultado de lectura | 4 | 10 |
| Páginas sin resultado | 20 | 14 |
| Bloques guardados | 55 | 125 |
| Fichas guardadas | 49 | 9, de 7 bloques |
| Bloques pendientes | 6 | 125 |
| Identidades / bases de precio pendientes | 49 / 49 | 9 / 9 |
| Llamadas OpenAI | 4: 2 correctas, 2 timeout | 5 correctas |
| Llamadas JEV | 0 | 13 de clasificación |
| Rechazos locales JEV por tamaño | 0 | 82 |
| Duración del análisis | 237,125 s | 167,625 s |
| Corte predominante | Plazo agotado | Reservas de llamadas agotadas |

Las páginas A 5–8 se intentaron sin resultado; 9–24 nunca se enviaron. B 11–24
nunca se enviaron. Páginas que necesitan revisión no equivale a páginas sin leer:
A marca 22 para revisión, incluyendo dos leídas con cobertura incierta.

La fuente son snapshots de metadata, análisis y candidatos guardados. El catálogo
exportado contiene 3.260 productos del negocio; se tomó después de ambas corridas
y no garantiza que todas las filas fueran idénticas durante cada ejecución.
Los hashes de la política guardada coinciden con el código revisado. Esta investigación
no inspeccionó visualmente el PDF original: no certifica lectura completa ni identidad
de cada oferta, y no permite contar artículos de las páginas sin resultado.

## Qué falló

En A, OpenAI extrajo muchos nombres y precios útiles. El backend dejó coincidencias
pendientes por exigir igualdad de todas las palabras y por depender del puntaje
de similitud incluso cuando hay una identidad canónica exacta. El orden de palabras
puede perjudicar al producto correcto. No hubo decisiones de JEV: el plazo se agotó.

En B, el backend genera opciones de nombre incompletas, considera tamaños/mínimos
como posibles precios y envía preguntas con contexto repetido. Los rechazos locales
consumen el presupuesto reservado para llamadas y frenan la lectura del PDF.
También mezcla referencias por litro con el precio publicado del envase.
Hubo selecciones incorrectas de JEV entre las opciones disponibles: referencias XLT
como precio actual y tamaños como conteo de piezas. Eso requiere corregir roles,
opciones y validación; no atribuir todo a JEV ni afirmar que todas sus respuestas
fueron correctas. Ninguna consulta JEV decidió identidad de catálogo en estas corridas.

Ambos caminos tienen validaciones demasiado amplias o acopladas: una cita opcional
inválida puede retirar toda una propuesta; una cita de precio repetida puede borrar
un importe; una base no confirmada se informa como evidencia de precio no verificada.
Identidad del producto, importe publicado, base del precio y posibilidad de comparar
deben tener estados y motivos independientes.

## Secuencia de implementación propuesta

### 1. Congelar ejemplos y registrar la causa exacta

Crear fixtures pequeños de los bloques y las propuestas recibidas, los candidatos
de catálogo y las respuestas válidas guardadas. Conservar IDs de origen y fecha de
snapshot, omisiones y errores; no presentarlos como respuestas correctas etiquetadas.
No usar el resultado anterior como etiqueta humana de identidad/precio.

Separar en auditoría: lectura, propuesta, validación por campo, recuperación de
candidatos, decisión, comparación y presentación. Registrar puntaje, vetos y margen
de candidatos internos, las razones de abstención y los errores concretos del proveedor.

Primera entrega recomendada: reparar cobertura/presupuestos y validación por campo,
junto con el camino de identidad exacta; mantener A como opción predeterminada.
Preparar las correcciones específicas B en paralelo después de cerrar contratos,
con archivos exclusivos. Comparar ambos sobre la misma cobertura antes de decidir
si B debe ser predeterminado: 49 fichas frente a 9 no mide precisión ni ahorro.

### 2. Recuperar cobertura y controlar el trabajo por etapas

Dar presupuesto explícito a lectura, clasificación y decisiones. Validar solicitudes
antes de reservar llamadas externas; un rechazo local debe registrarse sin consumir
el cupo del lector. Evitar que clasificar el principio impida leer el final.

Separar timeout de transporte, plazo para iniciar tareas, plazo de análisis y espera
de tareas activas. Hoy V2 pasa el tiempo restante global como timeout del lector e
ignora su límite configurado de 60 s; esperar futures puede extender la petición.
Mantener hasta dos lectores reales y no borrar temporales ni iniciar reemplazos
mientras una tarea anterior siga activa.

Evaluar una salida más compacta y grupos de una página para páginas densas contra
el esquema actual de dos. Una página puede reducir salida/timeout, pero duplica
parte del overhead; se decide con mediciones, no por suposición.
Si una lectura completa necesita varios minutos, diseñar continuación y progreso
explícitos conservando resultados y archivo/fingerprint; no prometer 24 páginas en
180 s ni agregar infraestructura de colas por defecto.

### 3. Confirmar identidades claras con reglas y mejorar los candidatos

Priorizar identidad canónica única dentro del negocio: marca/producto, tamaño,
unidad equivalente, variante explícita y presentación compatibles. Un cambio de
orden de palabras no debe desplazar un producto canónico exacto por otro parecido.
Las correcciones humanas y rechazos siguen teniendo prioridad y deben revalidarse.

Normalizar abreviaturas/unidades y palabras descriptivas con reglas versionadas y
alcance controlado. No quitar todas las variantes o presentaciones para mejorar score.
Mantener un camino exacto independiente del ranking aproximado y otro para alias
o decisión asistida. Duplicados exactos, variantes no especificadas, múltiples productos
y falta de información siguen siendo motivos de abstención.

Casos de partida: NATURA mayonesa 125 g con «sachet» adicional; AYUDIN original
2 L con palabras reordenadas; harina FAVORITA 000 frente a 0000. Controles: CIF
genérico frente a limón, AYUDIN original frente a lavanda, SKIP 3 L frente a 800 ml
y DOVE reconstrucción frente a reparación. No confirmar estos controles por score solo.

### 4. Conservar campos útiles y separar los tipos de precio

Validar cada campo por separado. Una cita fallida de impuestos no debe retirar
nombre/precio respaldados; limpiar el campo inválido y conservar su propuesta en
auditoría. Reparar el caso donde `L'OREAL` después de un precio se interpreta como
unidad «l»; validar unidades dentro de límites del importe, no nombres posteriores.

Resolver ocurrencias de precio por ubicación/contexto. Dos citas del mismo importe
no deben causar pérdida automática si se puede demostrar cuál es publicada y cuál
de referencia. Importes diferentes, tachados o condiciones ambiguas siguen pendientes.
Registrar precio publicado, anterior, referencia kg/litro/unidad y descuento como
roles separados; referencias y porcentajes no generan otras fichas de producto.

Normalizar moneda usando contexto documentado, conservando `$` en la evidencia;
no mapear dólares u otras monedas a ARS. Confirmar base sobre evidencia del folleto,
incluidas condiciones y equivalencias de referencia comprobables cuando corresponda.
No convertir compra mínima ni contenido interno en cantidad comercial de pack.

La identidad puede confirmarse con precio pendiente. Un importe observado puede
conservarse aunque la base esté pendiente. Ahorro/margen/compra se habilitan sólo
con identidad, base, cantidad cuando aplica y moneda comparables verificadas.

### 5. Hacer útil y compacto el método B

Preparar spans candidatos a partir de todo el bloque, incluidas líneas posteriores
al precio, marcas y presentaciones. Los spans deben conservar referencias exactas;
no reconstruir nombres que el lector no transcribió. Incluir `cc` como volumen.

Distinguir números observados de candidatos a precio. No hacer nueve preguntas
completas sobre cada tamaño o mínimo. Dar a JEV las decisiones necesarias, con
opciones útiles y `ninguno/evidencia insuficiente/desconocido` según la función.

Evaluar contexto común compacto y opciones que referencien IDs existentes, en lugar
de repetir prompts y descripciones completas. Medir tamaño antes de enviar, lotes
acotados y versiones. Menos bytes no acredita mismas decisiones: probar calidad.
El experimento offline B mantuvo las mismas preguntas y redujo la suma de bytes de
17.497.033 a 3.792.665 (78,3 %); el máximo pasó de 343.656 a 65.252, por debajo
del límite local de 100.000. Es una alternativa sin implementar: no mide tokens,
precio, latencia ni calidad del proveedor, y no corrige las opciones de nombre.
Reutilizar las respuestas ya útiles de una ejecución cuando no cambian preguntas,
evidencia ni versiones; no reutilizarlas para una lista de opciones diferente.

Mantener la aceptación automática de propuestas probabilísticas desactivada hasta
evaluar precisión/calibración. Corregir primero candidatos y reglas deterministas;
no resolver esta corrida activando esa aceptación global.

### 6. Mostrar qué quedó pendiente y comprobar ambas opciones

Mostrar páginas con resultado/total, páginas no intentadas, fallos de lectura,
identidad confirmada/sugerida y base de precio pendiente. Los seis bloques pendientes
de A no deben interpretarse como seis únicas dudas: hay 49 identidades y bases
pendientes y 20 páginas sin resultado. Un candidato sugerido no es un vínculo confirmado.

Los logs deben usar el ledger actual, sin `provider_calls=unknown`, y separar llamadas
reales de rechazos locales. Informar usage conocido y llamadas con usage desconocido
sin convertirlo en cero ni mostrarlo como factura. Mantener el historial original;
las correcciones futuras son otra versión y no reescriben silenciosamente la corrida.

## Ownership propuesto para ejecutar después de este plan

| Responsable | Área exclusiva |
|---|---|
| Agente A | Lector/prompt OpenAI estructurado, ensamblado de propuestas A y sus pruebas; coordinar cambios del adaptador compartido con el coordinador |
| Agente B | Detector/ensamblador B, cliente/adaptador JEV, prompts B y sus pruebas |
| Coordinador | Contratos comunes, presupuesto, catálogo/identidad/política/matching, API/UI, integración y pruebas de aceptación cruzadas |

`document_analysis.py` contiene ensamblado A y presupuesto común: los cambios de
esa clase se integran secuencialmente por el coordinador, sin dos autores en paralelo.
No modificar ni migrar base del negocio para las pruebas. Los servicios deterministas
validan; los proveedores interpretan y nunca escriben por sí mismos en la DB.

## Condiciones para considerar corregida la funcionalidad

- Todas las páginas reciben resultado o un pendiente explícito y reanudable;
  rechazos locales JEV no impiden iniciar las lecturas presupuestadas.
- Nombre NATURA aparece entre opciones B y el texto posterior al precio se conserva.
- Impuestos opcionales inválidos no eliminan el artículo y el importe válidos.
- AYUDIN original con identidad canónica única se prioriza frente a lavanda;
  tamaños/variantes/duplicados ambiguos y feedback rechazado siguen vetados.
- PRECIO XLT de KEVIN no genera otra oferta del envase; mínimos y contenidos
  no se usan como cantidades de pack.
- Identidad y base se verifican separadas también al recomparar y corregir.
- A/B, parciales, historia y tenant se prueban con componentes reales y transportes
  simulados; se añade un corpus humano antes de medir autoaceptación y ahorro.
- Comparación real A/B usa el mismo conjunto de páginas/documentos y catálogo
  controlado, con cobertura, precisión y costo de todos los intentos, incluidos fallos.

## Fuentes y entregas

### Revisión integrada realizada

El coordinador revisó los dos informes y contrastó los puntos centrales con el
código: descarte global de referencias A, regex de unidad/importe, gate de identidad
exacta condicionado por ranking y decisiones posteriores al cierre de lectura.
También verificó los resultados offline archivados: A conserva 55 interpretaciones;
B reconstruye 95 auditorías con preguntas idénticas y 82 rechazos de tamaño.
El replay B prepara cinco rutas JEV entre 16 propuestas: falta de referencias de
base no explica por sí sola la ausencia de todas las decisiones; el presupuesto
agotado sigue siendo una causa comprobada.

El investigador B ejecutó 143 pruebas existentes que pasaron. El investigador A
reprodujo 31 rutas JEV y 24 humanas sin llamadas externas. Esas verificaciones
explican el resultado actual; las correcciones propuestas todavía no están
implementadas ni evaluadas, y no se certificó visualmente el PDF.

Informes de los dos investigadores, revisados por el coordinador al cierre:

- [Caso A](agents/PLAN_CORRECCION_CASO_A_OPENAI_VITAL_2026_10_07.md).
- [Caso B](agents/PLAN_CORRECCION_CASO_B_JEV_VITAL_2026_10_07.md).
- [Diagnóstico inicial B](DIAGNOSTICO_CORRIDA_JEV_2026_10_07.md).

Structured Outputs limita el formato, pero puede contener errores de interpretación;
mantener validación semántica y pruebas con casos reales.
[Documentación oficial](https://developers.openai.com/api/docs/guides/structured-outputs).
El SDK admite timeouts y control de retries; esa configuración no reemplaza los
límites de etapas y seguimiento de tareas propios de la aplicación.
[SDK oficial](https://github.com/openai/openai-python#timeouts).

Este plan no fija porcentajes de ahorro ni cambia modelos, configuración o aceptación.
