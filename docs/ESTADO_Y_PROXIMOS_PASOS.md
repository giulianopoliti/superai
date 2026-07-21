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
