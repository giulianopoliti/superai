# Plan técnico: extracción dual de catálogos y matching opcional con Jev

**Fecha:** 20 de septiembre de 2026
**Estado:** propuesta para evaluación; este documento no implementa cambios.

## Objetivo y decisión de arquitectura

Procesar todas las ofertas de un PDF de proveedor, vincular cada oferta con el producto correcto del catálogo interno y reducir las revisiones manuales sin alterar el recorrido que ya funciona. La comparación económica debe ejecutarse únicamente después de identificar el producto y normalizar la presentación del precio.

La hipótesis a probar es:

```text
PDF → lectura visual independiente A/B → conciliación y validación
    → candidatos fuzzy/feedback → Jev para desambiguar candidatos
    → reglas de aceptación o revisión → comparación determinística de precios
```

Jev recibe texto o JSON, no el PDF ni sus imágenes. Por eso su lugar natural es **después** de la extracción visual y **después** de recuperar candidatos; su decisión sería elegir un producto entre opciones cerradas, o indicar que ninguno encaja. No debe extraer importes, calcular precios unitarios ni decidir compras. [Tipo de entrada de Jev](https://docs.typesafe.ai/concepts/state), [primitivas y preguntas](https://docs.typesafe.ai/primitives).

La primera prueba debe medir por separado si A/B mejora la **cobertura de ofertas** y si Jev mejora la **precisión del matching**. Si la extracción sigue omitiendo artículos, Jev no puede recuperarlos.

## Punto de partida observado en el repositorio

- `POST /procurement/supplier-offers/from-document` usa `gemini` por defecto, recibe `extraction_provider`, importa los ítems y luego llama a `ProductMatchService.compare_supplier_offer`. `max_candidates` vale `1` por defecto y los candidatos se persisten salvo que se indique lo contrario (`app/main.py`).
- El proveedor Gemini puede enviar el PDF entero o leer por página cuando detecta un PDF de imágenes; si obtiene ambos resultados, hoy favorece el documento completo cuando devuelve al menos tantos ítems como la lectura por página (`app/providers/documents/gemini.py`). **Más ítems no demuestra que los precios sean correctos**. El caso de Diarco documentado en `docs/DIARCO_PDF_EXTRACTION_PLAN.md` mostró una extracción incompleta concentrada en las primeras páginas.
- Existe un proveedor OpenAI que envía el archivo completo y devuelve `SupplierOfferExtraction`; la configuración actual apunta a `gpt-5.6-luna` (`app/providers/documents/openai.py`, `app/settings.py`). El soporte de PDF de la API puede incluir texto y vistas de página, pero por sí solo no garantiza que el modelo enumere todos los renglones. [Entradas PDF de OpenAI](https://developers.openai.com/api/docs/guides/file-inputs), [Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna).
- El matching actual primero reutiliza feedback humano y, si no lo hay, puntúa los productos con similitud de nombre, tokens, marca y unidad. Su `confidence_score` es un **puntaje heurístico**, no una probabilidad calibrada. Además, genera relación comercial y cálculo de precios dentro del mismo servicio (`app/modules/procurement/matching.py`).
- `replace_product_match_candidates` borra los candidatos previos del documento y crea otros nuevos (`app/db/repositories/sql_procurement.py`). Antes de permitir una decisión automática hay que preservar decisiones y referencias ya revisadas al volver a comparar.
- Los prompts de extracción indican `price_type = "package"`, mientras el contrato usa `pack`. Corregir esa divergencia antes de medir resultados de precios (`app/assistant/prompts/supplier_offer_extractor.md`, `app/assistant/prompts/supplier_offer_page_extractor.md`, `app/modules/procurement/pricing.py`).
- Aceptar un match deja feedback, pero no incorpora automáticamente la oferta a `supplier_products`. La comparación entre proveedores lee esta última fuente. Este puente es un alcance separado y debe explicitarse.

El plan previo `docs/PLAN_MATCHING_PROVEEDORES_SIN_JEV.md` define la base de feedback y normalización sin Jev. Este experimento se agrega como ruta opcional sobre esa base; no la sustituye.

## Contratos propuestos

Mantener `DocumentExtractionProvider` como límite de proveedor y agregar un **orquestador de lecturas** que produzca resultados independientes A y B. En la prueba inicial, A es Gemini y B es Luna. Ambos reciben las mismas páginas o regiones originales, un esquema estructurado y prompts versionados, **sin mostrar a B la respuesta de A**. Así se puede medir acuerdo real. Si conviene usar B solo para revisar regiones omitidas, medirlo como un modo distinto, no como una segunda lectura independiente.

Cada observación de oferta necesita, como mínimo: `business_id`, documento y huella del archivo, página, región o recorte de origen, proveedor/modelo/versión/prompt, nombre literal, marca/presentación, precio literal y parseado, moneda, impuestos, `price_type`, cantidad por pack, advertencias y estado de validación. Los valores inferidos deben distinguirse de los visibles en la fuente. El contrato existente puede seguir alimentando el flujo actual; la evidencia adicional debe guardarse en un registro de ejecución separado o en metadatos versionados, según el diseño de persistencia que se elija en esa fase.

La conciliación agrupa observaciones de A y B por página y posición, y después por contenido. Debe unir duplicados auténticos sin colapsar dos renglones repetidos de la misma página. Si un modelo ve una oferta y el otro no, se conserva para revisión de cobertura. Si difieren en importe, moneda, unidad o tamaño, queda `conflict` y exige cotejo con el PDF; ni “mayoría” de modelos ni Jev pueden resolver una discrepancia visual que no han verificado. Nunca elegir el resultado de una lectura solo por su cantidad de ítems.

## Recuperación de candidatos y decisión

1. Consultar primero las decisiones humanas previas para el mismo negocio, proveedor y artículo normalizado. Conservar su prioridad y exclusiones de rechazos; una decisión automática no debe escribirse como si fuera feedback humano.
2. Validar reglas duras antes del fuzzy: identidad de tenant, código de barras cuando sea legible y confiable, marca, tamaño, unidad y cantidad por pack. Separar incompatibilidades reales de datos faltantes. Un nombre parecido con distinto contenido no es el mismo producto.
3. Recuperar un conjunto pequeño de productos plausibles, inicialmente `top K = 5` como parámetro experimental, más las opciones `ninguno` y `evidencia insuficiente`. El objetivo de este paso es **recall**, no aceptar el primer puesto. Ajustar K y el corte de score con el conjunto etiquetado; el `max_candidates=1` actual no sirve para medir la elección de Jev.
4. Enviar a Jev el ítem estructurado y las opciones identificadas por ID estable. Usar una pregunta `Choice` con criterios claros de equivalencia. Jev escoge entre candidatos recuperados; no busca en toda la base. Si se necesita clasificar la relación comercial del elegido, hacerlo en una consulta posterior o con preguntas independientes por candidato: las preguntas de un mismo request se evalúan independientemente y no deben suponer que una ya respondió otra. [Estado y preguntas de Jev](https://docs.typesafe.ai/concepts/state).
5. Aplicar reglas determinísticas a la respuesta. Registrar modelo versionado, opciones presentadas, probabilidades, confianza, margen frente al segundo candidato y motivos de veto. La `confidence` de Jev resume su distribución de respuesta; **no equivale** al score fuzzy ni garantiza exactitud. Calibrar el umbral con datos propios y fijar la versión del modelo durante la evaluación. [Confianza](https://docs.typesafe.ai/confidence), [versiones de Jev](https://docs.typesafe.ai/models).

Estados de decisión recomendados: `confirmed_by_human`, `auto_matched`, `review_required`, `unmatched` y `evaluation_error`. Mantener separado el estado de matching de la recomendación de compra. La aceptación automática vincula una oferta a un producto para análisis; no aprueba una compra ni transforma por sí sola el costo del sistema. Si el sistema actual solo admite `pending/accepted/rejected`, añadir una representación persistente de procedencia antes de activar autoaceptación; no reutilizar `accepted` sin distinguir su origen.

## Modos y fallos

La selección de ruta debe ser explícita por ejecución, con configuración y versión registradas:

| Modo | Extracción | Matching | Efecto |
| --- | --- | --- | --- |
| `legacy` | Proveedor actual | Feedback + fuzzy actual | Comportamiento predeterminado existente. |
| `dual_shadow` | A y B independientes | Matching actual | Mide cobertura y conflictos; no cambia decisiones visibles. |
| `jev_shadow` | Extracción validada | Fuzzy + Jev | Guarda evaluación comparativa; no acepta ni rechaza candidatos. |
| `jev_assisted` | Extracción validada | Fuzzy + Jev | Muestra propuesta de Jev para revisión humana. |
| `jev_auto` | Extracción validada | Fuzzy + Jev + compuertas | Auto-vincula solo casos que superen criterios calibrados. |

Un error, timeout, límite de tasa o respuesta inválida de Jev debe terminar en `review_required` o en la ruta legacy según el modo; jamás en autoaceptación. La extracción fallida debe conservar evidencia y permitir reintento idempotente. El modo shadow no debe llamar al reemplazo destructivo de candidatos persistidos. Al recalcular un documento, preservar IDs y decisiones aprobadas y registrar una nueva versión de evaluación; comprobar integridad de referencias de feedback.

## Comparación de precios y enlace entre proveedores

Después del matching, un servicio determinístico normaliza precio por unidad o pack, moneda, impuestos y vigencia. Si `price_type`, cantidad, divisa o impuestos siguen ambiguos, mostrar el precio citado pero **no** concluir que una oferta es más barata. La comparación contra el costo actual y la comparación entre proveedores son recorridos distintos.

El posible enlace a `supplier_products` debe ser una fase posterior: publicar una oferta validada con `supplier_id`, `product_id`, precio original, precio comparable, presentación, fecha/validez, documento y página de origen. Definir política de vigencia, reemplazo y deduplicación por proveedor/producto/documento. Al principio, requerir confirmación humana para publicarla; evaluar publicación automática por separado. Así no se introduce un precio dudoso en la tabla que usa la comparación entre proveedores.

## Secuencia de trabajo y criterios de salida

| Fase | Trabajo | Evidencia necesaria para avanzar |
| --- | --- | --- |
| 0. Línea base | Seleccionar catálogos distintos (incluido Diarco), etiquetar todos los renglones y precios de páginas de prueba, su producto correcto o `ninguno`, pack/impuestos y discrepancias. Congelar un conjunto de desarrollo y otro ciego. Medir el flujo actual. | Conteo manual por página/región y métricas de omisión, error de importe, recall@K, match equivocado, revisiones, latencia y costo. |
| 1. Cobertura visual | Corregir `package`/`pack`; probar A y B por página y, si hay páginas densas, por recortes solapados. Conciliar sin elegir por cantidad. Registrar uso de tokens e imágenes. | Más ofertas correctas recuperadas que la línea base, sin aumento inaceptable de importes inventados o duplicados; discrepancias localizables en el PDF. |
| 2. Candidatos | Separar recuperación fuzzy de decisión y cálculo comercial. Exponer top K, reglas de exclusión, feedback humano y opción `ninguno`. | Recall@K medido en el conjunto ciego; confirmar que el producto real aparece antes de evaluar a Jev. |
| 3. Jev shadow | Agregar un adaptador de clasificación detrás de una interfaz, con `Choice` de IDs, versión fija, trazas, límites y fallback. Ejecutarlo sobre documentos guardados sin modificar matches. | Tabla de errores Jev vs. heurística actual, tiempos y costo real por ítem/documento; evaluar especialmente español y abreviaturas argentinas. |
| 4. Asistido y auto | Mostrar Jev al revisor; calibrar umbral de confianza, ventaja sobre segundo puesto, acuerdo A/B y vetos por marca/tamaño/pack. Incorporar estados/procedencia e impedir que una recomparación borre decisiones. Activar auto por flag y luego por porcentaje. | Precisión de auto-match acordada sobre muestra independiente, sin falsos positivos críticos; reducción medible de revisiones. Ante poca muestra, continuar asistido. |
| 5. Proveedores | Diseñar y probar publicación de ofertas validadas en la fuente de precios entre proveedores, con vigencia y trazabilidad. | Comparaciones entre proveedores correctas sobre el mismo producto/presentación y rollback o anulación de una oferta equivocada. |

No fijar ahora un umbral como `0.90`: el valor no tiene significado operativo sin calibración local. Para una meta inicial exigente, medir por separado **recall de extracción**, **recall@K**, **precisión de auto-match** y **porcentaje de casos automatizados**. Exigir evidencia suficiente antes de declarar, por ejemplo, 99 % de precisión; una muestra pequeña sin errores no lo demuestra.

## Pruebas y costos

- Pruebas de contrato con respuestas simuladas de Gemini, Luna y Jev; errores de red, timeout, 429, JSON inválido, `ninguno`, duplicados de página y conflicto de importes.
- Casos de productos argentinos con abreviaturas, acentos, marcas parecidas, variantes de sabor, tamaños `g/ml/kg/l`, packs `x6/x12` y ofertas con impuesto dudoso. Confirmar aislamiento por `business_id` y proveedor.
- Regresión de aceptación/corrección/rechazo humano, recomparación del documento y preservación de feedback. No mezclar `auto_matched` con `confirmed_by_human` en el aprendizaje posterior.
- Evaluación offline con etiquetas humanas y desglose por proveedor/página, no solo un promedio global. Una segunda lectura que repite la misma omisión no cuenta como cobertura.
- Medir costo y latencia **por PDF y por precio utilizable**: páginas/regiones llamadas a A y B, tokens de entrada/salida, tokens de imagen si aplica, llamadas Jev, reintentos y revisiones humanas. Jev cobra por entrada y su precio/modelo puede cambiar; no asumir que doble extracción reduce el gasto total. [Precios y límites de Jev](https://docs.typesafe.ai/models), [modelo Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna).

El piloto debería empezar con `dual_shadow` y `jev_shadow` sobre documentos ya conocidos. La decisión de activar `jev_auto` depende de los resultados medidos, no de que Jev parezca rápido o barato en una demostración aislada.
