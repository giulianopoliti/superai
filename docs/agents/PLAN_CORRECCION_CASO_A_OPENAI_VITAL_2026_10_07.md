# Caso A: OpenAI estructurado, Vital 07/10 — investigación y plan

Fecha: 7 de octubre de 2026. Investigación independiente, sin implementación funcional.

Corrida/documento: `dbea1644fa0b4b7f8c021b0b63abb415`. Archivo: `116561.pdf`. Tenant: `demo-business`. Método: `openai_structured`.

## Conclusión

OpenAI reconoció nombres, marcas, tamaños e importes de muchas ofertas. El resultado de 0 vinculadas no demuestra que el lector no haya entendido los productos: hay pérdidas por validación de evidencia, una búsqueda que ordena peor algunas identidades exactas, reglas de aceptación excesivamente dependientes de texto idéntico y un presupuesto que se consume antes de resolver dudas. Tampoco demuestra que las 49 ofertas se puedan aceptar: varias propuestas reúnen variantes, faltan presentaciones del catálogo y hay candidatos de otra clase de producto.

JEV no falló en esta corrida: no recibió llamadas. `allow_jev_acceptance=False` está registrado e impide convertir propuestas de JEV en aceptaciones; habilitarlo no resolvería citas inválidas, cantidad comercial ausente ni bases sin evidencia. Debe mantenerse hasta tener evaluación independiente.

## Evidencia y límites

Fuentes guardadas en `output/diagnostics/`: `openai-run-dbea1644-full.json`, `openai-run-dbea1644-analysis.json`, `openai-run-dbea1644-matches.json`, `catalog-demo-business-2026-10-07.json` y configuración segura `analysis-settings-safe-2026-10-07.json`.

El catálogo contiene 3260 productos y 0 reglas de feedback relevantes. Es un snapshot posterior a las corridas; no prueba que cada producto ya existiera cuando se ejecutó el análisis. Los candidatos persistidos sí prueban cuáles fueron las sugerencias finales entonces. Las listas internas top 5 no quedaron guardadas: las reconstrucciones siguientes se identifican como replay offline. El hash SHA-256 del archivo actual de policy coincide con el registrado en la corrida.

El ledger conserva fragmentos, condiciones y propuestas con citas. No conserva la respuesta HTTP original completa, el PDF de cada request ni todos los datos de coverage originales. Los textos de oferta de este informe son salida transcrita por el lector, **no el input textual original enviado a OpenAI**. El input se reconstruye desde `openai.py:158`: PDF inline de dos páginas, prompt `supplier_offer_structured_v2.md`, supplier hint y nombre de archivo. No se reconstruyó ni volvió a enviar el PDF.

## Hallazgos por prioridad

### P1 — Se agota el presupuesto antes de decidir y no es un límite de duración real

Hay 24 páginas, pero sólo se conservó evidencia de 1–4: 20, 11, 10 y 14 bloques. Las primeras dos llamadas terminaron bien: páginas 1–2 en 121,375 s con 12015 tokens de salida, páginas 3–4 en 111,594 s con 10684. Las de 5–6 y 7–8 fallaron con `APITimeoutError` en 76,391 y 85,109 s. No hubo requests para 9–24; quedaron `deadline_exhausted`. La duración total fue 237,125 s, frente al presupuesto de 180 s.

Cobertura exacta: 24 totales, 8 intentadas, 4 con resultado, 20 sin resultado y 16 sin intento. Hay 22 páginas para revisión: páginas 1–2 también registran cobertura visual incompleta. “22 para revisar” no equivale a “22 sin leer”.

`document_analysis.py:397` envía todo el tiempo global restante; `openai.py:169` utiliza ese valor e ignora el timeout de lector configurado (60 s). El SDK tiene `max_retries=0` en `openai.py:112`, por lo que no corresponde culpar retries automáticos. `future.result()` espera sin límite en `document_analysis.py:724`; el executor hace join. `decide_offers()` recién se ejecuta después de terminar la fase inicial (`:727`), dejando 31 rutas semánticas sin presupuesto. No hay `decision_audit` ni calls JEV.

Corrección propuesta: definir por separado deadline global, timeout por transporte, margen para parseo/persistencia y presupuesto para decisiones; procesar resultados en orden de finalización y decidir por lote completado. Garantizar cierre/cancelación del transporte antes de liberar archivos o slots. Un timeout de Future solo no cancela el SDK. Un simple `min(60, remaining)` descartaría las dos respuestas actuales exitosas de más de 100 s: debe acompañarse de reducción medible de salida o de un modo de procesamiento con duración explícita.

### P1 — Se pierden campos válidos por un error en una cita opcional

NATURA aceite 900 ml, `$3.649`, tiene nombre y precio citados correctamente, pero `tax_included` cita una oración con minúsculas mientras la condición fuente está en mayúsculas. `StructuredProposalAssembler` considera inválida cualquier referencia y descarta toda la propuesta (`document_analysis.py:187–219`). El bloque queda pendiente sin ficha. Responsabilidad compartida: el lector incumple literalidad; el assembler amplifica el error de un campo opcional y pierde los otros campos.

Corrección propuesta: validar por campo y conservar su propuesta original. Una cita fiscal inválida debe dejar `tax_included` pendiente; campos comerciales obligatorios deben seguir requiriendo sus propias citas válidas. No hacer equivalencia textual aproximada ni cambiar offsets sin una correspondencia con la fuente. Mantener fallos de scope, tenant y binding como rechazo del vínculo afectado.

### P1 — El validador elimina un precio correcto por confundir una marca con una unidad

L'OREAL ELVIVE máscara hyalur pure 300 g tiene fragmento legible y cita única `$8.499`, offsets 17–23. El replay produce `published_price_unverified`. La búsqueda de sufijos de unidad en `_price_evidence_valid` (`decision_policy.py`, regex de suffix) ve ` L'OREAL` y satisface `l\b`, porque el apóstrofe delimita una palabra. El monto se borra y la ficha no se publica.

Corrección propuesta: reconocer unidades ligadas a una expresión numérica con delimitadores válidos, sin interpretar el inicio de una marca como unidad. Regresiones: `$8.499 L'OREAL` válido; `1 l`, `1kg`, `10%` y compra mínima siguen sin ser precios. No quitar el control de contexto numérico completo.

### P1 — El ranking confunde similitud textual con identidad

La búsqueda interna filtra incompatibilidades y retiene top 5, pero puntúa con SequenceMatcher (65 %), overlap (20 %), marca (10 %) y tamaño (10 %) (`matching.py:434–487`). El orden de palabras afecta el score. Después sólo acepta automáticamente el primer candidato con score >= 0,80, tokens completos idénticos y margen >= 0,08 (`:244–250`). AYUDIN Original 2 L tiene un candidato con tokens idénticos, pero queda tercero compatible con 0,7234. FAVORITA harina 000 1 kg queda primera con 0,9562 y tokens idénticos, pero harina 0000 recibe 0,8896 y achica el margen a 0,0666.

Los vetos de `identity.py:47–94` no cubren grado de harina 000/0000, todas las fragancias, categoría ni cepa de vino. El mismo código admite como candidatos ALMA MORA Malbec → Blanco dulce y KNORR fideos → salsa. Actualmente la aceptación exacta evita activarlos; relajar globalmente los umbrales sería peligroso.

Corrección propuesta: priorizar equivalencia canónica exacta después de vetos estructurados; comparar margen dentro del conjunto semánticamente compatible. Tipar categoría, variante explícita, contenido y presentación; una ausencia en catálogo es información desconocida, no prueba de igualdad. Incorporar alias con alcance y versión, sin borrar palabras de variante indiscriminadamente. Separar score de recuperación, regla de identidad y recomendación comercial.

### P1 — El monto publicado y la base del precio están mezclados en las razones

49 fichas conservaron importe, todas con base pendiente. Hay 54 `price_evidence_unverified`, pero sólo 3 `published_price_unverified`: la primera razón engloba monto y base y es demasiado genérica. `_basis_supported` (`decision_policy.py:175–187`) sólo admite unas frases explícitas. La mayoría de propuestas `unit` no trae `field_evidence.price_type`; la policy las vuelve `unknown` (`:435–438`). El prompt muestra “por envase” y “por sobre”, pero la policy no acepta todas esas expresiones.

`LLEVANDO POR PACK` de HEINEKEN satisface la regex `por pack`, aunque describe una condición de compra y no prueba que $4.489 sea el total del pack. Su `package_quantity` es null; correctamente no se divide el monto. El precio XLT puede aportar consistencia aritmética, pero no autoriza por sí solo una base comercial ni una cantidad. La condición general “venta por bulto cerrado” tampoco transforma automáticamente todos los importes visibles en totales por bulto.

Corrección propuesta: representar por separado importe observado, precio de referencia XKG/XLT, base comercial y condición de compra. Introducir razones precisas (`price_basis_unverified`, `published_amount_unverified`, `commercial_quantity_missing`) y una policy versionada de interpretación de folletos, con evidencia y alcance. No usar costos del catálogo para confirmar bases.

### P2 — Citas repetidas y resumen esconden la causa recuperable

FAVORITA cita `$749` dos veces en el mismo fragmento: importe destacado y XKG. El assembler conserva la cita sin offsets (`document_analysis.py:190–198`); `evidence_text` exige una ocurrencia única y borra el monto. L EAU VIVE tiene una cita que también coincide con el prefijo de `$1.729,00`. Deben identificarse ocurrencias completas y roles; no elegir siempre la primera ni aceptar cualquier número repetido porque sus importes coincidan.

La UI reproduce los contadores de policy (`app.js:106–113`): 49 fichas − 49 sin `product_id` = 0 vinculadas. Los 6 bloques pendientes son grupos sin ficha utilizable; no representan las 49 identidades ni las 49 bases pendientes, ni las 20 páginas sin evidencia. `matching.py:556–588` aplica correctamente el gate V2 y conserva `recommendation=review`; el repositorio usa `auto_accepted` para status accepted/pending (`sql_procurement.py:760–765`). Un candidato sugerido, un producto confirmado y una recomendación de compra son tres resultados diferentes.

## Cinco ofertas con buenos candidatos y controles

Los números de bloque son locales y comienzan en cero. “Después” describe la policy guardada, no una implementación futura. Todas estas identidades y bases quedaron pending y `product_id=null`. Se detallan candidatos del snapshot y replay; no se promete aceptación automática de todas.

| Oferta y salida del lector | Evidencia e importe | Candidato del catálogo y score offline | Antes → después / causa |
|---|---|---|---|
| P1 b18: AYUDIN Lavandina original x2lt.; unit, 1869 | Nombre literal, `x2lt.`, `$1.869` [17,23] | **LAVANDINA AYUDIN 2L ORIGINAL**, 0,7234; 2 L; tokens idénticos; tercero compatible | Buen candidato presente → sin vínculo. Ranking favorece Lavanda 0,7684 y Pureza glaciar 0,7243; primero exacto no queda primero ni alcanza 0,80. Base sin cita. Después añade deadline. Es caso claro para corregir canonicalización/ranking. |
| P1 b3: FAVORITA Harina 000 x1kg.; unit, 749 | Nombre y tamaño válidos; `$749` aparece dos veces | **FAVORITA HARINA 000 1KG**, 0,9562; 1 kg; tokens idénticos | Propuesta completa → monto null y sin ficha. Margen frente a 0000: 0,0666 < 0,08; falta veto de grado. Resolver ocurrencia y veto antes de aceptar identidad. |
| P1 b2: NATURA Mayonesa sachet x125gr.; unit, 509 | `$509` [21,25], tamaño [49,56], condición “LLEVANDO POR DISPLAY” | **NATURA MAYONESA 125G**, 0,8106; 125 g; segundo compatible 0,5592 | Monto válido → ficha unknown, identidad pendiente. `sachet` impide igualdad de tokens. Buen candidato para alias de envase validado; no deducir unidades del display. Final sugiere mismo producto, pero `analysis_policy_review_required`. |
| P4 b3: COLGATE Crema dental ultra blanco x90g; unknown, 2669, moneda `$` | Multilínea: nombre f0; XKG f1; mínimo f2; precio `$2.669` f3 [0,6] | **COLGATE ULTRA BLANCO 90G**, 0,7889; 90 g | Monto/nombre/tamaño válidos → identidad pending por términos genéricos extra y score < 0,80. Hace falta alias de categoría validado; además moneda sin resolver activa `currency_not_comparable`. Conservar Ultra blanco y 90 g; no equiparar a Total carbón. |
| P2 b0: DOVE Acondicionador reconst.+minoácido x200ml; unit, 2999 | Cita exacta usa `minoácido` en Unicode descompuesto; `$2.999` [87,93]; 200 ml | **DOVE ACONDICIONADOR RECONSTRUCCION 200ML**, 0,8143; segundo Reparación 0,7771 | Lector conservó la variante → identity pending: abreviatura y compuesto no equivalen a texto catalogado; margen 0,0372. Buen candidato, pero requiere validar alias/familia o decisión humana; no eliminar “minoácido” silenciosamente. |

Controles que deben quedar pendientes o rechazados:

- CIF detergente 300 ml → LIMÓN 300 ml 0,8332, BIOACTIVE 300 ml 0,7898. El folleto omite variante. El alto score no resuelve esa ambigüedad.
- AYUDIN Original 2 L → Lavanda 2 L 0,7684. Debe vetarse esa variante cuando se agregue la regla; no aceptar el primer candidato actual.
- ALMA MORA Malbec 750 ml → Blanco dulce Low 750 ml 0,6561; falta veto de cepa/variante. Catálogo también contiene VINO ALMA MORA 750ML genérico: eso no acredita Malbec.
- HEINEKEN lata 710 cc → HEINEKEN 710CM 0,5424. `CM` en catálogo no prueba ml; pack sin cantidad no se divide. Corregir datos del catálogo con trazabilidad, no un alias global cm→ml.
- SKIP líquido 3 L → líquido 800 ml es `presentation_conflict`; el replay conserva como candidato alternativo polvo 600 por ausencia de veto de categoría. Ninguno es identidad aceptable.
- AZUCEL azúcar común 1 kg, $1.099: nombre/importe válidos, sin candidato >= 0,45. No afirmar fallo de reconocimiento ni dar de alta un producto automáticamente.

## Plan de implementación posterior y ownership

No se autorizó ni ejecutó implementación en esta investigación. El principal debe aprobar contratos y secuencia antes de repartir trabajos con archivos exclusivos.

Para integrar ambos casos, el coordinador conserva ownership de contratos, policy compartida, presupuesto, identidad, matching y UI. El trabajador futuro del caso A debe editar exclusivamente lector OpenAI, ensamblador estructurado, prompt y pruebas específicas de A; el ensamblador está hoy en el archivo compartido `document_analysis.py`, por lo que su edición se programa secuencialmente o se extrae primero bajo integración del coordinador. Este informe describe paquetes de trabajo, no autoriza cambios simultáneos en archivos compartidos.

| Orden | Responsable y archivos | Trabajo y aceptación |
|---|---|---|
| 0 | Principal: contratos, especificación y fixtures | Acordar estados independientes de lectura, identidad, monto, base y recomendación. Congelar snapshot tenant y hash/versiones; anotar que no es catálogo histórico. Definir qué alias/equivalencias se consideran confiables. |
| 1 | Backend de evidencia: `document_analysis.py`, `decision_policy.py` | Validación por campo, corregir L'OREAL/unidad y ocurrencias repetidas. Citas válidas sobreviven; citas falsas y monedas/roles ambiguos no activan compras. Policy devuelve causas por etapa. |
| 2 | Identidad/catálogo: `identity.py`, `normalization.py`, `matching.py` | Identidad canónica independiente de orden; filtros de categoría/grado/variante; exactos compatibles antes de fuzzy. AYUDIN exacto por encima de Lavanda; FAVORITA 000 no compite como equivalente con 0000. Mantener tenant, active, contenidos, packs y presentaciones. |
| 3 | Lectura/orquestación: `openai.py`, prompt estructurado, límites de scheduler | Reducir duplicación de salida manteniendo líneas, roles, variantes, condiciones y citas. Separar timeouts y presupuestos, decidir incrementalmente, cerrar transporte seguro. Coordinar cambios en `document_analysis.py` con el paso 1; no edición simultánea de ese archivo. |
| 4 | Observabilidad/UI: contratos, endpoints y `app.js` | Mostrar páginas con evidencia/total y falladas/no enviadas; separar faltas de monto/base/identidad. Audit determinístico incluso sin JEV: candidato, score, veto, regla de aceptación, input/output policy y transición. Revisión tras cada fase con fixtures offline. |

Contratos compartidos propuestos: `EvidenceOccurrence` (fragment_id, start, end, literal, semantic_role); `IdentityAssessment` (canonical_attributes, candidate_id, retrieval_score, conflicts, rule_version); estados de importe/base separados y `AnalysisProgress` por fase. Versionar wire/prompt/policy y preservar datos originales; no introducir campos obligatorios que invaliden lecturas históricas. No agregar migraciones ni infraestructura de colas sin elegir previamente persistencia/ejecución.

Alternativas de ejecución para decidir con evidencia: (A) request limitado, chunks de una página o menor volumen y salida compacta con decisiones incrementales; (B) procesamiento reanudable con checkpoint por página/fase cuando el documento no entra en el presupuesto. Aumentar timeout puede recuperar cobertura, pero no arregla validación ni ranking. Cambiar de modelo no es la primera corrección. Gemini permanece suspendido.

## Criterios para aceptación confiable

La identidad puede confirmarse aunque la base del precio siga pendiente; comprar requiere ambos controles y moneda comparable. Confirmar identidad sólo con evidencia propia y una equivalencia aprobada: marca, categoría, tamaño físico, variante explícita y contenido compatibles; candidato único en esa equivalencia; tenant correcto y activo. Separar ausencia de variante de variante contradictoria. Los alias no pueden borrar sabor, cepa, grado, género, fórmula, contenido contado o tamaño.

| Identidad | Base comercial | Resultado permitido |
|---|---|---|
| Confirmada | Pendiente | Producto vinculado; revisión de base; sin comparación comercial |
| Pendiente | Confirmada | Importe/base conservados; revisión de identidad; sin recomendación de compra |
| Confirmada | Confirmada | Comparar sólo con monto válido, moneda comparable y cantidades verificadas |
| Pendiente | Pendiente | Conservar evidencia y candidatos; causas separadas |
| Sin coincidencia validada | Cualquier estado | Conservar oferta sin inventar producto ni relación |

Para monto/base: cita de ocurrencia actual completa, moneda respaldada, base explícita con alcance y cantidades comerciales propias; nunca mínimos ni contenido del envase para dividir. Repetición del mismo importe permite recuperación sólo si los roles/ocurrencias acreditan el precio actual. XKG/XLT aporta un control aritmético auxiliar y no reemplaza base. No aceptar “por pack” cuando pertenece a “llevando por pack”. Feedback humano conserva provenance y no convierte una alternativa en identidad exacta.

Evaluar por separado precisión de identidad, recuperación de candidatos, monto, base, cobertura y latencia. No usar porcentaje de autovínculo como objetivo único. Las probabilidades JEV no son garantía calibrada: mantener acceptance deshabilitada hasta validar fixtures y conjunto independiente.

## Regresiones offline necesarias

Usar estos datos reales guardados como fixtures de entrada y esperados, sin red ni DB. Las pruebas existentes relevantes son `test_document_decision_policy.py`, `test_automatic_identity.py`, `test_analysis_candidate_feedback.py`, `test_analysis_recovery.py`, `test_openai_document_extraction_v2.py` y `analysis_ui.test.cjs`; no se ejecutaron suites en esta investigación.

1. NATURA aceite: una cita fiscal inválida no borra nombre/precio válidos; scope/tenant inválidos no vinculan.
2. L'OREAL $8.499 válido, junto con controles 1 L, 1 kg, 10 %, mínimo 3, importe negativo y precio anterior.
3. FAVORITA $749 repetido y L EAU VIVE `$1.729`/`$1.729,00`: ocurrencias completas; no fusionar roles ni asumir la primera. 000 ≠ 0000.
4. AYUDIN Original 2 L con orden de palabras distinto: exacto recuperable; Lavanda, 1 L y Pureza glaciar no sustituyen la variante.
5. NATURA sachet, COLGATE Ultra blanco y DOVE: alias aprobados frente a variantes adversarias; no usar una expectativa de aceptación sin adjudicación del dato ambiguo.
6. Multilínea COLGATE: preservar nombre, referencia XKG, mínimo y precio como roles diferentes; moneda `$` no se vuelve ARS sin evidencia de documento/configuración explícita.
7. SEDAL shampoo/acondicionador, REXONA/DOVE MEN y TRESEMME 750/880 ml: no colapsar opciones ni dividir packs a partir del contenido.
8. HEINEKEN llevando por pack y NATURA por display: cantidad ausente sigue pendiente; XKG/XLT no fabrica envases.
9. Fake reader con reloj/transportes lentos: deadline impide nuevos envíos, decisiones reciben su cuota, no se multiplican SDKs ni se borran temporales en uso; registrar draining/cancelación real y duración efectiva.
10. Resumen/UI: distinguir 4/24 páginas con evidencia, 49 fichas, 6 bloques sin ficha, candidatos sugeridos, identidades vinculadas y base pendiente. Mantener todos los controles multi-tenant y productos inactivos.

## Verificación realizada y archivos escritos

Se ejecutó `.venv/Scripts/python.exe output/diagnostics/investigar_openai_vital/replay.py`. El script usa sólo JSON y un repositorio fake que filtra `business_id`; llama assembler, recuperación y policy de producción sin persistir. Resultado: 55 interpretaciones ensambladas, 31 `needs_jev`, 24 `needs_human_review`; coincide con 31 `deadline_exhausted` y 24 `no_identity_candidates` guardados. Ningún candidato recibió `auto_accepted_identity_v1`. Se verificó el hash de policy y las citas/offsets de los cinco ejemplos.

Archivos creados: este plan; `output/diagnostics/investigar_openai_vital/replay.py`; `output/diagnostics/investigar_openai_vital/offline-stage-replay.json`. No se cambiaron runtime, tests, settings ni datos persistidos. No se instalaron dependencias ni se llamó OpenAI/JEV/Gemini. El primer intento del replay necesitó corregir su import path; la ejecución posterior completó.

Limitaciones: la exactitud visual y cobertura interna de páginas 1–4 no se verificó contra el PDF original; no puede inferirse cuántas ofertas faltan en páginas no leídas. El snapshot actual no permite probar listas internas históricas. No se midió una nueva latencia, ni costo total de requests con timeout, ni resultado de una implementación futura. El gasto conocido de tokens es parcial: 22699 de salida y 23200 de entrada en llamadas exitosas; usage global permanece null porque las fallidas no informan consumo.

Fuentes oficiales consultadas: [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [timeouts del SDK Python oficial](https://github.com/openai/openai-python#timeouts) y [latency optimization](https://developers.openai.com/api/docs/guides/latency-optimization). Structured Outputs garantiza estructura del schema, no equivalencia comercial ni verdad de citas; minimizar salida puede reducir latencia, manteniendo las evidencias requeridas. Las atribuciones causales específicas de esta corrida proceden del código y snapshots locales.
