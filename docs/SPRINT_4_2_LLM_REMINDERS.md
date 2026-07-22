# Sprint 4.2 - Interpretacion Inteligente De Recordatorios

## Objetivo

Agregar IA para interpretar recordatorios en lenguaje natural sin acoplar el core a OpenAI
ni permitir que el modelo ejecute acciones directamente.

## Alcance

- Crear `LLMProvider` como interfaz.
- Agregar `OpenAILLMProvider` usando Responses API y salida estructurada.
- Crear `LLMIntentRouter` con fallback al router deterministico actual.
- Versionar prompt de recordatorios en `app/assistant/prompts/reminder_parser.md`.
- Pedir aclaracion cuando falten datos o la confianza sea baja.

## Fuera De Alcance

- LangChain.
- Agents SDK.
- Tools autonomas ejecutadas por el modelo.
- Escrituras directas del LLM a la base.
- Interpretacion de vencimientos, proveedores o precios.

## Flujo

```txt
WhatsApp / CLI / HTTP
        |
AssistantRequest
        |
LLMIntentRouter
        |
LLMProvider -> OpenAI Responses API -> JSON estructurado
        |
AssistantEngine valida y ejecuta
        |
ReminderService guarda
```

## Variables

```txt
LLM_ENABLED=false
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.6-luna
LLM_TIMEOUT_SECONDS=10
```

## Criterio De Aceptacion

- Los tests pasan sin llamadas externas.
- Si `LLM_ENABLED=false`, el router local sigue funcionando.
- Si OpenAI falla, se usa fallback deterministico.
- Si el LLM pide aclaracion, no se crea recordatorio.
- El caso `12.30` puede interpretarse mediante fake provider en tests.
