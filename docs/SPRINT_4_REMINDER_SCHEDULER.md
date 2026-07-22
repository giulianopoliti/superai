# Sprint 4 - Reminder Scheduler

## Objetivo

Permitir que Stock AI guarde recordatorios con fecha/hora y los dispare por WhatsApp cuando vencen.

## Implementado

- Parseo deterministico inicial de horarios:
  - `a las 10:05hs de hoy`
  - `mañana a las 9`
  - `en 10 minutos`
  - `en 2 horas`
- Guardado de `due_at` en recordatorios.
- Estado `notified` para evitar envios duplicados.
- `ReminderDispatcher` para consultar recordatorios vencidos y enviar notificaciones.
- `NotificationProvider` desacoplado.
- `KapsoNotificationProvider` para enviar WhatsApp sin acoplar reminders a Kapso.
- Endpoint interno `POST /internal/reminders/dispatch-due`.
- Scheduler interno opcional con `SCHEDULER_ENABLED=true`.

## Variables

```powershell
SCHEDULER_ENABLED=true
SCHEDULER_INTERVAL_SECONDS=60
INTERNAL_API_TOKEN="token-local-opcional"
```

Para enviar por WhatsApp tambien deben estar:

```powershell
KAPSO_API_KEY="..."
KAPSO_SANDBOX_PHONE_NUMBER_ID="597907523413541"
```

## Prueba Manual

Crear recordatorio:

```txt
recordame revisar caja en 1 minutos
```

Disparar manualmente:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/internal/reminders/dispatch-due
```

Con token interno:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/internal/reminders/dispatch-due `
  -Headers @{ "X-Internal-Token" = "token-local-opcional" }
```

## Limites Del Sprint

- No usa OpenAI real para fechas ambiguas.
- No implementa recurrencia.
- No implementa timezones por negocio desde DB, usa `America/Buenos_Aires`.
- No separa aun `notified_at`; usa `status=notified` como MVP anti-duplicados.
