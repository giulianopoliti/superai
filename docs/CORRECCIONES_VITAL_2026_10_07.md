# Correcciones Vital — primera entrega integrada

7 de octubre de 2026. Implementación local revisada; no es el cierre del plan completo.

Se reutilizaron los dos investigadores para implementar A y B con archivos exclusivos.
Ambos alcanzaron el límite de uso antes de entregar su cierre. El coordinador revisó
el código escrito, completó regresiones del método B y corrigió dos incompatibilidades
detectadas por la suite completa. No se reinició el servidor del usuario ni se ejecutó
inferencia, reprocesamiento o escritura en la base real.

## Implementado

- A conserva campos respaldados cuando falla una cita opcional. Las referencias
  inválidas de campos obligatorios siguen sin publicar esos valores. El texto y las
  propuestas originales quedan en el ledger.
- Citas monetarias repetidas se distinguen por ocurrencia completa/contexto cuando
  hay una sola candidata publicada. Repeticiones sin contexto siguen pendientes.
  `$8.499 L'OREAL` ya no se interpreta como una expresión de litros.
- Coincidencia canónica única antes del ranking fuzzy: unidades equivalentes y
  orden de palabras; alias acotados de mayonesa/sachet y Colgate/crema dental.
  Hay vetos de variantes, grados 000/0000, presentación, tenant, inactivos y feedback.
  Duplicados canónicos no se aceptan automáticamente. Se conserva la convención
  legacy de identidad exacta XN, sin usarla para inventar cantidades comerciales V2.
- B prepara nombres de líneas posteriores al precio, reconoce cc y separa mínimos,
  tamaños y conteos. XLT/XKG/XU son referencias; una selección errónea como precio
  actual no crea otra ficha del envase. Marca sólo mediante opciones literales.
- JEV recibe reglas comunes una vez en el estado y opciones por IDs. Las preguntas
  irrelevantes se omiten y las restantes se particionan por límites sin truncar texto.
  Cada POST real reserva cupo; preflight local no lo consume. Se conservan respuestas
  parciales válidas y razones específicas de respuestas inválidas.
- Lectura y semántica avanzan en pools separados: hasta dos lectores OpenAI y un
  transporte JEV. Libro de evidencia, política y catálogo se modifican en el hilo
  principal. Se espera el cierre de tareas reales antes de eliminar PDFs temporales.
  El lector aplica el mínimo entre timeout configurado y tiempo global restante.
- Hay cuotas de llamadas/tiempo por fase, decisiones incrementales y auditoría de
  candidatos, validaciones, llamadas y usage conocido/desconocido. A lee una página
  por request por defecto; B conserva dos. Esto no acredita menor latencia/costo.
- La interfaz separa producto identificado de base de precio pendiente, y muestra
  páginas con resultado, nunca enviadas e intentadas sin resultado. GET de historia
  proyecta cobertura desde el ledger sin modificar registros guardados.

## Verificación

Suite completa Python: **599 passed**. Interfaz: **11 passed**. Ruff de los archivos
modificados: **All checks passed**. Tras limpieza de formato/imports se repitieron
212 pruebas afectadas, todas aprobadas. Queda una advertencia existente de
deprecación Starlette/httpx; no se instalaron dependencias para corregirla.

Regresiones incluyen NATURA multilínea, HEINEKEN 710cc con condición de pack,
KIMBIES mínimo/contenido, KEVIN XLT, citas opcionales/obligatorias, ocurrencias
repetidas, particiones/preflight, cuotas, lectores concurrentes, JEV lento y
PDF temporal retenido hasta finalizar el transporte.

Replay offline de A sobre los bloques guardados y el snapshot de catálogo posterior:
56 propuestas conservadas, 52 importes respaldados y **cinco identidades confirmadas**:
NATURA aceite 900ml, NATURA mayonesa 125g, FAVORITA harina 000 1kg, AYUDIN original
2L y COLGATE Ultra blanco 90g. Las bases de precio de las 56 propuestas siguen
pendientes por falta de respaldo suficiente. Esto prueba recuperación sobre texto
ya extraído, no fidelidad visual, cobertura de páginas faltantes ni nuevos resultados
de OpenAI/JEV. No modifica la corrida histórica. Evidencia en
`output/diagnostics/integrated_vital_replay.json` (scratch local, no fixture publicada).

## Pendiente del plan completo

- Continuación funcional de análisis parcial: se registra `recovery_pending` con
  `continuation_available=false`; todavía no hay endpoint/botón para retomarlo.
  Hace falta definir versionado/persistencia, archivo y binding, idempotencia y revisión
  humana antes de reutilizar resultados sin modificar silenciosamente la historia.
- Lotes de clasificación que agrupen varios bloques: la partición actual divide
  preguntas de cada bloque, no agrupa bloques en un mismo POST. Por eso la cuota de
  clasificación puede dejar bloques pendientes aunque todos los cuerpos entren.
- Calibración y aceptación automática de propuestas probabilísticas de JEV. Sigue
  deshabilitada; no se bajaron umbrales ni se inventaron cantidades/base/moneda.
- Interpretación adicional del precio por envase en folletos con condiciones de compra
  y referencias XLT/XKG, con corpus adjudicado. Identificar el producto no habilita por
  sí solo ahorro, margen o recomendación de compra.
- Medición A/B real con igual cobertura y catálogo controlado. No se garantiza leer
  24 páginas dentro de 180 s ni se informa un porcentaje de ahorro a partir de bytes.

Para utilizar esta entrega hay que reiniciar el backend iniciado sin `--reload` y
recargar la interfaz. Los resultados comerciales históricos conservan su estado;
estas correcciones se aplican al analizar de nuevo, y la cobertura histórica sólo
se amplía como presentación de los datos ya registrados.

## Archivos principales

Orquestación/lector: `document_analysis.py`, `providers/documents/openai.py`, prompt
estructurado y pruebas de recuperación. B: `block_interpretation.py`,
`providers/jev_analysis.py`, prompt de clasificación y pruebas. Compartidos:
`analysis_contracts.py`, `analysis_reporting.py`, `identity.py`, `matching.py`,
`decision_policy.py`, `main.py`, `static/app.js`, `static/index.html` y sus regresiones.

El [plan integrado](PLAN_CORRECCION_CORRIDAS_VITAL_2026_10_07.md) sigue siendo la
referencia de las etapas restantes. Gemini continúa suspendido.
