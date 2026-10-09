# Handoff: selector y resultados de los dos métodos

Fecha: 6 de octubre de 2026. Responsable: agente frontend.
Estado: análisis del código y contrato propuesto; no se modificó el runtime.

## Decisiones acordadas con el integrador

- Mantener `app/static/`: HTML, JavaScript y CSS existentes, sin dependencias ni otra app.
- Ruta de capacidad: `GET /procurement/supplier-offers/analysis-methods`.
- Un solo selector compartido para los formularios de uno y dos PDF.
- Capturar método, negocio, archivos y nombres antes del primer `await`.
- B solo puede habilitarse cuando la implementación está integrada y sus proveedores están configurados. Una clave JEV por sí sola no habilita B.
- En la fase preparatoria B continúa deshabilitado. Los contratos siguientes son propuestas para implementación, no capacidades presentes.
- El integrador conserva ownership de `schemas.py`, `main.py`, `settings.py`, `dual.py`, `supplier_offers.py`, `matching.py` y pruebas API compartidas.

Se aplicó `stock-ai-architecture`, leyendo `AGENTS.md`, `docs/PLAN_ACCION_TECNICO.md`, `docs/CODEX_AGENTES_Y_SKILLS.md` y `docs/PLAN_DOS_METODOS_EXTRACCION.md`. Los avisos antiguos sobre inexistencia de frontend/backend no describen el código actual; esta entrega revisa la interfaz ya existente y respeta la solicitud de planificar su modificación.

## Presente versus faltante, comprobado en archivos

| Código existente | Implicación para la entrega |
|---|---|
| `index.html` ofrece Un PDF / Dos PDF y dos formularios | Esto elige proveedores; no métodos A/B. Agregar el selector fuera de ambos formularios. |
| `analyzePdf(file, supplierName)` envía `business_id`, `supplier_name`, `extraction_provider=auto`, `persist_candidates=true` | Falta `analysis_method`; el proveedor legacy debe quedar separado. |
| El endpoint solo tiene `extraction_provider` | No hay selección A/B pública ni capacidad de disponibilidad. |
| La respuesta contiene `import_result`, `comparison`, `extraction_warnings` | Agregar campos opcionales compatibles, sin renombrar los existentes. |
| Los dos PDF se envían secuencialmente | Mantener esta secuencia. Hoy el segundo archivo y el tenant se vuelven a leer durante la operación; pueden cambiar. |
| El botón deshabilitado pertenece solo al formulario enviado | Otro formulario puede iniciar una operación concurrente. Usar bloqueo compartido de análisis. |
| `summarizeAnalysis()` cuenta ofertas/candidatos y consulta metadata | No representa bloques sin precio. Sus métricas detalladas solo aparecen para `dual_independent_chunks`, no para el lector único actual. |
| El formulario único decide Listo/Revisar solo por `needs_review_pages` | Pendientes de clasificación, precio o identidad sin páginas pendientes necesitan otro estado visible. |
| El formulario doble siempre termina Listo y con mensaje de éxito | Debe reconocer resultados parciales de cualquiera de los dos análisis. |
| `bestOffersByProduct()` descarta candidatos sin producto/precio comparable y conserva el menor por producto | No usar ese mapa para contar ni mostrar todas las ofertas: varias quedan fuera por diseño. |
| El historial cuenta `pending_count` de coincidencias | No representa bloques pendientes de lectura/interpretación. Mantener estos conteos separados. |
| `SupplierOfferDocument.metadata` viaja en `/summaries` | Hay un camino existente para resumen histórico; no inventar método cuando no está guardado. |
| `openDocument()` y `renderDocuments()` reescriben `detailSubtitle` | Ambos deben usar el mismo formateador histórico o uno pisa el método mostrado por el otro. |
| `escapeHtml()` escapa texto y atributos; `textContent` se usa para varios avisos | Reutilizar esta protección para nuevos bloques, citas, motivos y etiquetas. |
| `readerAgreementHtml()` imprime “Coincidencia Gemini/OpenAI” incluso con `single_source` | Conservar esa descripción solo para documentos con evidencia de lectura dual histórica; mostrar lector único correctamente. |
| `friendlyError()` todavía recomienda Gemini en algunos errores | Actualizar esa copia al tocar UI: Gemini sigue suspendido. |
| `tests/test_procurement_api.py` usa lectores falsos y SQLite en memoria | Las pruebas nuevas pueden verificar contratos/persistencia sin API paga ni DB del negocio. |

Hay una prueba existente que devuelve HTTP 200 con cero ofertas y `needs_review_pages=[1]` al guardar un PDF fallido para revisión. No interpretar HTTP 200 ni `items.length === 0` como análisis completo. El campo de documento `extraction_status` actual tampoco reemplaza los estados independientes de interpretación e identidad.

## Contrato público propuesto

### Disponibilidad antes del envío

`GET /procurement/supplier-offers/analysis-methods?business_id=<tenant>` devuelve estado de configuración e integración, sin llamar proveedores. Usar el mismo contexto y reglas de acceso del módulo. No devolver claves, valores de entorno ni errores externos crudos.

```json
{
  "contract_version": "supplier_offer_analysis_v1",
  "default_method": "openai_structured",
  "methods": [
    {
      "id": "openai_structured",
      "available": true,
      "unavailable_reason_code": null,
      "unavailable_reason": null
    },
    {
      "id": "openai_blocks_jev",
      "available": false,
      "unavailable_reason_code": "method_not_integrated",
      "unavailable_reason": "Este método todavía no está disponible."
    }
  ]
}
```

La interfaz tiene una lista cerrada de IDs y etiquetas españolas; no convierte un ID desconocido del servidor en método ejecutable. `available` significa que el backend puede iniciar la ruta, no que una llamada futura tendrá éxito. A requiere OpenAI configurado e implementación lista. B requiere OpenAI, JEV e integración lista. Durante despliegue preparatorio puede responder A disponible y B no integrado; sin OpenAI ambos quedan indisponibles. Códigos adicionales: `openai_not_configured`, `jev_not_configured`, `method_disabled`.

Declarar esta ruta estática antes de futuros GET dinámicos de documento. La consulta debe funcionar aunque B no esté integrado, sin importar módulos opcionales incompletos. No sondear APIs pagas para verificar disponibilidad. Al cargar UI y cambiar tenant, consultar la capacidad. Si la consulta falla, mostrar “No pudimos consultar los métodos disponibles” y acción Reintentar; no habilitar B por defecto. En una UI nueva que depende de este contrato, bloquear nuevos envíos hasta tener una respuesta válida. El backend sigue siendo la autoridad si la capacidad quedó desactualizada.

### Envío

Mantener `POST /procurement/supplier-offers/from-document`, añadiendo:

```text
analysis_method: openai_structured | openai_blocks_jev
default: openai_structured
extraction_provider: auto (legacy; separado de analysis_method)
```

El backend valida método y disponibilidad antes de invocar OpenAI. Método desconocido devuelve 422 con mensaje claro; método conocido indisponible devuelve 503. Gemini continúa rechazado. No sustituir B por A en frontend ni backend. Mantener `detail` como texto español para compatibilidad con `requestJson()`; un eventual código de error puede agregarse como campo, sin convertir `detail` en objeto que la UI actual muestre como `[object Object]`.

### Resultado compatible

Agregar `analysis` y `pending_blocks` a la respuesta manteniendo los tres campos actuales. El resumen se calcula en backend; los conteos de ofertas usan IDs únicos, no cantidad de candidatos. Forma propuesta:

```json
{
  "import_result": { "document": "contrato actual", "items": [] },
  "comparison": { "report": "contrato actual", "persisted_count": 0 },
  "extraction_warnings": [],
  "analysis": {
    "contract_version": "supplier_offer_analysis_v1",
    "run_id": "backend-generated-id",
    "method": "openai_blocks_jev",
    "status": "partial",
    "page_count": 1,
    "observed_block_count": 1,
    "interpreted_offer_count": 0,
    "pending_block_count": 1,
    "unlinked_offer_count": 0,
    "identity_review_offer_count": 0,
    "price_review_offer_count": 0,
    "review_page_numbers": [],
    "duration_seconds": 5.1,
    "usage": null,
    "cost": null
  },
  "pending_blocks": [
    {
      "block_id": "run-id:p1:attempt1:b1",
      "page_number": 1,
      "order_on_page": 1,
      "text": "Aceite 900 ml — precio ilegible",
      "reason_codes": ["missing_current_price"],
      "reason": "Falta confirmar el precio actual."
    }
  ]
}
```

Los marcadores de contratos actuales en el ejemplo son abreviaciones explicativas; no reemplazan sus objetos reales. `method` describe lo efectivamente ejecutado. `status` describe interpretación (`completed` o `partial`); la revisión de identidad/precio se expresa además en sus conteos. Se pueden completar todas las lecturas y quedar ofertas sin vínculo. Un fallo sin resultado salvable produce error HTTP; un resultado guardado incompleto se presenta como parcial. Duración/usage/costo desconocidos deben ser `null`, no cero. Conteos de revisión pueden solaparse: no sumarlos como si fueran grupos exclusivos.

Guardar el mismo resumen bajo `document.metadata.analysis`; conservar la evidencia completa y pendientes en el ledger de documento definido por el integrador. Derivar la respuesta pública de esa misma fuente para evitar que POST e historial cuenten distinto. Un bloque puede producir varias ofertas; no asumir `bloques = ofertas + pendientes`.

### Historial y detalle persistente

Para primera entrega pequeña puede aprovecharse `/summaries`, que hoy incluye metadata del documento. Para evitar enviar todos los textos de todos los documentos en cada refresh, recomiendo una proyección pequeña `analysis` en `SupplierOfferDocumentSummary` y una lectura de detalle:

`GET /procurement/supplier-offers/{document_id}/analysis?business_id=<tenant>`

Respuesta: `document_id`, `analysis` nullable y `pending_blocks`. Solo lectura del registro guardado, sin ejecutar IA. Validar pertenencia al negocio; documento ajeno/inexistente devuelve 404. Este endpoint resuelve enlaces directos y documentos fuera de los últimos 100 del historial. Si el integrador elige usar metadata por ahora, documentar el límite de tamaño y conservar la opción de consultar detalle por ID; no desaparecer pendientes cuando el documento no está en la lista cargada.

Documentos anteriores sin resumen: `analysis=null`, mostrar “Método no registrado”. No inferir A de `extraction_provider=openai`, ni de `auto`, ni del selector vigente. Un documento `dual_independent_chunks` conserva su información histórica dual. Un precio corregido no cambia la etiqueta del método de lectura original.

## Implementación UI concreta

1. En `index.html`, debajo de Un PDF / Dos PDF, añadir label explícito y `select#analysisMethod` con A/B; ayuda `#analysisMethodHelp` con `aria-describedby` y disponibilidad `role=status`. Mantener selección A por defecto si está disponible. La opción B aparece deshabilitada con motivo visible fuera del `option`, ya que las opciones deshabilitadas no siempre se anuncian bien. No afirmar ahorro/velocidad.
2. Etiquetas: A “OpenAI separa los datos”; ayuda “Lee y organiza la oferta; JEV ayuda con las dudas.” B “OpenAI lee y JEV organiza”; ayuda “Lee los textos de cada oferta y JEV los clasifica.” Publicar la descripción de JEV selectivo en A únicamente al estar integrado; mientras se prepara, describir solo la lectura OpenAI existente.
3. Añadir estado global `analysisBusy`. Antes del primer `await`, capturar selección, tenant, archivos y nombres como objeto de operación. Validar todos los PDF/nombres del formulario doble antes de iniciar el primero. Cambiar `analyzePdf` para recibir el contexto explícito; no consultar controles globales dentro de cada envío.
4. Mientras está activo, deshabilitar ambos botones de envío, selector, cambio de modo, nombres/archivos y selector de negocio. Restaurarlos en `finally` según disponibilidad vigente, no habilitar indiscriminadamente B. Mantener snapshot aunque el DOM se modifique programáticamente. Congelar tenant también evita cargar el documento de otro negocio al finalizar.
5. Dos PDF continúan secuenciales y usan el mismo método. Guardar el ID del primer resultado en el contexto. Si el segundo falla, mostrar enlace concreto al primero guardado; no repetir automáticamente el primer POST. No correr los dos métodos por un archivo sin elección del usuario.
6. Usar el método recibido para resultados. Estados: Analizando, Guardado / Revisar, Parcial y Error. Las revisiones se muestran con texto; el color es complementario. Si alguno de los dos resultados es parcial, el estado conjunto lo expresa. El texto “Comparación lista” no debe ocultar bloques pendientes.
7. Añadir panel “Ofertas que necesitan datos” al detalle, con página, texto original y motivo. Aparece incluso sin ofertas/candidatos, fuera de filtros de matching y del mapa de mejores precios. Sin acción de editar implementada, mostrar lectura y motivo; no crear botones ficticios.
8. En comparación doble, sumar métricas separadas por proveedor: vinculadas, sin vínculo, revisión de base del precio y bloques pendientes. Ofrecer acceso a esos bloques; no limitarse al contador `bestOffersByProduct().needsReview`. Esa función puede seguir alimentando precios comparables, pero nunca el registro completo. No anunciar “Sin oferta” si el proveedor tiene evidencia pendiente para ese producto sin una asociación validada; aclarar que la tabla incluye solo precios comparables.
9. Historial: método persistido bajo el nombre de archivo y conteo de bloques pendiente distinto del de coincidencias. `formatDocumentAnalysis()` centraliza texto para `documentTemplate`, `renderDocuments`, `openDocument` y resumen final. Si no hay datos registrados, no adivinar.
10. Nuevos textos del documento con `textContent` siempre que se pueda. Si se usan templates de HTML, aplicar `escapeHtml()` a texto, motivos, método desconocido y IDs de atributos; URLs locales de detalle con `encodeURIComponent()`. Nunca usar un fragmento como HTML ni tomar del documento un href arbitrario. CSS `white-space: pre-wrap` preserva el texto visible; el ledger mantiene el texto íntegro.

Ajustes CSS mínimos: contenedor de selector/ayuda consistente con `.surface`, estados de aviso accesibles, panel de pendientes y adaptación móvil existente. No rehacer tarjetas ni adoptar Next.js. Se acepta la verificación de teclado, 320 px de ancho y textos largos, sin truncar evidencia permanentemente.

## Pruebas concretas sin llamadas pagas

### API/persistencia, ownership del integrador

- Capacidad anuncia A/B según integración/configuración y no llama factories que inicien servicios externos. B con clave pero implementación incompleta sigue indisponible.
- Default legacy ejecuta A; ID desconocido/B no disponible/Gemini suspendido no invocan OpenAI. Precedencia de validaciones definida y estable.
- Respuesta A y B falsas conserva los campos existentes, método real, run ID y mismo esquema de summary. Pendientes sin precio sobreviven POST, DB de fixture y GET histórico sin importar el método.
- Cero ofertas con bloques o páginas pendientes permanece guardado y parcial; cero ofertas sin evidencia conserva error explícito actual según política del integrador.
- JEV cae después de la lectura B: evidencia conservada, método B y resultado parcial; no se llama A como reemplazo oculto.
- Documento histórico sin `analysis` devuelve null, no default A. Métodos/metadata quedan asociados al documento al cambiar selección o corregir matching.
- GET de detalle valida tenant y devuelve 404 para documento ajeno. Lectura de historial no ejecuta IA. Contrastar summary y ledger para que los conteos coincidan.

### UI con respuestas simuladas y verificación en navegador

- A/B seleccionados aparecen en FormData; `extraction_provider=auto` continúa separado. B indisponible se anuncia y no produce POST. Fallo de capacidades ofrece Reintentar sin asumir disponibilidad.
- Operación doble con promesas controladas: cambiar método/tenant/archivo tras el primer envío no cambia el segundo payload. Ambos archivos se validan antes de toda llamada. Segundo fallo conserva link al primer documento e impide rerun silencioso.
- Submit de ambos formularios durante una ejecución produce una sola operación; controles se restauran tras error. Sin tokens consumidos por capacidad ni cambio de selector.
- API devuelve método distinto/ausente: UI usa resultado o “Método no registrado”, no la selección. Documento viejo sin metadata se mantiene sin método inventado. Documento dual histórico conserva descripción dual; single OpenAI no se presenta como Gemini/OpenAI.
- Resultado solo con pendientes se ve en detalle aunque candidatos y precios estén vacíos. Resultado parcial con ofertas utilizables no obtiene estado completo. Pendientes no desaparecen al filtrar coincidencias ni comparar dos proveedores.
- Una oferta con varios candidatos cuenta una vez; cantidades de pendientes de matching y bloques permanecen separadas; no sumar conteos superpuestos como total único.
- Texto de bloque `<img src=x onerror=alert(1)>`, motivo con comillas y proveedor con `<script>` se renderizan como texto y no crean nodos ejecutables. IDs de documentos se codifican al formar enlaces.
- Teclado recorre label/select/archivos/submit, motivo de B se anuncia, `role=status` comunica estados y foco visible permanece. Pantalla móvil y fragmentos largos no rompen el formulario.

Preparar fixtures API/DOM sin instalar dependencias. La verificación en navegador utiliza respuestas falsas o backend de fixtures; no hace importaciones pagas. `node --check` puede verificar sintaxis cuando exista runtime disponible, pero no reemplaza pruebas de snapshots, estados ni HTML seguro. Esta entrega no ejecutó tests de runtime porque solo agregó documentación.

## Secuencia de entrega y cierre

Primero el integrador fija schemas de capacidad, summary y ledger. Luego publica capacidades con B deshabilitado, integra ambos métodos y persistencia, y habilita B solo al pasar sus pruebas. Frontend puede preparar markup/fixtures en paralelo, pero no ofrece una ruta operativa ficticia. El agente UI modifica exclusivamente `app/static/index.html`, `app/static/app.js`, `app/static/styles.css` y pruebas UI acordadas; no toca contratos compartidos ni endpoint. QA revisa el flujo completo después de integración.

Cierre de UI: método seleccionado viaja correctamente en uno/dos PDF, método real se muestra en resultados/historia, indisponibilidad es honesta, parciales y bloques sin precio quedan visibles, tenant no cambia a mitad de operación y no se ejecuta texto del documento como HTML. Ahorro, calidad y tiempo requieren evaluación real aparte; la interfaz no promete resultados de esa medición.

Archivo cambiado en esta entrega: `docs/agents/FRONTEND_DOS_METODOS_HANDOFF.md`.
