---
name: stock-ai-backend-core
description: Backend core workflow for Stock AI. Use when implementing or reviewing the FastAPI app, Pydantic schemas, AssistantRequest/AssistantResponse, AssistantEngine, IntentRouter, provider interfaces, service boundaries, endpoint /assistant/message, or tests for the channel-agnostic assistant core.
---

# Stock AI Backend Core

## Overview

Use this skill to implement the backend core without channel coupling. The first goal is a generic assistant endpoint that can be exercised from CLI/Postman before WhatsApp exists.

## Implementation Order

1. Create schemas before services.
2. Create `AssistantEngine` with dependency-injected router/services.
3. Start `IntentRouter` as a mock or fake.
4. Implement `ReminderService` as the first business module.
5. Persist conversation logs and reminders only after the contracts are stable.
6. Add OpenAI provider only behind `LLMProvider`.
7. Add Kapso only through channel adapters after the core works.

## File Ownership

- `app/main.py`
- `app/assistant/`
- `app/schemas/`
- `app/modules/reminders/`
- `tests/assistant/`
- `tests/modules/reminders/`

## Rules

- Do not import Kapso, WhatsApp, or webhook payload types in assistant core.
- Do not call OpenAI directly from services.
- Do not create hidden global state for tenant/user context.
- Use Pydantic models for external/internal contracts.
- Keep tests runnable without network calls.
- Keep services deterministic where possible.

## Done When

- `/assistant/message` accepts normalized input.
- Tests cover request/response schemas and engine behavior.
- No channel-specific dependency exists in the core.
- The next agent can implement Kapso by mapping payloads only.
