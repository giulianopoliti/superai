# Stock AI

Backend inicial de Stock AI: un Assistant Core agnóstico al canal para operaciones de supermercado.

## Sprint 1

Este sprint implementa solo el núcleo:

- FastAPI con `POST /assistant/message`.
- Contratos `AssistantRequest` y `AssistantResponse`.
- `AssistantEngine` e `IntentRouter` mockeado.
- `ReminderService` con repositorio en memoria.
- Prompts versionados como archivos placeholder.
- Tests sin WhatsApp, sin OpenAI real y sin DB real.

## Requisitos

- Python 3.12+
- uv

## Instalación

```powershell
uv sync
```

## Ejecutar API

```powershell
uv run uvicorn app.main:app --reload
```

## Probar endpoint

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

## CLI

```powershell
uv run python -m app.cli "recordame revisar la heladera"
```

## Tests y lint

```powershell
uv run pytest
uv run ruff check
```

## Fuera de Sprint 1

No incluye WhatsApp/Kapso, OpenAI real, Supabase/Postgres, migraciones, scheduler, vencimientos, proveedores, productos/precios/márgenes, facturas, frontend ni desktop.

## Sprint 2 local persistence

Para desarrollar con Postgres local:

```powershell
docker compose up -d db
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Si `DATABASE_URL` no está configurado, la app usa repositorios en memoria como fallback de desarrollo/tests.

## Sprint 3 Kapso Sandbox

Este sprint agrega un adapter de WhatsApp/Kapso sin acoplar el Assistant Core:

- `POST /webhooks/kapso` recibe webhooks `whatsapp.message.received`.
- El payload externo se transforma a `AssistantRequest`.
- La respuesta del core se envia por Kapso si `KAPSO_API_KEY` esta configurado.
- El endpoint soporta payloads simples y batch.
- La firma `X-Webhook-Signature` se valida cuando `KAPSO_WEBHOOK_SECRET` tiene valor.

Variables relevantes:

```powershell
DEFAULT_BUSINESS_ID="demo-business"
KAPSO_API_KEY="tu_api_key"
KAPSO_WEBHOOK_SECRET="un_secret_para_el_webhook"
KAPSO_SANDBOX_PHONE_NUMBER_ID="597907523413541"
```

## Sprint 4 Reminder Scheduler

El backend puede guardar recordatorios con `due_at` y despacharlos por WhatsApp cuando vencen.

Activar scheduler local:

```powershell
$env:SCHEDULER_ENABLED = "true"
$env:SCHEDULER_INTERVAL_SECONDS = "60"
```

Disparo manual:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/internal/reminders/dispatch-due
```

## Sprint 4.2 LLM para recordatorios

La IA puede interpretar recordatorios en lenguaje natural usando OpenAI Responses API con
salida estructurada. Sigue siendo opcional: si `LLM_ENABLED` esta apagado, si falta
`OPENAI_API_KEY`, o si el provider falla, el backend usa el router deterministico local.

Activar en PowerShell:

```powershell
$env:LLM_ENABLED = "true"
$env:OPENAI_API_KEY = "tu_api_key"
$env:OPENAI_MODEL = "gpt-5.6-luna"
```

Ejemplo esperado:

```txt
recordame a las 12.30 que si no puedo concentrarme me ponga a escribir
```

El LLM debe extraer `title`, `due_at`, `confidence` y si necesita aclaracion. El backend
valida y guarda; la IA no escribe directamente en la base.

Levantar API local:

```powershell
& "$env:APPDATA\Python\Python312\Scripts\uv.exe" run uvicorn app.main:app --reload
```

Exponer con Cloudflared:

```powershell
cloudflared tunnel --url http://localhost:8000
```

Registrar en Kapso Sandbox la URL:

```txt
https://TU-TUNEL.trycloudflare.com/webhooks/kapso
```

Evento:

```txt
whatsapp.message.received
```

Para una prueba manual sin Kapso:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/webhooks/kapso `
  -ContentType "application/json" `
  -Headers @{ "X-Webhook-Event" = "whatsapp.message.received" } `
  -Body '{
    "message": {
      "from": "541169405063",
      "from_user_id": "AR.test",
      "id": "wamid.test",
      "kapso": {
        "direction": "inbound",
        "phone_number": "541169405063",
        "phone_number_id": "597907523413541"
      },
      "text": { "body": "recordame comprar bolsas" },
      "timestamp": "1784651246",
      "type": "text"
    },
    "phone_number_id": "597907523413541"
  }'
```
