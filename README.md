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
