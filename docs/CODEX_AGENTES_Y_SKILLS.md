# Codex Agents y Skills - Stock AI

## Objetivo

Este documento define cómo usar Codex con varios agentes especializados para construir Stock AI sin perder coherencia técnica.

La idea no es tener muchos agentes por deporte. La idea es separar trabajo cuando haya límites claros de responsabilidad, archivos distintos y una revisión central.

## Fuentes oficiales revisadas

- [AGENTS.md](https://developers.openai.com/codex/guides/agents-md)
- [Agent Skills](https://developers.openai.com/codex/skills)
- [Subagents](https://developers.openai.com/codex/subagents)
- [Worktrees](https://developers.openai.com/codex/app/worktrees)
- [Docs MCP](https://platform.openai.com/docs/docs-mcp)

## Setup recomendado

1. Mantener `AGENTS.md` en la raíz del repo con las reglas permanentes del proyecto.
2. Mantener skills versionables en `.agents/skills/`.
3. Inicializar Git antes de usar worktrees de Codex.
4. Usar worktrees para tareas largas o paralelas cuando el repo ya tenga commits.
5. Usar subagentes para exploración, implementación por módulos y revisión.
6. Usar el agente principal como Tech Lead: decide arquitectura, reparte ownership, integra resultados.

## Roster de agentes

### 1. Tech Lead / Agente Principal

Responsabilidad:

- Mantener la arquitectura y el orden de sprints.
- Aprobar contratos compartidos.
- Evitar acoplar WhatsApp al core.
- Integrar resultados de subagentes.
- Cerrar decisiones pendientes.

Ownership:

- `AGENTS.md`
- `docs/`
- estructura raíz
- contratos globales
- decisiones transversales

Skill sugerida:

- `$stock-ai-architecture`

### 2. Backend Core Agent

Responsabilidad:

- Implementar el núcleo FastAPI.
- Crear `AssistantRequest`, `AssistantResponse`, `AssistantEngine` e `IntentRouter`.
- Crear endpoint genérico `/assistant/message`.
- Mantener el core libre de detalles de canales.

Ownership sugerido:

- `app/main.py`
- `app/assistant/`
- `app/schemas/`
- `tests/assistant/`

Skill sugerida:

- `$stock-ai-backend-core`

### 3. Data Model / Persistence Agent

Responsabilidad:

- Diseñar tablas, repositorios y límites multi-tenant.
- Modelar `businesses`, `users`, `conversation_messages`, `reminders` y `expiration_items`.
- Cuidar que cada query tenga `business_id`.
- Modelar vencimientos como partidas con cantidad, ingreso y vencimiento.

Ownership sugerido:

- `app/db/`
- `app/db/repositories/`
- modelos/migraciones cuando existan
- tests de persistencia

Skill sugerida:

- `$stock-ai-data-model`

### 4. AI / Prompts Agent

Responsabilidad:

- Diseñar prompts versionados.
- Crear schemas de salida estructurada.
- Definir confidence, clarification y confirmation flows.
- Mantener la IA como parser/intérprete, no ejecutor.

Ownership sugerido:

- `app/assistant/prompts/`
- `app/providers/llm/`
- schemas de intents
- tests/mocks de LLM

Skill sugerida:

- `$stock-ai-prompts`

### 5. Channel Adapters Agent

Responsabilidad:

- Implementar adapters de canal.
- Normalizar payloads externos a `AssistantRequest`.
- Transformar `AssistantResponse` a mensajes del canal.
- Mantener Kapso/WhatsApp fuera del core.

Ownership sugerido:

- `app/channels/`
- `app/providers/notifications/`
- endpoints de webhooks
- tests de adapters

Skill sugerida:

- `$stock-ai-channel-adapters`

### 6. Business Modules Agent

Responsabilidad:

- Implementar servicios determinísticos de negocio.
- Empezar por reminders.
- Luego vencimientos, proveedores, productos, precios y márgenes.

Ownership sugerido:

- `app/modules/reminders/`
- `app/modules/expirations/`
- `app/modules/suppliers/`
- `app/modules/products/`
- `app/modules/margins/`

Skills sugeridas:

- `$stock-ai-backend-core`
- `$stock-ai-data-model`

### 7. Scheduler / Background Jobs Agent

Responsabilidad:

- Diseñar `SchedulerProvider`.
- Implementar jobs idempotentes para recordatorios y alertas de vencimiento.
- Mantener APScheduler, Cloudflare Cron/Queues u otra opción detrás de interfaz.

Ownership sugerido:

- `app/providers/scheduler/`
- jobs de reminders/expirations
- tests con scheduler fake

Skills sugeridas:

- `$stock-ai-backend-core`
- `$stock-ai-data-model`

### 8. QA / Review Agent

Responsabilidad:

- Revisar bugs, regresiones, acoplamientos y tests faltantes.
- Validar que el core siga agnóstico al canal.
- Detectar fugas multi-tenant.
- Revisar que la IA no ejecute acciones críticas sin confirmación.

Ownership sugerido:

- `tests/`
- review de PRs/cambios
- checklist de calidad

Skill sugerida:

- `$stock-ai-quality-review`

## Cómo paralelizar sin romper el proyecto

### Sprint 1 recomendado

Agentes paralelos:

- Backend Core Agent: contratos y engine mockeado.
- Data Model Agent: modelos conceptuales y repositorios iniciales.
- AI / Prompts Agent: prompts y JSON estructurado mockeado.
- QA Agent: tests de contratos y checklist de riesgos.

El agente principal debe decidir antes:

- `uv` vs `poetry`.
- Supabase client directo vs SQLAlchemy.
- estructura raíz definitiva.
- shape final de `AssistantRequest` y `AssistantResponse`.

### Sprint 2 recomendado

Agentes paralelos:

- Channel Adapters Agent: Kapso adapter.
- Backend Core Agent: integración del adapter con el endpoint.
- QA Agent: tests de payload normalization.

El agente principal debe cuidar que Kapso no toque servicios de negocio directamente.

### Sprint 3 recomendado

Agentes paralelos:

- Business Modules Agent: vencimientos.
- Scheduler Agent: alertas por vencimiento.
- Data Model Agent: `expiration_items` y ajustes de cantidades.
- QA Agent: casos de productos con múltiples vencimientos.

## Prompts útiles para lanzar subagentes

### Backend core

```txt
Usá $stock-ai-backend-core. Implementá solo el core FastAPI del Sprint 1. Ownership: app/main.py, app/assistant/, app/schemas/, tests/assistant/. No toques adapters de WhatsApp ni DB real. Dejá tests básicos y listá archivos modificados.
```

### Data model

```txt
Usá $stock-ai-data-model. Diseñá modelos y repositorios iniciales para businesses, users, conversation_messages, reminders y expiration_items. Ownership: app/db/ y tests/db/. No implementes LLM ni webhooks. Cuidá multi-tenant con business_id.
```

### AI prompts

```txt
Usá $stock-ai-prompts. Creá prompts versionados y schemas de salida para intent classification. Ownership: app/assistant/prompts/, app/providers/llm/, tests/providers/. No llames APIs externas. Usá mocks.
```

### Kapso adapter

```txt
Usá $stock-ai-channel-adapters. Implementá el adapter Kapso como transformación entre payload externo y AssistantRequest/AssistantResponse. Ownership: app/channels/ y tests/channels/. No pongas reglas de negocio en el adapter.
```

### Review

```txt
Usá $stock-ai-quality-review. Revisá los cambios actuales buscando acoplamiento WhatsApp-core, lógica de negocio en prompts, fugas multi-tenant, acciones sin confirmación y tests faltantes. No edites archivos; devolvé findings con severidad y archivo/línea.
```

## Reglas de ownership

- Un subagente puede editar solo su área asignada.
- Si necesita tocar un contrato compartido, debe avisar en su respuesta final.
- El agente principal integra cambios sobre contratos compartidos.
- Los agentes no deben revertir cambios ajenos.
- Los agentes deben adaptar su trabajo a cambios existentes.

## Cuándo usar worktrees

Usar worktrees cuando:

- El repo ya esté inicializado con Git.
- La tarea pueda durar varios minutos.
- El agente tenga ownership claro.
- Se quiera revisar diff separado antes de integrar.

No usar worktrees cuando:

- El repo aún no tiene Git.
- La tarea es solo editar un documento.
- Hay decisiones de arquitectura pendientes que bloquean a todos.
- Dos agentes necesitarían editar los mismos archivos.

## Notas sobre MCP

MCP conviene cuando el contexto vive fuera del repo o cambia con frecuencia.

Para este proyecto, priorizar:

- OpenAI Docs MCP para OpenAI API, Codex, structured outputs, audio, visión y embeddings.
- GitHub MCP cuando existan issues, PRs y CI.
- Vercel MCP solo si se decide deployar o usar Vercel.

No conectar MCPs por entusiasmo. Cada MCP debe reducir un loop real.
