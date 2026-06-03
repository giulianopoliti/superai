# Plan de acción técnico - Stock AI

## A. Resumen del producto

Stock AI es un asistente operativo para supermercados. Su objetivo es ayudar al equipo del negocio a recordar tareas, vencimientos, reglas de proveedores, precios, márgenes y decisiones operativas frecuentes usando lenguaje natural.

El primer canal previsto es WhatsApp mediante Kapso, pero el producto no debe construirse como "un bot de WhatsApp". La base correcta es un Assistant Core agnóstico al canal, capaz de recibir mensajes desde WhatsApp, web, desktop, CLI u otros adapters futuros sin cambiar la lógica principal.

El problema que resuelve es ordenar conocimiento operativo disperso, reducir olvidos, acelerar consultas comerciales y transformar mensajes cotidianos en acciones estructuradas y trazables.

## B. Principios de arquitectura

- El asistente debe ser agnóstico al canal.
- WhatsApp/Kapso debe ser un adapter, no el core.
- La IA interpreta intención y extrae datos, pero el backend ejecuta acciones.
- El sistema debe pedir confirmación cuando falten datos o haya baja confianza.
- Las reglas de negocio deben estar en servicios propios, no dentro del prompt.
- Los prompts deben estar versionados como archivos.
- Las integraciones externas deben estar abstraídas por interfaces/providers.
- Diseñar pensando en web/desktop futuro, pero sin construirlo ahora.
- El backend debe ser la fuente de verdad para validaciones, persistencia, permisos y decisiones.
- El sistema debe soportar múltiples negocios y usuarios desde el inicio, aunque el MVP use un solo negocio.
- La conversación debe quedar registrada para auditoría, debugging y mejora del asistente.

## C. Arquitectura propuesta

### Estado actual del repo

El repo inspeccionado en `C:\Users\54116\Downloads\stock-ai` está vacío:

- No existe backend.
- No existe frontend.
- No hay estructura Python, TypeScript ni monorepo.
- No existen carpetas `app`, `src`, `api` ni `docs` antes de este documento.
- No hay archivos de configuración existentes.
- No hay `.git` detectado.
- No hay convenciones previas que respetar.

Por este motivo, conviene iniciar con una estructura simple de backend Python/FastAPI. El nombre sugerido para la raíz técnica puede ser `stock-ai-api/` si se decide separar el backend en una carpeta propia. Como el repo actual parece dedicado al proyecto completo, también sería válido poner `app/` en la raíz. La recomendación inicial es mantener repo único con backend en raíz hasta que exista una razón real para monorepo.

### Estructura inicial recomendada

```txt
stock-ai/
│
├── app/
│   ├── main.py
│   ├── assistant/
│   │   ├── engine.py
│   │   ├── intent_router.py
│   │   ├── context_builder.py
│   │   ├── confirmation.py
│   │   └── prompts/
│   │       ├── system.md
│   │       ├── intent_classifier.md
│   │       ├── reminder_parser.md
│   │       ├── expiration_parser.md
│   │       ├── supplier_note_parser.md
│   │       └── price_parser.md
│   ├── channels/
│   │   ├── base.py
│   │   ├── whatsapp_kapso.py
│   │   ├── web.py
│   │   └── cli.py
│   ├── modules/
│   │   ├── reminders/
│   │   │   ├── service.py
│   │   │   ├── repository.py
│   │   │   └── schemas.py
│   │   ├── expirations/
│   │   ├── suppliers/
│   │   ├── products/
│   │   └── margins/
│   ├── providers/
│   │   ├── llm/
│   │   │   ├── base.py
│   │   │   └── openai_provider.py
│   │   ├── speech/
│   │   ├── notifications/
│   │   ├── storage/
│   │   └── scheduler/
│   ├── db/
│   │   ├── models.py
│   │   ├── session.py
│   │   └── repositories/
│   ├── schemas/
│   │   ├── assistant.py
│   │   ├── messages.py
│   │   └── actions.py
│   └── settings.py
│
├── tests/
│   ├── assistant/
│   ├── modules/
│   └── channels/
│
├── docs/
│   └── PLAN_ACCION_TECNICO.md
│
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

### Flujo conceptual

```txt
WhatsApp / Web / Desktop / CLI
            ↓
      Channel Adapter
            ↓
      Assistant Core
            ↓
 Tools / DB / IA / Scheduler
```

El adapter traduce formatos externos. El core entiende requests normalizados. Los módulos de negocio ejecutan acciones. Los providers aíslan servicios externos.

### Convención de idioma

Conviene usar inglés para nombres internos de código, tablas, columnas, carpetas, clases, intents y APIs. Ejemplos: `expiration_items`, `received_at`, `expires_at`, `create_reminder`, `supplier_products`.

Conviene usar castellano para la experiencia de usuario, respuestas del asistente, paneles, labels, mensajes de confirmación y documentación operativa del negocio.

Motivo:

- Inglés facilita trabajar con librerías, documentación técnica, herramientas de IA, ORMs, SDKs y convenciones globales.
- Castellano mantiene el producto natural para supermercados argentinos.
- La capa de presentación puede traducir `expiration_items` a "vencimientos" o "partidas con vencimiento" sin contaminar el modelo interno.
- Los prompts pueden estar en castellano cuando interpretan mensajes reales de usuarios argentinos, aunque el JSON estructurado devuelto use claves en inglés.

## D. Contrato del Assistant Core

El Assistant Core debe operar con modelos propios y no con payloads específicos de Kapso, WhatsApp, una web o una app desktop.

### AssistantRequest

Representa cualquier mensaje entrante, sin importar el canal.

Campos sugeridos:

- `channel`: canal de origen normalizado, por ejemplo `whatsapp`, `web`, `desktop`, `cli`.
- `external_user_id`: identificador del usuario en el canal externo.
- `business_id`: identificador del negocio al que pertenece el mensaje.
- `message_type`: tipo de mensaje, por ejemplo `text`, `audio`, `image`, `pdf`.
- `text`: texto normalizado disponible para el core. En audio, sería el resultado de transcripción.
- `attachments`: lista de adjuntos normalizados con tipo, URL, metadata y proveedor de storage.
- `timestamp`: fecha/hora del evento recibido.
- `raw_payload`: payload original para auditoría y debugging, sin que el core dependa de su estructura.

Ejemplo conceptual:

```json
{
  "channel": "whatsapp",
  "external_user_id": "5491112345678",
  "business_id": "business_123",
  "message_type": "text",
  "text": "Recordame mañana revisar los yogures que vencen",
  "attachments": [],
  "timestamp": "2026-05-28T10:30:00Z",
  "raw_payload": {}
}
```

### AssistantResponse

Representa una respuesta normalizada del asistente.

Campos sugeridos:

- `reply`: texto a responder al usuario.
- `actions`: acciones ejecutadas o propuestas por el backend.
- `requires_confirmation`: indica si se necesita confirmación humana antes de ejecutar o guardar.
- `confirmation_payload`: datos estructurados que se pedirán confirmar.
- `metadata`: información auxiliar para trazabilidad, métricas o debugging.

Ejemplo conceptual:

```json
{
  "reply": "Te preparo el recordatorio para mañana. ¿Confirmás que querés revisar los yogures?",
  "actions": [],
  "requires_confirmation": true,
  "confirmation_payload": {
    "intent": "create_reminder",
    "title": "Revisar yogures que vencen",
    "due_at": "2026-05-29"
  },
  "metadata": {
    "confidence": 0.78
  }
}
```

Kapso/WhatsApp solo debe transformar payloads externos hacia `AssistantRequest` y transformar `AssistantResponse` hacia mensajes de WhatsApp. No debe decidir intenciones, guardar recordatorios ni contener reglas del negocio.

## E. Intenciones iniciales

Intenciones del MVP:

- `create_reminder`
- `list_reminders`
- `mark_reminder_done`
- `save_expiration`
- `list_expirations`
- `save_supplier_note`
- `get_supplier_info`
- `save_product_price`
- `compare_product_prices`
- `suggest_sale_price`
- `unknown`

La IA debe devolver JSON estructurado con:

- `intent`: intención detectada.
- `entities`: datos detectados, por ejemplo fecha, producto, proveedor, precio, familia, cantidad.
- `confidence`: confianza del modelo.
- `requires_clarification`: si falta información o hay ambigüedad.
- `clarification_question`: pregunta sugerida para completar datos.
- `reasoning_summary`: resumen breve, no sensible, para debugging interno.

Ejemplo conceptual:

```json
{
  "intent": "save_product_price",
  "entities": {
    "product_name": "Coca Cola 2.25L",
    "supplier_name": "Distribuidora Norte",
    "cost_price": 1800,
    "currency": "ARS"
  },
  "confidence": 0.91,
  "requires_clarification": false,
  "clarification_question": null
}
```

El backend debe validar este JSON con Pydantic antes de ejecutar cualquier acción.

## F. Módulos del MVP

### Sprint 1 - Núcleo sin WhatsApp

Objetivo:

- Crear endpoint genérico `/assistant/message`.
- Crear `AssistantRequest`.
- Crear `AssistantResponse`.
- Crear `AssistantEngine`.
- Crear `IntentRouter`.
- Crear `ReminderService` inicial.
- Guardar conversación y recordatorios.
- Probar desde CLI/Postman.

Resultado esperado:

- El asistente puede recibir texto normalizado por HTTP o CLI.
- El core no depende de WhatsApp.
- Las intenciones pueden estar mockeadas al principio para validar arquitectura.

### Sprint 2 - Adapter Kapso/WhatsApp

Objetivo:

- Crear endpoint `/webhooks/kapso`.
- Parsear payload de Kapso.
- Transformar payload externo a `AssistantRequest`.
- Enviar respuesta por Kapso.
- Mantener Kapso aislado del core.

Resultado esperado:

- WhatsApp funciona como un canal más.
- El core sigue sin conocer estructuras específicas de Kapso.

### Sprint 3 - Vencimientos

Objetivo:

- Guardar partidas de productos con cantidad, fecha de ingreso y fecha de vencimiento.
- Consultar vencimientos.
- Avisar X días antes.
- Marcar como ofertado, vendido o descartado.
- Permitir múltiples vencimientos para el mismo producto.
- Guardar lote como dato opcional, no obligatorio.

Resultado esperado:

- El sistema puede registrar que un mismo producto tiene cantidades distintas con vencimientos distintos. Por ejemplo: entraron 10 galletitas el 2026-05-28, 4 vencen en 10 días y 6 vencen en 4 meses.
- Las alertas se calculan por partida/grupo de vencimiento, no por producto agregado.

### Sprint 4 - Proveedores

Objetivo:

- Guardar datos de proveedor.
- Guardar reglas del proveedor.
- Consultar información de proveedor.
- Registrar si factura en blanco/negro, impuestos, flete, retiro, pago, bultos, días de visita y observaciones.

Resultado esperado:

- El asistente funciona como memoria operativa de proveedores.

### Sprint 5 - Productos, precios y márgenes

Objetivo:

- Guardar productos.
- Guardar precio por proveedor.
- Comparar proveedores.
- Guardar márgenes por familia.
- Guardar excepciones para productos gancho.
- Sugerir precio de venta.

Resultado esperado:

- El asistente ayuda a decidir compras y precios con reglas determinísticas validadas por backend.

### Sprint 6 - Memoria semántica / RAG

Objetivo:

- Agregar `knowledge_notes`.
- Agregar embeddings.
- Usar pgvector.
- Recuperar reglas o notas relevantes por similitud.

Resultado esperado:

- El asistente puede recuperar conocimiento no estructurado sin convertir todo en tablas rígidas.

### Sprint 7 - Facturas

Objetivo:

- Recibir imagen/PDF.
- Extraer datos con IA/OCR.
- Identificar proveedor.
- Aplicar reglas del proveedor.
- Pedir confirmación humana antes de guardar.
- Aprender de correcciones.

Resultado esperado:

- El sistema procesa facturas de forma asistida, no automática ciega.

## G. Modelo de datos inicial

### MVP mínimo

#### `businesses`

Propósito: representar cada supermercado o negocio.

Campos tentativos:

- `id`
- `name`
- `timezone`
- `created_at`
- `updated_at`

#### `users`

Propósito: representar usuarios internos o externos asociados a un negocio.

Campos tentativos:

- `id`
- `business_id`
- `display_name`
- `role`
- `phone`
- `email`
- `created_at`
- `updated_at`

#### `conversation_messages`

Propósito: guardar mensajes entrantes/salientes para trazabilidad, debugging, auditoría y mejora del asistente.

Campos tentativos:

- `id`
- `business_id`
- `user_id`
- `channel`
- `direction`: `inbound` o `outbound`
- `message_type`
- `text`
- `attachments`
- `raw_payload`
- `assistant_metadata`
- `created_at`

#### `reminders`

Propósito: guardar recordatorios operativos.

Campos tentativos:

- `id`
- `business_id`
- `created_by_user_id`
- `title`
- `description`
- `due_at`
- `recurrence_rule`
- `status`: `pending`, `done`, `cancelled`
- `completed_at`
- `source_message_id`
- `created_at`
- `updated_at`

### Futuro cercano

#### `expiration_items`

Propósito: registrar partidas o grupos de stock con vencimiento. Un mismo producto puede tener varias filas si distintas cantidades vencen en fechas diferentes.

Campos tentativos:

- `id`
- `business_id`
- `product_name`
- `product_id`
- `received_at`
- `quantity`
- `unit`
- `expires_at`
- `lot_code`
- `notify_days_before`
- `status`: `pending`, `offered`, `sold`, `discarded`
- `notes`
- `source_message_id`
- `created_at`
- `updated_at`

Notas de modelado:

- `lot_code` debe ser opcional. En muchos supermercados se piensa más en "las unidades que entraron tal día y vencen tal día" que en el lote formal del proveedor.
- La combinación importante para operación es producto + cantidad + fecha de ingreso + fecha de vencimiento.
- Si un mensaje dice "entraron 10 galletitas, 4 vencen en 10 días y 6 en 4 meses", deberían crearse dos registros de `expiration_items`.
- Si no se conoce `received_at`, puede asumirse la fecha del mensaje y pedir confirmación cuando sea importante.
- `quantity` debe permitir ajustes parciales porque una parte puede venderse, ofertarse o descartarse antes que el resto.

#### `suppliers`

Propósito: guardar datos estructurados de proveedores.

Campos tentativos:

- `id`
- `business_id`
- `name`
- `contact_name`
- `phone`
- `billing_type`: `white`, `black`, `mixed`, `unknown`
- `usual_taxes`
- `delivery_terms`
- `payment_terms`
- `minimum_order`
- `notes`
- `created_at`
- `updated_at`

#### `products`

Propósito: catálogo interno básico de productos.

Campos tentativos:

- `id`
- `business_id`
- `name`
- `normalized_name`
- `family`
- `brand`
- `unit_size`
- `barcode`
- `active`
- `created_at`
- `updated_at`

#### `supplier_products`

Propósito: relacionar productos con proveedores y precios.

Campos tentativos:

- `id`
- `business_id`
- `supplier_id`
- `product_id`
- `supplier_product_name`
- `cost_price`
- `currency`
- `tax_included`
- `observed_at`
- `created_at`
- `updated_at`

#### `margin_rules`

Propósito: guardar márgenes por familia o categoría.

Campos tentativos:

- `id`
- `business_id`
- `family`
- `margin_percentage`
- `rounding_rule`
- `active`
- `created_at`
- `updated_at`

#### `product_margin_exceptions`

Propósito: manejar excepciones para productos clave, productos gancho o estrategias especiales.

Campos tentativos:

- `id`
- `business_id`
- `product_id`
- `exception_type`: `hook`, `fixed_margin`, `fixed_price`, `manual_review`
- `margin_percentage`
- `fixed_price`
- `reason`
- `active`
- `created_at`
- `updated_at`

#### `knowledge_notes`

Propósito: guardar notas operativas semiestructuradas para memoria semántica/RAG.

Campos tentativos:

- `id`
- `business_id`
- `entity_type`: `supplier`, `product`, `family`, `general`
- `entity_id`
- `title`
- `content`
- `tags`
- `embedding`
- `source_message_id`
- `created_at`
- `updated_at`

## H. Providers / Interfaces a abstraer

### `ChannelAdapter`

Responsabilidad:

- Convertir payloads externos en `AssistantRequest`.
- Convertir `AssistantResponse` en respuestas del canal.

Por qué desacoplarlo:

- Permite agregar web, desktop o CLI sin tocar el core.
- Evita que Kapso/WhatsApp contamine la lógica principal.

### `LLMProvider`

Responsabilidad:

- Clasificar intención.
- Extraer entidades.
- Generar respuestas asistidas.
- Crear embeddings cuando corresponda.

Por qué desacoplarlo:

- Permite cambiar de modelo o proveedor.
- Facilita tests con mocks.
- Evita mezclar SDKs externos con servicios de negocio.

### `SpeechProvider`

Responsabilidad:

- Transcribir audio a texto.
- Devolver metadata de confianza y duración.

Por qué desacoplarlo:

- El core debería trabajar con texto normalizado.
- Permite cambiar motor de transcripción sin romper canales.

### `NotificationProvider`

Responsabilidad:

- Enviar notificaciones por WhatsApp, email, web push, desktop u otro canal.

Por qué desacoplarlo:

- Los recordatorios no deben depender de WhatsApp.
- En el futuro el usuario podría elegir canal preferido.

### `StorageProvider`

Responsabilidad:

- Guardar y recuperar adjuntos como audios, imágenes, PDFs o facturas.

Por qué desacoplarlo:

- Permite usar Supabase Storage al inicio y migrar si hace falta.
- Evita que módulos de negocio dependan del storage concreto.

### `SchedulerProvider`

Responsabilidad:

- Programar recordatorios, avisos recurrentes y alertas por vencimiento.

Por qué desacoplarlo:

- APScheduler puede servir al principio, pero más adelante podría reemplazarse por workers, queues o cron jobs externos.

## I. Prompts

Los prompts deben vivir en:

```txt
app/assistant/prompts/
```

Archivos sugeridos:

- `system.md`
- `intent_classifier.md`
- `reminder_parser.md`
- `expiration_parser.md`
- `supplier_note_parser.md`
- `price_parser.md`

Reglas:

- Los prompts deben versionarse como archivos.
- No deben quedar hardcodeados mezclados en servicios.
- Cada prompt debe tener una responsabilidad clara.
- Los cambios de prompt deben poder revisarse como cambios de producto.
- Las respuestas de IA deben validarse con schemas antes de usarse.
- Los prompts no deben contener reglas de negocio que deberían estar en servicios determinísticos.

## J. Reglas de uso de IA

- La IA no escribe directamente en la base.
- La IA interpreta intención y devuelve datos estructurados.
- El backend valida.
- El backend decide.
- El backend guarda.
- Si falta información, el asistente pregunta.
- Si hay baja confianza, pide confirmación.
- Los datos críticos se guardan solo después de confirmar.
- Los cálculos de márgenes, comparación de precios y reglas de proveedores deben ejecutarse en backend.
- Las acciones destructivas o sensibles deben requerir confirmación explícita.
- Toda respuesta estructurada del modelo debe pasar por Pydantic.
- Toda acción ejecutada debe quedar registrada para auditoría.

## Nota complementaria: Cloudflare, tenants y workers

Cloudflare puede ser útil más adelante, pero no debería ser una decisión obligatoria para el primer commit del backend.

Opciones relevantes:

- Workers: buenos para endpoints livianos, webhooks, tareas edge y ejecución serverless.
- Cron Triggers: útiles para disparar procesos periódicos, por ejemplo revisar recordatorios pendientes cada cierta frecuencia.
- Queues: útiles para procesar trabajos asíncronos, por ejemplo enviar notificaciones, procesar audios o reintentar integraciones.
- Workflows: útiles para procesos más largos o de varios pasos, por ejemplo procesamiento asistido de facturas.
- Durable Objects: útiles cuando hace falta estado coordinado por entidad, por ejemplo un workspace/tenant, una conversación activa o un recurso que requiere serializar operaciones.

Recomendación para Stock AI:

- Para Sprint 1, mantenerlo simple con FastAPI + Postgres + un `SchedulerProvider` intercambiable.
- No depender de Cloudflare para el core.
- Diseñar `SchedulerProvider` y `NotificationProvider` para que después puedan implementarse con APScheduler, Cloudflare Cron/Queues, workers propios o una queue administrada.
- Si el producto crece y hay muchos tenants, evaluar Durable Objects por tenant o por unidad de coordinación, pero no usar un único Durable Object global.
- Si se usa Cloudflare, debe quedar como infraestructura/adapters/providers, no dentro de servicios de negocio.

## K. Plan de implementación

1. Inicializar estructura base del proyecto Python/FastAPI.
2. Definir herramienta de dependencias: `uv` o `poetry`.
3. Crear `pyproject.toml` con configuración mínima.
4. Agregar `ruff` y `pytest`.
5. Crear `app/settings.py` con configuración por variables de entorno.
6. Crear schemas `AssistantRequest` y `AssistantResponse`.
7. Crear schemas de intención y acciones.
8. Crear endpoint `/assistant/message`.
9. Crear `AssistantEngine`.
10. Crear `IntentRouter` mockeado.
11. Crear carpeta `app/assistant/prompts/`.
12. Crear prompts iniciales versionados.
13. Crear `ReminderService` con lógica mínima.
14. Crear repositorio de reminders.
15. Crear registro de `conversation_messages`.
16. Agregar persistencia con Supabase/Postgres o SQLAlchemy según decisión tomada.
17. Agregar tests unitarios de schemas.
18. Agregar tests unitarios del `IntentRouter` mockeado.
19. Agregar tests del `AssistantEngine` sin LLM real.
20. Probar `/assistant/message` desde CLI/Postman.
21. Integrar OpenAI en `LLMProvider` detrás de interfaz.
22. Reemplazar intent mockeado por clasificación estructurada con IA.
23. Agregar confirmaciones para baja confianza o datos faltantes.
24. Crear adapter Kapso aislado en `channels/whatsapp_kapso.py`.
25. Crear endpoint `/webhooks/kapso`.
26. Transformar payload Kapso a `AssistantRequest`.
27. Transformar `AssistantResponse` a mensaje Kapso.
28. Agregar scheduler inicial para recordatorios.
29. Agregar módulo de vencimientos.
30. Agregar módulo de proveedores.
31. Agregar módulo de productos, precios y márgenes.
32. Agregar `knowledge_notes` y pgvector cuando exista suficiente contenido real.
33. Agregar procesamiento de facturas solo después de estabilizar proveedores/productos.

## L. Riesgos técnicos

- Acoplar WhatsApp al core.
- Usar IA para lógica que debería ser determinística.
- Guardar datos sin confirmación.
- No manejar ambigüedad.
- No tener logs de conversaciones.
- No versionar prompts.
- Intentar hacer un POS completo demasiado temprano.
- Meter facturas demasiado pronto.
- No diseñar para múltiples negocios/usuarios desde el inicio.
- Crear demasiada abstracción antes de validar el MVP.
- No separar mensajes entrantes, intención detectada y acción ejecutada.
- No guardar payloads crudos para debugging de integraciones.
- Depender de un único canal de notificación.
- No contemplar timezone del negocio.
- No tener estrategia clara para audios, imágenes y PDFs.
- Adoptar Cloudflare Durable Objects, Queues o Workflows antes de necesitar sus capacidades reales.
- Diseñar aislamiento multi-tenant solo en infraestructura y olvidarse de validarlo también en base de datos, servicios y permisos.

## M. Decisiones pendientes

- Supabase client directo vs SQLAlchemy.
- APScheduler vs worker/queue.
- `uv` vs `poetry`.
- Estructura monorepo o repo único backend.
- Cómo manejar autenticación de usuarios internos.
- Cómo mapear usuarios de WhatsApp a usuarios del sistema.
- Cómo guardar media de WhatsApp: Supabase Storage u otro.
- Cuándo agregar pgvector.
- Cuándo agregar panel web.
- Si el primer deploy será en Vercel, Render, Railway, Fly.io, VPS u otra plataforma.
- Cómo manejar backups y restauración de datos.
- Qué datos requieren confirmación obligatoria.
- Qué nivel de auditoría se necesita para cambios en precios y reglas.
- Cómo modelar permisos por rol dentro del supermercado.
- Si Cloudflare entra solo como edge/webhooks/scheduler o si también se usa para ejecución durable por tenant.
- Si el aislamiento multi-tenant será una única base con `business_id`, schemas separados, bases separadas o alguna combinación futura.

## Recomendación inicial

La primera implementación debería concentrarse en el núcleo sin WhatsApp:

```txt
AssistantRequest → AssistantEngine → IntentRouter → Service → Repository → AssistantResponse
```

Si este flujo queda limpio, Kapso entra como adapter con bajo riesgo. Si se empieza por WhatsApp, el proyecto puede quedar atrapado en detalles del canal antes de tener un producto sólido.

La secuencia más sana es:

1. Core HTTP genérico.
2. Recordatorios simples.
3. Persistencia y logs de conversación.
4. IA estructurada con confirmaciones.
5. Adapter Kapso.
6. Vencimientos.
7. Proveedores.
8. Productos/precios/márgenes.
9. RAG.
10. Facturas.

Lo que no conviene hacer todavía:

- Construir frontend web.
- Construir desktop.
- Implementar facturas.
- Agregar pgvector antes de tener datos reales.
- Crear un POS completo.
- Meter lógica de negocio en prompts.
- Diseñar todo alrededor de WhatsApp.
- Instalar dependencias sin cerrar decisiones básicas del proyecto.
