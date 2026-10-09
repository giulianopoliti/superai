# Etapa 0: instrumentación y evaluación reservada

Fecha: 5 de octubre de 2026. Estado: herramienta local lista; **línea base real abierta**.
Ownership: únicamente este documento, `scripts/benchmark_procurement.py` y
`tests/scripts/test_benchmark_procurement.py`. Sin cambios a proveedores, contratos,
piloto, DB ni plan compartido. Sin instalaciones, llamadas pagas ni subagentes.

## Evidencia disponible e inventario

Inspección de código y archivos locales; no se consultó la DB ni se ejecutó una importación.

| Fuente existente | Métrica recuperable | Límite |
|---|---|---|
| `app/main.py`, `catalog_analysis` | Duración de extracción, packs, persistencia, matching y total por `request_id`; errores capturados y tiempo al fallo | Etapas son intervalos; `completed` es total. No sumar ambos. Errores fuera del bloque de procesamiento pueden carecer de cierre |
| `app/modules/procurement/matching.py`, `catalog_match` | Carga de catálogo, ofertas, feedback y matching; `total_duration_ms` | Mismo `request_id`, familia distinta; `duration_ms` de completed es solo el último intervalo |
| `app/providers/documents/dual.py`, `document_extraction` | Duración total, llamadas iniciales y relecturas, fallos y detalle por proveedor/páginas/fase | ID propio; enlace explícito desde `catalog_analysis.document_extracted`. Llamadas son intentos del wrapper, no facturación |
| `extraction.document_metadata` | `duration_seconds`, llamadas iniciales/retry, `provider_errors`, llamadas de packs, pendientes | Solo si se exporta este objeto; la respuesta del endpoint no incluye toda esta metadata |
| `app/providers/documents/pack_review.py`, `pack_review` | Duración total, ofertas objetivo y pendientes | No registra llamadas en log final ni enlace a request/extraction. Cada `item_completed` es una oferta; hasta ocho por llamada. No contar filas como llamadas |
| `scripts/run_jev_pilot.py`, shadow | Tokens input/output y errores por decisión cuando existen | No hay duración ni identificación reproducible de ejecución/modelo/prompt/catálogo completa; una decisión fallida no prueba cuántas llamadas HTTP se hicieron |
| Piloto baseline/dual histórico | Métricas de calidad si hay etiquetas suministradas | No contiene telemetría suficiente para línea base actual; el baseline recupera ofertas legacy de DB |

Los lectores OpenAI y el revisor visual devuelven el resultado parseado sin conservar
`response.usage`. JEV sí conserva tokens cuando su respuesta los incluye. La API
Responses documenta `usage`, incluidos input/output y detalles de caché; se propone
capturarlos después en los proveedores, sin implementarlo fuera de este ownership.
Referencia oficial: [objeto de respuesta OpenAI](https://developers.openai.com/api/reference/resources/responses/methods/retrieve).

El inventario local no encontró `.log` ni `.jsonl` de procurement. El único JSON
en `output/`, `diarco_ocr_inspection.json`, tiene **0 bytes**. Existen PDFs e imágenes
en `tmp/` y archivos de pruebas, pero su existencia no acredita un conjunto humano
etiquetado ni una ejecución OpenAI vigente. No se localizaron exports del piloto
ni documentos con etiquetas humanas verificables. Esto no afirma que no existan
en otro almacenamiento: faltan su entrega e identificación explícita.

El registro histórico dual de aproximadamente 28 minutos se excluye de la referencia
vigente. No se obtuvieron tiempos reales nuevos, precisión, ahorro, costo ni tarifas.

## Utilidad reproducible, sin dependencias nuevas

Desde la raíz, usando Python instalado o `.venv/Scripts/python.exe`:

```powershell
python scripts/benchmark_procurement.py captura-openai.log --cohort openai_current --provenance real
python scripts/benchmark_procurement.py export-extraction.json export-shadow.json --cohort unclassified --provenance real
python -m unittest discover -s tests/scripts -p test_benchmark_procurement.py -v
```

Los nombres de entrada son ejemplos; esos captures no se han creado. El resultado
JSON se imprime a stdout; el analizador solo lee archivos explícitos. No importa
`app`, no carga `.env`, no inicia proveedores y no lee ni escribe en DB.
Incluye hash SHA-256 de cada fuente, grupos por fuente/familia/ID, etapas,
duraciones, llamadas, fallos, tokens y costos. Cohorte y procedencia son declaraciones
del operador, **no verificaciones automáticas**. No mezclar captures de distintos modos.

Formatos aceptados:

- Logs de texto con las cuatro familias observadas y pares `key=value`.
- JSON de extracción con `document_metadata`, o con `extraction.document_metadata`.
- JSON de shadow existente con `decisions`; no se interpretan sus decisiones como etiquetas.
- JSONL de eventos explícitos, o JSON `{"schema_version":"procurement-benchmark-v1","events":[...]}`.

Cada evento explícito lleva `family`, `stage`, ID de familia o `run_id`, y un
`event_id` único para las filas de llamada. Se sugieren `stage=call` para mediciones
por llamada y `stage=completed` para totales. Tokens solo se suman en filas `call`
o `decision`: así no se duplican con resúmenes. `initial_calls` + `retry_calls` o
`provider_calls` se leen del resumen, nunca se suman con starts. Si solo existen
starts se reporta `provider_starts_observed` como conteo parcial; no se inventa el total.
Los tiempos concurrentes y las filas por oferta no se suman como tiempo de pared.

Los IDs `none`/`unknown` se rechazan. Fuentes distintas no se fusionan, incluso
cuando tienen el mismo ID. Las fuentes repetidas se omiten; eventos con `event_id`
repetido se rechazan y generan diagnóstico. Logs sin event_id no permiten demostrar
deduplicación: usar una captura canónica sin duplicados, incluyendo logs correlacionados
en un único archivo. No cargar artifact y log como dos mediciones del mismo documento.
`extraction_links` señala la correlación explícita; packs quedan separados. Si se
reciclan IDs entre procesos, separar captures por proceso. Totales contradictorios
o números negativos/no finitos producen null y advertencia. Datos ausentes quedan null;
un contador de filas observado igual a cero no prueba ausencia de errores en producción.

### Usage y costo

`known_sum` es subtotal de tokens realmente presentes, junto con número de filas y
`complete`. No estima tokens por páginas, caracteres o nombre del modelo.

Los eventos pueden contener `observed_cost` y/o `estimated_cost`, cada uno con
`amount` no negativo, `currency` y `source`. Observado requiere referencia a
facturación/medición atribuida a esa llamada. Estimado requiere referencia a cálculo
y snapshot fechado de tarifas, modelo y componentes aplicados (incluida caché).
La herramienta conserva categorías separadas y subtotales por moneda; no aplica
tarifas predeterminadas, convierte monedas ni transforma estimaciones en facturación.
Sin esos campos, los subtotales están vacíos y `complete=false`: costo no disponible.
La procedencia debe ser auditada por el evaluador; un campo `source` por sí solo no
certifica la veracidad. Tests usan importes sintéticos, no tarifas comerciales.

## Casos a seleccionar y etiquetar por humanos

| Caso | Evidencia y error a evaluar |
|---|---|
| Folleto con fotos y columnas | Precio ligado al bloque correcto, ofertas omitidas |
| Lista textual | Código, marca, variante y moneda |
| Pack real de seis | Base pack y cantidad comercial explícita; división válida |
| Toallitas x48 / contenido del envase | Contenido minorista separado de cantidad comercial |
| Compra mínima de seis | Mínimo separado de base/precio; evitar dividir |
| Cantidad ausente / leyendas contradictorias | Mantener precio original y pendiente |
| Marcas/variantes/presentaciones similares | Identidad correcta y vetos deterministas |
| Página poco legible / precio ausente | Relectura acotada y pendiente si no resuelve |
| Descuento sin precio absoluto | No fabricar importe |
| Ofertas repetidas | Identidad de bloque estable, sin deduplicación por nombre |
| Producto fuera del catálogo/candidatos | `none`/`insufficient`, recall de recuperación |
| Feedback humano anterior / otros negocios | Prioridad de feedback compatible y aislamiento |
| Timeout/esquema inválido de JEV | Pendientes explícitos; resultados claros preservados |
| Identidad resuelta y precio pendiente, e inversa | Evaluar ambos ejes independientemente |

Las últimas fallas de proveedor pueden cubrirse con fixtures sintéticos offline.
Los fixtures del test de esta entrega son **telemetría sintética**, sin ofertas
humanas etiquetadas; no constituyen una evaluación de calidad.

## Esquema propuesto para evaluación reservada

Preparar un manifiesto versionado con `dataset_version`, `business_id`, `supplier_id`,
`document_sha256`, `document_group_id`, `format_group`, `split` (`development`/`blind`),
procedencia `human_labeled`, fecha, identificador del anotador y adjudicación de dudas.
Un grupo reúne todas las páginas y versiones cercanas del mismo folleto. Asignar
grupos completos a un solo split; reservar además proveedores o formatos. Fijar
el split y sus hashes antes de ajustar prompts/política; no publicar respuestas
reservadas a quienes ajustan. Documentar conteos por documento, proveedor y caso.

Cada oferta etiquetada necesita `offer_block_id`, página y coordenadas o cita que
la distingan, `product_id` o estado verificado de desconocido, precio publicado y
moneda, `price_basis` (unit/pack/unknown), cantidad comercial, compra mínima,
contenido minorista, evidencia textual/visual y legibilidad. Campos desconocidos
son explícitos; incluir **todos** los bloques válidos del documento, también los
omitidos por el modelo. No convertir predicción/confianza en verdad humana.

Fijar catálogo y feedback por negocio, versiones/hash de código, prompt, schema,
política y modelo. Dos protocolos independientes:

1. Extracciones congeladas e idénticas: compara backend/matching con y sin JEV,
   sin volver a ejecutar visión. Registrar hash de extracción y candidatos;
   separar errores de recuperación de errores de elección.
2. Flujo completo sobre los mismos documentos: OpenAI vigente frente a selectivo,
   con repeticiones planificadas y orden alternado. Registrar fallos y parciales,
   además de ejecuciones exitosas. No mezclar dual histórico con OpenAI vigente.

Informe futuro: recall de ofertas (incluidas omisiones), precisión de identidad
aceptada, base de precio y cantidad, errores críticos de división, precio publicado
correcto, recall de candidatos, duplicados, pendientes y tiempo humano. Latencia
por etapa/total, llamadas iniciales/relecturas/packs/JEV, tokens/costo por documento.
Reportar denominadores, cobertura de telemetría, muestra, mediana y p95; comparar
pares compatibles por documento, sin contar páginas correlacionadas como muestras
independientes. Cero errores en una muestra no garantiza cero errores en producción.
No se calcula ahorro/accuracy con captures no etiquetados o costos incompletos.

## Dependencias para cerrar la línea base real

1. Documentos reales identificados por hash y etiquetas humanas completas/adjudicadas;
   manifiesto de grupos/splits reservado y catálogo/feedback congelado por negocio.
2. Capturas canónicas del flujo **OpenAI actual** sobre esos documentos: éxito,
   fallos, parciales, ID de ejecución, hash documental y versión de configuración.
3. Propuesta a ownership de proveedores/orquestación: capturar usage de cada respuesta,
   modelo concreto, ID único de llamada, fase/páginas, duración y error; correlacionar
   request/extraction/review y registrar llamadas reales de packs. Conservar fallos
   fuera del bloque actual y tiempos hasta pendiente/utilizable, separados del total HTTP.
4. Export local de metadata/telemetría que evite persistencia y resultados repetidos;
   distinguir llamadas facturables de intentos fallidos con usage desconocido.
5. Tarifas documentadas por fecha/modelo/componente para costo estimado; registros
   de facturación atribuibles para observado. No asumir que los tokens JEV/OpenAI
   tienen igual precio o que una llamada fallida no se factura.
6. Coordinación antes de extender piloto/contratos: `LabeledOffer` actual carece de
   base, cantidad comercial, mínimo, contenido y evidencia estable por bloque.
   `measure` no basta para evaluar esos ejes ni duración/costo de extremo a extremo.

La herramienta permite inspeccionar lo existente y consumir futuros eventos locales.
La línea base y la comparación selectiva requieren esas dependencias; esta entrega
no afirma resultados reales ni habilita las etapas siguientes automáticamente.

## Verificación de la entrega original (5 de octubre)

14 pruebas con fixtures sintéticos: separación de requests/familias, total frente a
intervalos, packs agrupados, errores y captura parcial, campos ausentes/inválidos,
totales contradictorios, metadata de extracción, usage parcial de shadow, categorías
de costo/monedas/procedencia, deduplicación, fuentes independientes, JSON vacío,
registros malformados y CLI ejecutada con `python -S` (sin paquetes de terceros).
Comandos de verificación: unittest anterior, pytest sobre este único archivo y
ruff check/format sobre los dos archivos Python. No se ejecutó la suite global.
Se probó además la CLI con el JSON local vacío: cero ejecuciones y diagnóstico
explícito de artefacto ilegible, sin inventar métricas. No se generó reporte persistido.

## Ampliación A/B (6 de octubre de 2026)

La utilidad conserva compatibilidad con capturas legacy y agrega el envelope de
telemetría local `procurement-benchmark-v2` y el adaptador del contrato compartido
`AnalysisLedger`/`AnalysisSummary` publicado en `analysis_contracts.py`. No crea
un esquema alternativo de lectura ni exige modificar runtime para analizar exports.
Acepta ledger directo, `analysis_ledger`/`analysis` en raíz o metadata de documento,
incluida `import_result.document.metadata` de la respuesta API.

Grupos por fuente, familia, `run_id`, `analysis_method` y negocio; conserva reading,
modelo y versiones de prompt/schema/política/catálogo/feedback cuando existen.
Las llamadas repetidas se deduplican por `call_id`; si dos copias discrepan se
excluyen sus métricas, se advierte y la cobertura queda incompleta. Las llamadas
iniciales, de relectura, clasificación y decisión participan una sola vez, incluyendo
fallos con usage desconocido. El resumen agregado no se suma con sus llamadas.
Cached input y reasoning output son subtotales, no tokens adicionales.

Contabiliza bloques vigentes, históricos y pendientes por ID; nunca por nombre.
En exports runtime, `supersedes_block_id` es una asociación declarada por backend:
se comprueba existencia, run/página, lectura diferente, resultado derivado y ausencia
de motivos pendientes antes de retirar la versión anterior. El benchmark no puede
certificar visualmente esa asociación. En eventos locales, se exige además
`association_validated=true` y mismo `lineage_id`; ciclos, refs ajenas y cadenas
incompletas permanecen vigentes con advertencia. Varias fichas del mismo bloque
se cuentan por ID de oferta; múltiples candidatos no amplían denominadores.

`offer_states` separa estados de identidad y precio de interpretaciones vigentes;
son estados del software, no medidas de calidad. `evaluation` solo calcula comparación
exacta de `expected.identity`/`expected.price` con `predicted` cuando hay etiqueta
explícita y `label_provenance=synthetic|human_labeled`. No convierte una predicción
en etiqueta, no mezcla accuracy humana/sintética y no infiere recall de ofertas
omitidas a partir del ledger. Precio debe representar el eje acordado (importe,
base, cantidad y/o unidad) con el mismo esquema en esperado/predicho. Recall real
requiere todos los bloques humanos anotados y asociaciones adjudicadas.

`AnalysisCost` se proyecta según su bandera `estimated`, usando `tariff_id` como
referencia declarada. Una bandera false no prueba facturación observada: auditar
la atribución de billing antes de publicar ese subtotal como costo real. No se
asumen tarifas ni se calcula ahorro con registros desconocidos o incompletos.

Ejemplo **sintético**, sin llamados a proveedores:

```powershell
python -S scripts/benchmark_procurement.py tests/fixtures/procurement_analysis/telemetry.json --provenance synthetic
```

El corpus de 24 escenarios y los resultados/pruebas/hallazgos actuales están en
[QA_IMPLEMENTACION_DOS_METODOS.md](agents/QA_IMPLEMENTACION_DOS_METODOS.md).
Estas ampliaciones no cierran la línea base humana ni habilitan métodos por sí solas.
