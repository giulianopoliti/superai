# Handoff: lectores OpenAI para los dos métodos

Fecha: 6 de octubre de 2026. Estado: diseño para implementación; ningún cambio de runtime.
Responsable de esta preparación: agente `openai_extraction`.
Plan principal: [PLAN_DOS_METODOS_EXTRACCION.md](../PLAN_DOS_METODOS_EXTRACCION.md).

## 1. Qué existe y qué no

- `OpenAISupplierOfferDocumentProvider` realiza una llamada `responses.parse` por
  archivo/bloque, con PDF inline o imagen. Usa `max_retries=0` y el modelo configurado.
- El wire actual `SupplierOfferReaderExtraction` devuelve páginas locales y ofertas
  con campos separados. `offer_price` es obligatorio y `price_type` tiene default
  `unit`. No contiene bloques incompletos ni evidencia tipada por campo.
- El prompt de chunks pide una entrada por página, incluso vacía. Promociones sin
  precio absoluto se convierten en advertencias; su texto no tiene representación
  completa obligatoria. Aún no existe lectura B ni selección A/B en este proveedor.
- `reader_extraction()` conserva observaciones por proveedor y cobertura. No
  conserva `response.usage`, ID de respuesta, versión del prompt ni respuesta fallida.
- `ChunkedDocumentExtractionProvider` ya limita concurrencia y tiene recuperación
  por página. El builder vigente lo limita a dos páginas por chunk. La clase acepta
  hasta seis para compatibilidad con pruebas/uso legacy.
- `_map_pages()` mapea solamente ofertas e informes de página; `_extract_pdf()`
  reconstruye metadata final. Los resultados originales quedan en `provider_attempts`,
  pero nuevos bloques/usage no serían todavía accesibles como contrato final.
- La política de packs visual es otra llamada posterior del endpoint. Por tanto, la
  recuperación actual no constituye un presupuesto único de una relectura por página.

La comprobación se hizo leyendo código y pruebas existentes. No se ejecutaron APIs
pagas ni se evaluó la calidad de transcripción sobre PDFs reales en esta tarea.

## 2. Contrato recomendado para congelar

Usar wire V2 independiente. No ampliar `SupplierOfferObservation` legacy haciendo
su precio nullable: eso rompería reconciliación, persistencia y consumidores existentes.
Los nombres siguientes son propuestas concretas a congelar por el integrador.

### Wire común de lectura

```text
ReaderFragmentV2:
  text: str
  legibility: Literal["readable", "partial", "unreadable"]

ReaderCoverageV2:
  estimated_offer_blocks: int | None
  needs_visual_review: bool
  warnings: list[str]

ReaderPageV2:
  page_number: int | None                 # local, nunca impresa/original
  page_conditions: list[ReaderFragmentV2] # condiciones generales, no ofertas
  blocks: list[ReaderBlockV2]             # orden visual indicado por el lector
  coverage: ReaderCoverageV2

ReaderBlockV2:
  fragments: list[ReaderFragmentV2]       # líneas/unidades visibles; pueden ser mixed
  page_condition_indices: list[int]      # referencias locales; [] si no aplican
  warnings: list[str]
```

Todos los objetos deben ser cerrados (`additionalProperties=false`). Las claves
se incluyen aunque el valor sea `null` o lista vacía. No emitir IDs globales ni
coordenadas de recorte desde OpenAI. Un bloque cuyo texto sea ilegible conserva
su presencia mediante fragmento marcado `unreadable`, texto visible disponible y aviso;
no rellenar letras/precios por intuición. No guardar placeholders como nombres comerciales.

El backend deriva `full_text` con `"\n".join(fragment.text for fragment in fragments)`.
Conservar literalmente espacios y signos de cada `text`; los separadores añadidos
son canónicos y no se atribuyen al PDF. Mantener también la lista de fragmentos:
`full_text` es una representación reconstruida, no una transcripción perfecta ni
garantía de que no hubo omisiones visuales. Esta decisión evita duplicar el mismo
texto en la salida OpenAI. No normalizar ni eliminar palabras sobre la evidencia original.

### Método A: propuestas de campos sobre los bloques

`ReaderStructuredExtractionV2` contiene `supplier_name`, páginas y warnings.
Sus bloques extienden el común con `proposals: list[ReaderOfferProposalV2]`.
Un bloque puede generar cero propuestas, una o varias variantes.

```text
ReaderEvidenceRefV2:
  scope: Literal["block", "page_condition"]
  fragment_index: int
  quote: str

ReaderFieldEvidenceV2:
  field: Literal["raw_name", "brand", "unit_size", "unit", "offer_price",
                 "currency", "tax_included", "price_type", "package_quantity",
                 "retail_contents_count", "minimum_purchase"]
  refs: list[ReaderEvidenceRefV2]

ReaderOfferProposalV2:
  raw_name: str | None
  brand: str | None
  unit_size: ReaderDecimal | None
  unit: Literal["g", "kg", "ml", "l", "unit"] | None
  offer_price: ReaderDecimal | None
  currency: str | None
  tax_included: bool | None
  price_type: Literal["unit", "pack", "unknown"]
  package_quantity: int | None
  retail_contents_count: int | None
  minimum_purchase: ReaderDecimal | None
  confidence_score: ReaderDecimal | None
  field_evidence: list[ReaderFieldEvidenceV2]
  warnings: list[str]
```

No usar un diccionario libre como `field_evidence` en el wire. El backend rechaza
campos duplicados o refs ajenas al bloque/condiciones permitidas. Confianza nullable
significa desconocida; no rellenarla como 0.5 para decisiones nuevas.

La presencia de un número en `offer_price` no lo confirma. Antes de confirmar precio
y cantidad comercial se exigen referencias válidas y una interpretación compatible
con el texto. Verificar cita exacta en el fragmento, y convertir su aparición a spans
locales. Si la misma cita aparece varias veces, no atribuirla arbitrariamente; conservar
referencia al fragmento y marcar ambigüedad hasta seleccionar una aparición concreta.
Normalizar moneda/unidad y números en backend conservando la cita original.
`raw_name` conserva términos de producto/presentación; todo el resto del texto sigue
en el bloque, aunque no integre ese campo. La ausencia de evidencia no equivale a `unit`.

El esquema final `ExtractedSupplierOfferItem` mantiene `offer_price` obligatorio.
Convertir únicamente propuestas con nombre legible y precio absoluto válido, además
de política de evidencia cumplida. Datos incompletos van al ledger; no inventar cero,
nombre genérico ni cantidad. Un precio válido con base desconocida puede guardarse
como oferta `unknown` pendiente de comparación, sin dividirlo.

### Método B: transcripción y relaciones

`ReaderBlocksExtractionV2` contiene `supplier_name`, páginas comunes y warnings.
No contiene propuestas ni campos precio/cantidad derivados. Reutilizar transporte
inline y cliente de A; cambiar prompt y `text_format`, sin cambiar modelo durante
la comparación inicial. OpenAI debe distinguir bloques/condiciones por estructura
visible, pero no adjudicar todos sus fragmentos a una categoría semántica.

El backend registra evidencia antes de llamar al clasificador JEV. JEV recibe texto
del bloque, condiciones referidas y candidatos detectados localmente. El ensamblador
retorna ofertas finales o pendientes, nunca valores inventados. Un fragmento `mixed`
se conserva completo aunque luego tenga varias apariciones con roles distintos.

## 3. IDs y ledger común de evidencia

El integrador crea `analysis_run_id` antes de la primera llamada y un `reading_id`
por intento. IDs deterministas dentro de esa lectura:
`{run_id}:{reading_id}:p{original_page}:b{block_index}` y sufijo `:f{fragment_index}`.
Los índices son backend, basados en arrays validados; no ordenarlos luego por nombre.
Si la página local es inválida, conservar evidencia bajo `reading_id` como no ubicada;
no adivinar página. Señalar explícitamente su ubicación desconocida para revisión.

Los IDs se mantienen al clasificar, persistir y mostrar esa lectura. Una relectura
genera otra versión: sus posiciones pueden cambiar. Enlazar versiones solo con
evidencia compatible y relación registrada; no prometer el mismo ID por posición
entre intentos, ni fusionar por nombre o por similaridad fuzzy. Repeticiones en la
misma página pueden ser dos promociones reales.

`OfferTextBlock` interno debe incluir `block_id`, `reading_id`, `page_number`,
`block_index`, fragmentos identificados, `full_text`, condiciones referidas,
`derived_offer_ids`, estado y motivos pendientes. Cada bloque recibido queda
representado por ofertas derivadas o pendiente; las variantes conservan referencia
al bloque origen. Evidencia anterior nunca desaparece por una relectura vacía.

Proponer campos tipados `text_blocks`, `pending_blocks`, `provider_calls` y
`analysis_method` para el resultado interno V2, o un envelope V2 propio convertido
al `SupplierOfferExtraction` compatible. El integrador decide la ubicación final.
Al persistir en metadata, evitar copias completas del bloque por cada oferta;
guardar evidencia completa una vez por documento y referencias por ítem.
Revisar también la duplicación actual de resultados en `provider_attempts`.

## 4. Cobertura y recuperación compartida

La cobertura de B se mide con bloques observados, no con `len(items)`, que permanece
vacío antes de JEV. Comparar el conteo estimado del lector contra bloques, separado
de ofertas derivadas y pendientes; un bloque multivariante altera la relación 1:1.
Exigir un informe por página local adjunta; validar duplicados, rango y ausencias.
Los estimados y la confianza no prueban que el PDF fue leído completo.

Una página con cero bloques y aviso visual/estimado positivo puede requerir recuperación.
Una página explícitamente sin ofertas no dispara relectura por ser vacía. Un bloque
sin precio porque la promoción solamente anuncia descuento queda pendiente y no
se vuelve a leer por defecto. Texto completo con duda semántica se revisa con JEV
o humano; no es motivo suficiente para pagar otra visión.

Agregar ledger de recuperación por página, propiedad del servicio de análisis:
`initial_done`, `reread_count` (0/1), motivos y resultado. Recopilar falta visual o
contradicciones recuperables, reservar presupuesto antes de ejecutar y releer esa
página completa como máximo una vez. Cualquier duda posterior permanece pendiente.
La revisión visual de packs debe consumir ese mismo presupuesto o desaparecer de
la ruta V2; no ejecutar el reviewer legacy después de una recuperación V2.

No reutilizar `_suspicious_pages()` sin adaptarla: hoy cero `items` implica `empty`
y cualquier warning implica `reader_warning`. Eso haría que B y páginas completas
con dudas semánticas se releyeran innecesariamente. La recuperación legacy puede
mantenerse mientras los clientes existentes aún usen ese contrato; V2 necesita
decisión explícita del servicio común después de interpretar la evidencia.

## 5. Usage, errores y medición

Registrar por llamada, antes de comprobar `output_parsed`:

```text
ProviderCallRecord:
  call_id, analysis_run_id, reading_id, provider, model
  prompt_version, schema_version, analysis_method, response_id | None
  phase: initial | reread
  start_page, end_page, duration_seconds, status, safe_error | None
  input_tokens | None, cached_input_tokens | None
  output_tokens | None, reasoning_output_tokens | None, total_tokens | None
```

Capturar `response.usage` y detalles cuando existan. Ausencia se representa `null`,
no cero; mocks antiguos pueden carecer de atributo. No sumar cached tokens como
tokens extra: forman parte de input; reasoning forma parte de output. Cada llamada
se agrega una vez por `call_id`, incluso si la lectura se almacena en varios lugares.
Si el SDK lanza excepción sin respuesta utilizable, registrar intento/duración/error
y usage desconocido. Refusal, incomplete y salida no parseada no se interpretan
como una página vacía exitosa; conservar diagnóstico seguro y usage disponible.

El registro de métricas debe sobrevivir a la excepción mediante el envelope de
llamada o colector compartido; si solo se coloca en un resultado de éxito se pierde
el costo de fallos. No revelar claves ni cuerpos grandes en logs.
Costo estimado separado con tarifa/modelo/fecha; sin tarifa o usage, costo desconocido.
PDF añade texto e imágenes al contexto: más fragmentos/evidencia también puede
aumentar la salida. No prometer menor costo de B antes de medir documentos completos.

## 6. Cambios futuros y ownership

El integrador conserva `schemas.py`, `dual.py`, `settings.py`, `main.py`,
`supplier_offers.py`, `matching.py` y pruebas compartidas. Congela contratos antes
de habilitar trabajo runtime. El agente de extracción puede implementar luego:

- `app/providers/documents/openai.py`: transporte compartido, formatos A/B,
  captura de respuesta/usage y selección de prompts V2.
- `app/providers/documents/base.py`: conversión/envelope y preservación de evidencia,
  únicamente después del contrato congelado.
- `app/assistant/prompts/supplier_offer_structured_v2.md` y
  `supplier_offer_blocks_v1.md`: instrucciones versionadas, ejemplos argentinos,
  páginas locales, textos como evidencia y protección ante instrucciones en PDF.
- `tests/providers/test_openai_document_extraction_v2.py`: lectores y schemas V2;
  adaptar test existente solo cuando el integrador coordine compatibilidad.

API acordada para capacidades: `GET /procurement/supplier-offers/analysis-methods`.
El lector no resuelve capacidades frontend, consulta catálogo ni escribe DB.
No instalar dependencias, crear migraciones ni activar B como parte de esta preparación.

## 7. Pruebas y criterios de cierre del lector

1. Schemas A/B estrictos y cerrados, claves requeridas con nullable; Decimal sin
   lookarounds incompatibles, siguiendo la prueba actual del SDK.
2. Un request inline por chunk; modelo sin cambios, max_retries=0, selección de
   prompt/formato correcta, sin subida/remoción de archivo remoto adicional.
3. PDF de páginas locales 1–2 remapeado una vez a originales 7–8; informes ausentes,
   duplicados/ajenos, evidencia no ubicada y conditions ajenas generan pendientes.
4. A conserva producto sin precio y descuento sin importe; B conserva todos los
   fragmentos/condiciones recibidos, incluido `mixed` y texto no clasificable.
5. Variantes en un bloque, precio anterior/actual, contenido x48, bulto x6 y mínimo
   seis conservan evidencias distintas. Referencia inexistente, cita alterada o
   números incompatibles no confirman campos; valores ausentes nunca se inventan.
6. full_text recompuesto conserva exactamente fragmentos más separador canónico;
   IDs distintos para ofertas repetidas y versiones de relectura; refs estables.
7. Coverage B usa bloques; página sin ofertas declarada no se vuelve a leer por
   defecto; recuperación conjunta nunca supera una adicional por página.
8. Usage en éxito/refusal/incomplete, ausencia nullable, errores sin respuesta,
   cached/reasoning no duplicados y agregación única por llamada.
9. Relectura vacía no borra evidencia previa; contradicción conserva ambas versiones
   y pendiente. No segunda revisión visual de packs por fuera del ledger.
10. Persistencia de ledger e ítems requiere pruebas del integrador: precio final
    obligatorio, sin precio inventado, `unknown` no dividido y tenant aislado.

Cierre local: lectores A/B producen evidencia V2 válida y todos los casos anteriores
pasan con proveedores simulados; contrato legacy conserva sus pruebas. Cierre de
producto: servicio/API/UI integrados y pendientes visibles. La evaluación real de
omisiones, costo y latencia requiere PDFs etiquetados y es una entrega posterior;
no se demuestra con una prueba del shape JSON ni con el conteo del propio lector.

## 8. Referencias verificadas

Structured Outputs permite exigir estructura, pero refusals e incomplete necesitan
tratamiento explícito; la estructura no valida hechos del documento.
[Documentación oficial de Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

Las entradas PDF usan texto e imágenes en contexto, con impacto en tokens.
[Documentación oficial de entradas de archivos](https://developers.openai.com/api/docs/guides/file-inputs).

Los campos usage indicados también fueron contrastados con
`.venv/Lib/site-packages/openai/types/responses/response_usage.py` instalado.
