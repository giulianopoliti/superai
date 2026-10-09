# Handoff JEV: dos métodos de extracción

Fecha: 6 de octubre de 2026. Estado: revisión y propuesta de implementación;
este documento no activa funciones ni reemplaza el flujo actual.
Ownership de esta revisión: únicamente este archivo. No se ejecutaron APIs pagas,
instalaciones, migraciones ni escrituras en la base del negocio.

## Hallazgos para resolver antes de integrar

1. **Alta — decisiones comerciales antes de confirmar identidad.**
   `app/modules/procurement/matching.py:375`, `_with_commercials`, puede asignar
   `buy` por similitud >= 0.75 y diferencia de costo aunque `auto_accepted` sea
   falso (`matching.py:453` y `matching.py:456`). Separar sugerencia de candidato y resultado comercial confirmado:
   una identidad pendiente debe permanecer en revisión. La incorporación de JEV
   no debe convertir su probabilidad en una autorización de compra.
2. **Alta — feedback antiguo omite compatibilidad física.**
   `_match_item_with_feedback` (`matching.py:256`, aceptación en `:286`) devuelve
   aceptados sin revalidar tamaño/variante; `retrieve_pilot_candidates`
   (`matching.py:125`) incluso permite esos IDs pese al filtro de tamaño.
   Respetar la corrección humana, pero detectar presentación nueva o evidencia
   contradictoria antes de reutilizarla automáticamente. Conservar la regla y
   mostrar el conflicto; no borrar feedback ni reemplazarlo por JEV.
3. **Alta — falta conservación de artículos sin precio.**
   `SupplierOfferObservation.offer_price` (`schemas.py:336`) y `SupplierOfferItem.offer_price`
   son obligatorios. No se puede introducir B con bloques incompletos usando
   cero o descartándolos. El integrador debe acordar el registro de evidencia
   pendiente por documento y que A también lo conserve.
4. **Media — validador del chooser incompleto.**
   `app/providers/jev.py:50`, `JevHttpChooser.choose`, comprueba opciones y modelo,
   pero no rangos/suma/finitud de todas las probabilidades, claves exactas de
   preguntas ni pertenencia de productos al negocio/oferta. El reconciliador
   legacy tiene más controles, pero solo admite una pareja Gemini/OpenAI.
   Usar validación uniforme en el contrato nuevo; no reutilizar ese reconciliador
   como clasificador ni reactivar Gemini.
5. **Media — abstenciones colapsadas.**
   `JevChoice` (`jev_pilot.py:164`) devuelve `product_id=None` tanto para `none` como `insufficient`.
   Distinguir ausencia de coincidencia, evidencia insuficiente y error de proveedor.
6. **Media — cantidad comercial puede reaparecer por regex.**
   `SupplierOfferService._build_item` (`supplier_offers.py:167`, fallback en `:193`) reconstruye `package_quantity` desde
   `raw_name` si falta y no hay `pack_review`. El contrato nuevo debe marcar
   interpretación verificada/pendiente y evitar ese fallback: contenido x48 no
   puede convertirse en 48 envases vendibles. Mantener compatibilidad manual
   como ruta separada, con pruebas.
7. **Media — evidencia libre no equivale a evidencia validada.**
   `apply_pack_review` (`pack_review.py:37`) acepta confianza >= 0.90 y texto no vacío; no comprueba
   que la cita aparezca en el texto ni que respalde la cantidad. El sistema nuevo
   requiere referencias válidas y consistencia, además del umbral evaluado.

## Qué existe y qué falta

Existe `JevHttpChooser`, con opciones cerradas de producto, `none` e
`insufficient`, versión de modelo, distribución y usage. Se utiliza para el
piloto shadow de `app/modules/procurement/jev_pilot.py`, que no cambia vínculos
ni precios. Existen recuperación top-k de piloto, controles conservadores de
identidad en `identity.py`, normalización de precios en `pricing.py`, revisión
visual OpenAI de packs y metadata en documentos/ofertas.

Faltan integración selectiva en el análisis normal, clasificador de fragmentos B,
selección de valores por evidencia, decisión textual de base del precio,
ensamblador, estados independientes, conservación de pendientes, presupuestos
compartidos, medición de usage por todas las llamadas y pruebas de ambas rutas.
Los tests del chooser actual comprueban contrato cerrado y usage; no prueban
todo el conjunto adversarial propuesto abajo. Este agente no volvió a ejecutar
la suite ni validó calidad contra proveedores reales.

## Capacidades verificadas y restricciones

JEV consume texto/JSON, devuelve decisiones tipadas y probabilidades, y no
recupera texto de imágenes ni genera explicaciones o precios nuevos.
La calibración poblacional documentada no garantiza exactitud por oferta ni
calibración en nuestro catálogo argentino.
[Documentación oficial System One](https://docs.typesafe.ai/concepts/system-one).

Una pregunta Choice devuelve una opción y su distribución. Varias preguntas de
una misma solicitud se evalúan por separado sobre el mismo estado; una no puede
depender de la respuesta de otra. Agrupar preguntas reduce solicitudes, aunque
cada pregunta agrega tokens. La aplicación debe incluir opciones de abstención.
[Documentación oficial Choice](https://docs.typesafe.ai/primitives/choice).

Fijar la versión de modelo. Según la documentación consultada, `jev-1.13.0`
admite 64k tokens por solicitud y 32k para estado más pregunta más larga; su
tarifa es USD 0.042/M tokens de entrada y salida gratuita. Son límites externos
consultados hoy, no objetivos de tamaño de nuestros bloques. Evaluar español y
texto local antes de activar autoaceptación; no prometer ahorro total por tarifa.
[Modelos oficiales](https://docs.typesafe.ai/models).

## Contrato que necesita JEV del lector

Contrato a acordar con el integrador antes de editar schemas compartidos:

Acuerdo recibido durante esta revisión: usar wire V2 A/B independiente del legacy,
con páginas/bloques y fragmentos ordenados sin IDs inventados por OpenAI. Backend
genera IDs por ejecución/página/intento/orden y recompone `full_text` canónico.
A añade campos nullable y referencias locales validadas; B solo transcribe.
El texto canónico acredita consistencia entre fragmentos recibidos, no equivalencia
certificada con el PDF. No exigir simultáneamente otra transcripción íntegra libre
que pueda divergir; conservar contenido recibido y formato de unión acordado.

- `run_id`, `analysis_method`, `schema_version`, página original, ordinal y
  `block_id` asignados/validados por backend. No IDs de catálogo inventados.
- `raw_text` íntegro del bloque; `fragment_id`, texto y orden de cada fragmento.
  Preservar partes ilegibles, precios anteriores/tachados y condiciones de página.
- Referencias a condiciones generales de la propia página/documento y alcance
  conocido o pendiente; no aplicar automáticamente una condición a todo el PDF.
- Candidatos locales de valores: `candidate_id`, fragmento, inicio/fin en texto,
  literal original, tipo de parseo y valor decimal/entero/unidad propuesto por
  backend. Una aparición numérica no tiene todavía función comercial.
- Para A, campos separados y referencias a la evidencia original, incluidos
  bloques incompletos; un precio sin cita/relación verificable queda pendiente.
- Conteos/cobertura y advertencias separados de las certezas comerciales.

Todos los offsets son sobre el texto recibido, no coordenadas certificadas del
PDF. Validar `text[start:end] == literal`. No usar offsets inválidos para buscar
otra coincidencia silenciosa. Mantener ambas lecturas si una recuperación cambia
una cifra; no sobrescribir original ni emparejar ofertas solo por nombre.

## Interfaces propuestas

Los nombres siguientes son orientativos; el integrador fija la versión definitiva.
Providers reciben DTOs de texto/candidatos, sin repositorio, credenciales de DB ni
acciones. Los servicios hacen consultas y persistencia con `business_id`.

| Interface | Entrada | Salida | Responsable |
|---|---|---|---|
| `classify_block` | Bloque, condiciones, fragmentos y candidatos de valores | Respuestas cerradas por pregunta + usage | Provider JEV |
| `assemble_interpretation` | Bloque y respuestas validadas | Campos con referencias o motivos pendientes | Servicio determinista |
| `retrieve_identity_candidates` | Oferta, catálogo del tenant y feedback | IDs candidatos compatibles y motivos | Matching/backend |
| `decide_offer` | Evidencia comercial + candidatos compatibles | Identidad/base/selecciones/reason IDs | Provider JEV |
| `validate_decision` | Decisión, evidencia y candidatos originales | Estados independientes y campos utilizables | Política backend |

`ChoiceDecision` debe retener `question_id`, `choice`, `confidence`, distribución,
modelo/prompt versionados. Un envelope contiene `run_id`, `block_id`, fase,
preguntas esperadas, duration/usage y error seguro. Usar `identity_status` y
`price_status` distintos: identificar el producto no confirma el precio, y un
precio claro no confirma el producto. Estados mínimos: `confirmed`, `pending`,
`no_match`; para precio no corresponde `no_match`.

## B: clasificación y armado sin perder información

1. Guardar la evidencia antes de interpretar. Segmentación local conservadora
   preserva puntuación, orden y texto reconstruible; no fragmentar por palabra.
2. Crear una pregunta por fragmento para la categoría dominante: `product_name`,
   `brand`, `variant`, `retail_size`, `retail_contents`, `commercial_quantity`,
   `current_price`, `previous_price`, `minimum_purchase`, `promotion`,
   `tax_condition`, `other`, `uncertain`, `mixed`.
3. En la misma solicitud se pueden preguntar precio actual, base del precio y
   cantidad comercial mediante candidatos cerrados. Cada pregunta incluye todo
   el bloque, IDs relevantes e instrucciones autosuficientes; no presupone las
   respuestas de clasificación. El backend solo utiliza combinaciones coherentes.
4. Pregunta `current_price`: IDs de ocurrencias monetarias + `none` +
   `insufficient`. `previous_price`: sus IDs + `none` + `insufficient`.
   Pregunta `commercial_quantity`: IDs de ocurrencias enteras + `not_applicable`
   + `insufficient`; la validación distingue contenido y compra mínima.
5. `mixed` conserva la línea completa. Para “6 botellas de 1,5 L — $12.000 por
   bulto, mínimo 2 bultos”, candidatos separados representan 6, 1,5 L, $12.000
   y 2. JEV elige roles/IDs; backend produce cantidad 6, tamaño 1,5 L y mínimo
   2 solo si cada relación queda sustentada. No divide por 2 ni 1,5.
6. Nombre/marca/variante se construyen copiando fragmentos elegidos en su orden.
   Para campos con varios fragmentos, usar preguntas cerradas por fragmento
   (pertenece/no/insuficiente), o una lista de composiciones conservadoras
   generadas localmente y seleccionables por ID. Choice no produce una lista
   libre de palabras. Si una línea mixta no admite separar el nombre con evidencia
   suficiente, conservar nombre original/contexto y marcar estructuración pendiente.
7. `other` no es basura: se conserva; `uncertain` no significa falso. Bloque sin
   precio válido queda en el ledger de pendientes, sin oferta ficticia. Bloque
   con varias variantes/precios requiere correspondencia explícita por variante;
   si no se puede construir, queda pendiente sin crear el producto cartesiano.

## A/B: identidad y base del precio

Backend carga catálogo y feedback del negocio una vez por ejecución. Recupera
candidatos internos top-k (inicio propuesto: 5) sin vincularlo al límite de salida
de la API, actualmente 1. Medir recall; top-5 no asegura que el producto exista.
Marca/presentación explícitamente contradictorias excluyen autoaceptación; datos
faltantes mantienen candidatos pero pueden exigir abstención. Reusar normalización
canónica `physical_size`; no duplicar tablas de equivalencias divergentes.

Resolver casos deterministas y feedback compatible primero. Ofrecer a JEV IDs
opacos mapeados a productos del negocio, `none` (ninguno corresponde) e
`insufficient` (faltan datos). Identidad no utiliza precio/costo para preferir
productos. JEV no ve el catálogo completo ni consulta la DB. El filtro y la
revalidación excluyen otros tenants incluso si un repositorio/mock los devuelve.

Base del precio: `unit`, `pack`, `unknown`. No limitar a SI/NO: se necesita
`unknown` para no forzar una decisión cuando falta evidencia. `pack` exige una
cantidad comercial entera válida y asociada al precio del mismo bloque;
`unit` no transforma contenido interno en cantidad comercial. `minimum_purchase`
y condiciones fiscales permanecen separadas. Una etiqueta x48 sola no demuestra
48 paquetes ni una base unit inequívoca. El costo anterior del catálogo puede
motivar revisar, pero no prueba la base del precio.

La política requiere pertenencia de referencias, parseo consistente, relación
comercial y ausencia de contradicción. Después aplica umbral/probabilidad/margen
evaluados por tarea y versión; no copiar 0.90 de visión como garantía JEV. Hasta
evaluar el dominio, decisiones ambiguas de JEV son propuestas auditables pendientes.
Backend calcula precio unitario con Decimal; no pide cálculos a JEV.

## Validación y fallas parciales

- Envelope/modelo/preguntas exactos; claves duplicadas JSON, IDs desconocidos o
  respuestas de otro bloque no se admiten.
- Probabilidades finitas entre 0 y 1, claves exactas y suma aproximadamente 1
  (tolerancia documentada por contrato); confianza finita en rango. Confianza y
  probabilidad elegida son magnitudes distintas, no deben exigir igualdad.
- Validar cada decisión del negocio tras validar el envelope. Si falta una
  pregunta, los campos que dependen de ella quedan pendientes. Las otras
  respuestas solo son reutilizables si su binding y formato son seguros; no
  rellenar silenciosamente la faltante con otra opción.
- Fallo JEV en B no cambia método a A: conserva evidencia y parcial explícito.
  Fallo en decisión opcional de A conserva campos deterministas confirmados.
- Error de identidad no elimina precio/oferta. Error de pack no divide ni borra
  precio observado. Referencia incompleta no permite usar un precio tachado.
- Abstención por texto completo no dispara visión. Backend decide relectura
  únicamente si falta/inconsiste evidencia visual recuperable. Todas las etapas
  comparten máximo una recuperación por página; no revisión visual pack adicional.
- Probabilidades no diagnostican cobertura de página ni prueban que no se omitió
  un bloque. Pendientes visibles y etiquetas humanas siguen siendo necesarios.

## Presupuesto de llamadas y medición

Inicio conservador: una solicitud de clasificación/selección por bloque B y una
solicitud posterior de decisiones pendientes de identidad/precio agrupadas por
oferta; A solo necesita esta última si hay dudas. No llamar JEV por campo.
Una recuperación visual puede justificar reevaluar el bloque cambiado, con nueva
versión de evidencia; no reconsultar bloques idénticos por ejecutar otro paso.

Formalizar `max_jev_calls`, `max_jev_input_tokens`, `deadline_seconds`, concurrencia
y máximo de preguntas/tamaño por request en configuración del integrador. Agotar
presupuesto deja pendientes; nunca truncar evidencia sin indicarlo ni excederlo
mediante retries ocultos. Empezar sin retry automático; si se habilita uno por
429/error transitorio, debe consumir el mismo presupuesto y respetar deadline.

Evitar agrupar ofertas distintas hasta probar aislamiento; después evaluar lotes
con ID de pregunta y contexto de bloque explícitos. No llenar el contexto 64k por
conveniencia: medir pérdida de precisión, latencia y tokens. La caché opcional
se identifica por tenant, evidencia, candidatos, modelo y prompt/política; no
reutilizar un resultado si cambió catálogo, presentación o feedback.

Registrar por llamada estado, fase, oferta/bloque, modelo/versiones, preguntas,
tokens, duración, intentos y motivo de abstención. Comparar costo total OpenAI+JEV,
incluida salida textual de B y revisión humana. No hay medición real de ahorro
en esta entrega.

## Fases y pruebas de aceptación

1. **Integrador:** aprobar contratos, ledger de pendientes y estados independientes.
   Pruebas: cada bloque recibido tiene oferta derivada o pendiente; no cero inventado;
   página original y referencias válidas tras chunk/recovery.
2. **Agente JEV:** DTOs/provider y prompts versionados, sin DB. Pruebas mock de
   criterios cerrados, none/insufficient, modelos movibles, respuesta parcial,
   probabilidades inválidas/NaN/Infinity, claves duplicadas y usage.
3. **Backend/JEV:** detector de ocurrencias y ensamblador. Fixtures: x48 contenido,
   bulto x6, mínimo 2, precio actual/anterior, “lleve 3 pague 2”, porcentaje sin
   precio absoluto, decimal argentino, nombres mixtos y variantes múltiples.
4. **Backend:** política identidad/precio y feedback. Fixtures: producto de otro
   negocio, presentación incompatible, duplicados de catálogo, sin candidato,
   feedback rechazado/aceptado antiguo, identidad confirmada con precio pendiente,
   precio confirmado con identidad pendiente. No compra automática pendiente.
   Cambios en feedback, gate comercial y fallback de persistencia son ownership
   exclusivo del coordinador (`matching.py`, `supplier_offers.py`); entregar a
   ese dueño fixtures/criterios, sin modificarlos desde el agente JEV.
5. **Integrador:** presupuestos/recovery y persistencia/API en fixtures. Caída parcial
   no cambia método ni elimina bloques; una sola revisión por página, presupuesto
   agotado visible y referencias conservadas en metadata.
6. **QA:** comparar interpretación con lectura congelada y luego ejecuciones A/B
   completas con etiquetas humanas separadas. Medir candidatos correctos, omisiones,
   precios/cantidades correctos, abstenciones, falsos aceptados, p50/p95 y costo.

No instalar librerías para esta propuesta. No introducir migraciones hasta elegir
persistencia de evidencia. No activar autoaceptación de JEV solo porque las pruebas
mock validaron el formato; la evaluación de dominio sigue pendiente.

Ownership futuro confirmado: nuevos providers JEV, prompts/pruebas propios y
`app/modules/procurement/block_interpretation.py` / `decision_policy.py`.
Schemas comunes, endpoint, configuración, orquestador, matching y persistencia son
del coordinador. `app/providers/jev.py` y archivos legacy solo cambian después de
una asignación explícita; este plan favorece mantener el piloto separado.
