# Sprint 3 - Kapso Sandbox

## Objetivo

Conectar Stock AI con Kapso Sandbox sin convertir el producto en un bot de WhatsApp.
Kapso queda como adapter de canal y el Assistant Core sigue trabajando con
`AssistantRequest` y `AssistantResponse`.

## Implementado

- Adapter Kapso aislado en `app/channels/`.
- Endpoint `POST /webhooks/kapso`.
- Soporte para webhooks `whatsapp.message.received`.
- Soporte para payload simple y batch.
- Normalizacion de mensajes Kapso a `AssistantRequest`.
- Envio de respuestas por Kapso cuando `KAPSO_API_KEY` esta configurado.
- Validacion opcional de `X-Webhook-Signature` con `KAPSO_WEBHOOK_SECRET`.
- Preservacion del payload original en `raw_payload`.
- Tests de adapter, endpoint webhook, batches, firma e independencia del core.

## Validacion Real

Validado el 2026-07-22 con Kapso Sandbox:

- Webhook `whatsapp.message.received` entregado por Kapso con `200 OK`.
- URL de prueba usada: `https://prot-moms-buttons-proposal.trycloudflare.com/webhooks/kapso`.
- Mensaje sandbox recibido desde `541169405063`.
- Conversacion y recordatorio guardados en Supabase.
- No hubo errores de entrega de webhook en Kapso.

Pendiente antes de cerrar completamente:

- Confirmar respuesta outbound por WhatsApp con `KAPSO_API_KEY` cargada al levantar Uvicorn.
- Confirmar que el usuario recibe el texto de respuesta del Assistant Core en WhatsApp.

## Variables

```powershell
DEFAULT_BUSINESS_ID="demo-business"
KAPSO_API_KEY="tu_api_key"
KAPSO_WEBHOOK_SECRET="un_secret_para_el_webhook"
KAPSO_SANDBOX_PHONE_NUMBER_ID="597907523413541"
```

## Probar Local

Levantar API:

```powershell
$env:KAPSO_API_KEY = [Environment]::GetEnvironmentVariable("KAPSO_API_KEY", "User")
$env:DEFAULT_BUSINESS_ID = "demo-business"
$env:KAPSO_SANDBOX_PHONE_NUMBER_ID = "597907523413541"
$env:KAPSO_WEBHOOK_SECRET = "stock-ai-local-dev-secret"
& "$env:APPDATA\Python\Python312\Scripts\uv.exe" run uvicorn app.main:app --reload
```

Exponer con Cloudflared:

```powershell
cloudflared tunnel --url http://localhost:8000
```

Registrar en Kapso Sandbox:

```txt
https://TU-TUNEL.trycloudflare.com/webhooks/kapso
```

Evento:

```txt
whatsapp.message.received
```

## Fuera De Este Sprint

- No templates.
- No scheduler.
- No Meta productivo.
- No billing.
- No vencimientos.
- No OpenAI real.

## Nota Sobre Recordatorios Programados

Este sprint solo guarda recordatorios y permite probar el flujo WhatsApp -> Kapso -> Adapter -> Core -> DB.
Todavia no existe scheduler, por lo tanto los recordatorios no se disparan automaticamente a una hora futura.
