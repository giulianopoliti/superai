---
name: stock-ai-architecture
description: Architecture guidance for Stock AI, a channel-agnostic supermarket operations assistant. Use when planning or reviewing Assistant Core boundaries, channel adapters, providers, modules, sprint sequencing, multi-agent ownership, or decisions that affect FastAPI, Supabase/Postgres, OpenAI, Kapso, scheduler, web/desktop futures, or Cloudflare tradeoffs.
---

# Stock AI Architecture

## Overview

Use this skill to keep Stock AI architecturally clean: Assistant Core first, WhatsApp/Kapso as an adapter, deterministic backend services for business rules, and providers for external integrations.

## Workflow

1. Read `docs/PLAN_ACCION_TECNICO.md` before making or reviewing architecture decisions.
2. Identify whether the decision affects core, channel adapter, module, provider, persistence, scheduler, or prompts.
3. Keep shared contracts stable before assigning work to subagents.
4. Prefer the simplest Sprint 1 path unless a future requirement would be expensive to retrofit.
5. Document important tradeoffs in `docs/` instead of burying them in code comments.

## Non-Negotiables

- Do not build the system as a WhatsApp bot.
- Keep `AssistantRequest` and `AssistantResponse` independent from Kapso payloads.
- Keep AI as interpreter/parser; backend validates and executes.
- Keep prompts versioned as files.
- Keep business rules in services, not prompts.
- Keep external systems behind providers/interfaces.
- Keep multi-tenant boundaries visible from the start with `business_id`.
- Use English internally and Spanish for user-facing product copy.

## Preferred Architecture

```txt
Channels -> ChannelAdapter -> AssistantCore -> Services -> Repositories/Providers
```

Recommended first slice:

- `app/schemas/assistant.py`
- `app/assistant/engine.py`
- `app/assistant/intent_router.py`
- `app/modules/reminders/`
- `app/db/repositories/`
- `tests/assistant/`

## Decision Checklist

- Does this couple a future module to WhatsApp?
- Can this be tested without OpenAI or Kapso?
- Is the business rule deterministic and outside the prompt?
- Is tenant isolation explicit?
- Can a subagent own this change without editing shared contracts?
- Is this needed for the current sprint?
