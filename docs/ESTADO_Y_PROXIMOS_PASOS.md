# Estado y próximos pasos - Stock AI

## Estado actual

Estamos en la rama:

```txt
feature/sprint-2-persistence
```

Commits relevantes:

```txt
29cab8c Add local persistence with SQLAlchemy
d12206f Implement sprint 1 assistant core
364dcce first commit about agents
```

El proyecto ya tiene un backend mínimo FastAPI con Assistant Core agnóstico al canal, persistencia SQL portable y conexión funcionando contra Supabase como Postgres.

## Qué hicimos hasta ahora

### 1. Plan técnico inicial

Se creó:

```txt
docs/PLAN_ACCION_TECNICO.md
```

Define:

- Stock AI como asistente operativo para supermercados.
- Assistant Core agnóstico al canal.
- WhatsApp/Kapso como adapter futuro, no como núcleo.
- Backend como responsable de validar, decidir y guardar.
- IA como intérprete de intención y extractor de datos estructurados.
- Prompts versionados en archivos.
- Arquitectura preparada para web/desktop futuro sin construirlo todavía.

### 2. Setup de trabajo con Codex

Se creó:

```txt
AGENTS.md
docs/CODEX_AGENTES_Y_SKILLS.md
.agents/skills/
```

Esto deja reglas persistentes para Codex, skills especializadas y una estrategia para usar subagentes por responsabilidad.

Skills del proyecto:

- `stock-ai-architecture`
- `stock-ai-backend-core`
- `stock-ai-data-model`
- `stock-ai-prompts`
- `stock-ai-channel-adapters`
- `stock-ai-quality-review`

### 3. Sprint 1 - Assistant Core sin WhatsApp

Se implementó:

- FastAPI.
- Endpoint `POST /assistant/message`.
- Endpoint `GET /health`.
- CLI local.
- `AssistantRequest`.
- `AssistantResponse`.
- `AssistantAction`.
- `IntentResult`.
- `IntentRouter` mockeado.
- `AssistantEngine`.
- `ReminderService`.
- Repositorios in-memory.
- Prompts placeholder versionados.
- Tests de core, schemas, API, router, reminders y frontera anti WhatsApp/Kapso.

Verificación:

```txt
14 tests passed
ruff check passed
```

### 4. Sprint 2 - Persistencia SQL portable

Se implementó:

- SQLAlchemy.
- Alembic.
- psycopg.
- Docker Compose con Postgres local.
- `DATABASE_URL` por `.env`.
- Modelos SQL para:
  - `businesses`
  - `users`
  - `conversation_messages`
  - `reminders`
- Migración inicial Alembic.
- Repositorios SQL para:
  - reminders
  - conversation messages
- Fallback in-memory cuando no hay `DATABASE_URL`.
- Tests SQL con SQLite in-memory para no depender de Docker/Supabase en la suite default.

Verificación:

```txt
17 tests passed
ruff check passed
docker compose config OK
alembic upgrade head --sql OK
```

### 5. Supabase como Postgres administrado

Se decidió usar Supabase por ahora solo como Postgres.

Regla importante:

```txt
No usar supabase-py todavía.
No acoplar el backend a Supabase.
Usar Supabase solo mediante DATABASE_URL.
Mantener SQLAlchemy + Alembic.
```

Proyecto Supabase:

```txt
uwyslofdvynmsdbsbpam
```

Se creó `.env` local con `DATABASE_URL`. Ese archivo está ignorado por Git.

Se ejecutó correctamente:

```powershell
& "$env:APPDATA\Python\Python312\Scripts\uv.exe" run alembic upgrade head
```

Y la API levantó correctamente:

```powershell
& "$env:APPDATA\Python\Python312\Scripts\uv.exe" run uvicorn app.main:app --reload
```

Servidor local:

```txt
http://127.0.0.1:8000
```

## Cómo levantar el proyecto

### Opción recomendada si `uv` está en PATH

```powershell
cd C:\Users\54116\Downloads\stock-ai
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

### Opción con ruta directa a `uv.exe`

```powershell
cd C:\Users\54116\Downloads\stock-ai
& "$env:APPDATA\Python\Python312\Scripts\uv.exe" sync
& "$env:APPDATA\Python\Python312\Scripts\uv.exe" run alembic upgrade head
& "$env:APPDATA\Python\Python312\Scripts\uv.exe" run uvicorn app.main:app --reload
```

### Agregar `uv` al PATH de usuario

```powershell
[Environment]::SetEnvironmentVariable(
  "Path",
  [Environment]::GetEnvironmentVariable("Path", "User") + ";$env:APPDATA\Python\Python312\Scripts",
  "User"
)
```

Después de eso, cerrar y abrir PowerShell.

## Cómo probar la API

Con el server corriendo, en otra terminal:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/assistant/message `
  -ContentType "application/json" `
  -Body '{
    "channel": "cli",
    "external_user_id": "local-user",
    "business_id": "demo-business",
    "message_type": "text",
    "text": "recordame revisar la heladera"
  }'
```

Listar recordatorios:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/assistant/message `
  -ContentType "application/json" `
  -Body '{
    "channel": "cli",
    "external_user_id": "local-user",
    "business_id": "demo-business",
    "message_type": "text",
    "text": "listar recordatorios pendientes"
  }'
```

Verificar en Supabase:

- `conversation_messages` debe tener mensajes inbound/outbound.
- `reminders` debe tener el recordatorio creado.

## Decisiones tomadas

- Backend-first en raíz del repo.
- Python + FastAPI + Pydantic.
- `uv` para dependencias.
- SQLAlchemy + Alembic para persistencia.
- Supabase solo como Postgres administrado.
- No usar Supabase SDK todavía.
- No usar Auth, Storage, Edge Functions, Realtime ni RLS avanzado todavía.
- No construir WhatsApp/Kapso todavía.
- No usar OpenAI real todavía.
- Mantener el core agnóstico al canal.
- Usar inglés para código/tablas/campos/intents.
- Usar castellano para respuestas al usuario.

## Próximos pasos recomendados

### Paso 1 - Validar persistencia real en Supabase

Objetivo:

- Confirmar que `/assistant/message` crea filas reales en Supabase.
- Confirmar que listar recordatorios lee desde Supabase.
- Confirmar que reiniciar la API no pierde recordatorios.

Checklist:

- Crear recordatorio por API.
- Listar recordatorios por API.
- Ver filas en `conversation_messages`.
- Ver filas en `reminders`.
- Reiniciar API.
- Listar de nuevo.

### Paso 2 - Mejorar bootstrap de negocio/usuario

Hoy `business_id` llega en el request, pero todavía no hay una forma cómoda de crear negocios/usuarios.

Opciones:

- Script seed local para crear `demo-business`.
- Endpoint interno temporal de setup.
- Crear filas manualmente en Supabase.

Recomendación:

```txt
Crear script seed, no endpoint público.
```

### Paso 3 - Definir Sprint 3

Dos caminos posibles:

#### Opción A - Kapso/WhatsApp adapter

Conviene si queremos demo por WhatsApp rápido.

Incluye:

- Endpoint `/webhooks/kapso`.
- Adapter Kapso -> `AssistantRequest`.
- `AssistantResponse` -> mensaje Kapso.
- Mantener Kapso fuera del core.

#### Opción B - OpenAI / IntentRouter real

Conviene si queremos que el asistente entienda mensajes más naturales antes de WhatsApp.

Incluye:

- `LLMProvider`.
- Prompts reales.
- Structured output.
- Confidence.
- Clarification.

#### Opción C - Vencimientos

Conviene si queremos avanzar directo al valor operativo del supermercado.

Incluye:

- `expiration_items`.
- Cantidades por fecha de ingreso/vencimiento.
- Lote opcional.
- Listar vencimientos.
- Alertas más adelante.

Recomendación actual:

```txt
Primero validar Supabase funcionando.
Después hacer Kapso si queremos demo rápida.
Después OpenAI real.
Después vencimientos.
```

## Pendientes técnicos

- Resolver `uv` en PATH permanente.
- Confirmar si el host directo de Supabase funciona estable o si conviene usar pooler.
- Decidir si queremos RLS ahora o más adelante.
- Agregar constraints más estrictas en DB para `status`, `direction` y `channel`.
- Agregar `source_message_id` a reminders cuando el flujo de conversación esté más maduro.
- Agregar tests de migración online contra Postgres local o Supabase de dev.
- Mergear `feature/sprint-2-persistence` a `main` cuando terminemos validación manual.

## Qué no hacer todavía

- No meter lógica WhatsApp dentro de `app/assistant`.
- No agregar Supabase SDK si solo usamos Postgres.
- No crear frontend.
- No crear desktop.
- No implementar facturas.
- No agregar pgvector.
- No agregar Cloudflare.
- No agregar scheduler hasta tener vencimientos/recordatorios más claros.
## Actualizacion Sprint 3 - Kapso Sandbox

Estado actualizado al 2026-07-22:

- Rama actual esperada: `feature/sprint-3-kapso-adapter`.
- Se implemento adapter Kapso aislado en `app/channels/`.
- Se agrego endpoint `POST /webhooks/kapso`.
- Se registro webhook Kapso Sandbox para `whatsapp.message.received`.
- Se valido flujo real: Kapso Sandbox -> Cloudflared -> FastAPI -> Assistant Core -> Supabase.
- Kapso registro entrega del webhook con `200 OK`.
- Supabase recibio `conversation_messages` y recordatorio asociado.

Pendiente para cerrar Sprint 3:

- Confirmar outbound real por WhatsApp con `KAPSO_API_KEY` cargada al levantar Uvicorn.
- Ejecutar `pytest` y `ruff check`.
- Commit de Sprint 3.
- Merge a `main`.

Nota sobre recordatorios:

- Hoy los recordatorios se guardan, pero no se disparan automaticamente.
- El disparo por horario requiere el proximo sprint de scheduler/notificaciones.
- Para recordar a una hora futura necesitamos guardar/parsear `due_at`; hoy el router mockeado solo guarda el texto como titulo.

Recomendacion de siguiente sprint:

1. Cerrar Sprint 3 confirmando respuesta outbound.
2. Implementar scheduler basico para recordatorios.
3. Agregar parseo real de fechas/horarios, idealmente con OpenAI detras de `LLMProvider`.
4. Despues avanzar con vencimientos por cantidades y fecha de vencimiento.
## Actualizacion Sprint 4 - Reminder Scheduler

Estado actualizado al 2026-07-22:

- Rama actual esperada: `feature/sprint-4-reminder-scheduler`.
- Se agrego parseo deterministico inicial de horarios para recordatorios.
- Se usa `due_at` existente en la tabla `reminders`, sin migracion nueva.
- Se agrego estado `notified` para evitar envios duplicados.
- Se agrego `NotificationProvider` desacoplado.
- Se agrego `KapsoNotificationProvider` para disparos por WhatsApp.
- Se agrego `ReminderDispatcher` para enviar recordatorios vencidos.
- Se agrego endpoint interno `POST /internal/reminders/dispatch-due`.
- Se agrego scheduler interno opcional con `SCHEDULER_ENABLED=true`.

Limites actuales:

- El parseo de fechas es basico y deterministico; no usa OpenAI todavia.
- No hay recurrencia.
- No hay `notified_at`; se usa `status=notified` como MVP.
- El canal default del dispatcher es configurable, pero los recordatorios todavia no guardan canal preferido propio.

Proximo paso recomendado:

1. Probar un recordatorio real por WhatsApp con `en 1 minutos`.
2. Confirmar que llega el mensaje `Recordatorio: ...`.
3. Si funciona, commitear Sprint 4.
4. Luego avanzar con OpenAI real para parsear fechas/horarios con lenguaje mas natural.

## Actualizacion Sprint 5 - Procurement Agent

Estado actualizado al 2026-07-27:

Estado de frontend:

```txt
No hay frontend visual implementado todavia.
Hay API backend completa para que el mini frontend y WhatsApp usen la misma logica.
La prueba manual hoy se hace desde Swagger en /docs.
```

- Se creo el plan del Procurement Agent en `docs/SPRINT_5_PROCUREMENT_AGENT.md`.
- Se creo el documento operativo en `docs/PROCUREMENT_AGENT_ESTADO_Y_PLAN.md`.
- Se agrego el modulo base `app/modules/procurement/`.
- Se agregaron modelos SQL para `products`, `suppliers` y `supplier_products`.
- Se agregaron migraciones Alembic:
  - `20260725_0002_procurement_products_suppliers.py`
  - `20260725_0003_allow_duplicate_product_barcodes.py`
- Se aplicaron migraciones en Supabase/Postgres.
- Se creo el tenant inicial `demo-business`.
- Se importo el CSV real del POS:
  - filas leidas: 3035
  - productos importados: 3035
  - omitidos: 0
  - productos en DB: 3035
- Se detecto que hay codigos de barras repetidos en el CSV real.
- Se decidio que `barcode` no sea unico; la identidad principal del POS es
  `business_id + external_product_id`.
- Se agrego import bulk para evitar imports lentos contra Supabase remoto.
- Se agrego `catalog_imports` para auditar importaciones de catalogo.
- Se agrego CLI:
  - `python -m app.cli import-catalog <csv_path> --business-id demo-business`
  - `python -m app.cli import-supplier-offer <json_path> --business-id demo-business`
- Ultimo import via CLI:
  - `catalog_import_id`: `ddf81291-758f-455a-911c-0dbab77b9b01`
  - estado: `completed`
  - productos activos: 3030
  - productos sin costo: 440
  - productos sin barcode: 552
  - barcodes duplicados: 56
- Se agregaron `supplier_offer_documents` y `supplier_offer_items`.
- Se creo `SupplierOfferService`.
- Se guardo una lista manual real de Vital:
  - `supplier_offer_document_id`: `03803038-deb9-4ce1-af78-604f1b3d2832`
  - items guardados: 2
  - estado: `extracted`
- Se inicio la capa OCR/IA:
  - interfaz `DocumentExtractionProvider`
  - provider local para texto simple
  - provider OpenAI preparado con structured outputs
  - prompt `supplier_offer_extractor.md`
- Se agrego `ProductMatchService` inicial.
- Se comparo Vital `112964.pdf` contra el catalogo:
  - `supplier_offer_document_id`: `c71e30dd-76b9-42fd-a4ca-34575169b532`
  - items comparados: 29
  - matches encontrados: 20
  - comprar: 5
  - revisar: 23
  - no comprar: 1
  - CSV: `output/vital_112964_comparison.csv`
- Se ajusto normalizacion para aliases como `t/b` y `tetrabrick`.
- Se agregaron tablas y modelos para memoria revisable de matching:
  - `product_match_candidates`
  - `product_match_feedback`
- Se aplico la migracion `20260727_0006_product_match_candidates_feedback.py`
  en Supabase/Postgres.
- Se persistieron los candidatos del PDF Vital `112964.pdf`:
  - `supplier_offer_document_id`: `c71e30dd-76b9-42fd-a4ca-34575169b532`
  - candidatos guardados: 29
  - estado inicial: `pending`
- Se agregaron comandos CLI para operar la revision:
  - `compare-supplier-offer --persist-candidates`
  - `list-product-matches`
  - `review-product-match`
- El matching ahora consulta feedback humano previo antes del scoring difuso:
  - un candidato aceptado para el mismo proveedor y nombre normalizado gana con confianza `1.0000`
  - un candidato rechazado queda excluido del fuzzy matching futuro para ese item
  - el feedback queda aislado por `business_id`
- Se agrego una API reusable para mini frontend y WhatsApp:
  - `POST /procurement/catalog-imports`
  - `POST /procurement/supplier-offers/from-json`
  - `POST /procurement/supplier-offers/from-document`
  - `POST /procurement/supplier-offers/{supplier_offer_document_id}/compare`
  - `GET /procurement/supplier-offers/{supplier_offer_document_id}/matches`
  - `POST /procurement/product-matches/{product_match_candidate_id}/accept`
  - `POST /procurement/product-matches/{product_match_candidate_id}/reject`
  - `POST /procurement/product-matches/{product_match_candidate_id}/correct`
- El endpoint `from-document` recibe upload multipart, extrae items, persiste la oferta,
  compara contra catalogo y guarda candidatos si `persist_candidates=true`.
- Providers disponibles para documentos:
  - `local_text`: para probar con `.txt` ya extraido, sin IA.
  - `openai`: para PDF/imagenes reales con structured output y `OPENAI_API_KEY`.

Flujo disponible hoy desde Swagger:

1. `POST /procurement/catalog-imports`: cargar catalogo POS por ruta local.
2. `POST /procurement/supplier-offers/from-document`: subir documento de proveedor.
3. `GET /procurement/supplier-offers/{supplier_offer_document_id}/matches`: ver sugerencias.
4. `POST /procurement/product-matches/{product_match_candidate_id}/accept`: aceptar.
5. `POST /procurement/product-matches/{product_match_candidate_id}/reject`: rechazar.
6. `POST /procurement/product-matches/{product_match_candidate_id}/correct`: corregir.

Decision de arquitectura:

```txt
No usar RAG, pgvector ni base vectorial todavia.
Primero guardar memoria relacional auditable por tenant.
```

Proximo paso recomendado:

1. Construir mini frontend sobre los endpoints existentes.
2. Normalizar comparacion por unidad para packs y multipacks.
3. Mejorar reporte de oportunidades y revisiones.
4. Agregar provider Gemini para documentos si queremos usar la key actual sin OpenAI.
