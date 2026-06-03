---
name: stock-ai-prompts
description: Prompt and structured AI workflow for Stock AI. Use when designing or reviewing versioned prompts, LLMProvider behavior, intent classification, JSON structured outputs, entity extraction, confidence thresholds, clarification questions, confirmation payloads, OpenAI integration boundaries, or tests/mocks for AI behavior.
---

# Stock AI Prompts

## Overview

Use this skill to keep AI behavior useful but constrained. The model interprets user language and returns structured data; backend services validate, decide, and persist.

## Rules

- Store prompts as files under `app/assistant/prompts/`.
- Do not hardcode long prompts in Python services.
- Use Spanish examples because the first market is Argentina.
- Use English JSON keys and intent names.
- Require Pydantic validation before using model output.
- Ask for clarification when required entities are missing.
- Ask for confirmation when confidence is low or data is critical.
- Never let the LLM write directly to the database.

## Prompt Files

- `system.md`
- `intent_classifier.md`
- `reminder_parser.md`
- `expiration_parser.md`
- `supplier_note_parser.md`
- `price_parser.md`

## Structured Output Shape

Prefer model output shaped like:

```json
{
  "intent": "save_expiration",
  "entities": {},
  "confidence": 0.88,
  "requires_clarification": false,
  "clarification_question": null
}
```

## Review Checklist

- Does the prompt ask for deterministic business calculations? Move that to services.
- Does the prompt include enough Argentina-specific language examples?
- Is every output field validated by a schema?
- Is there a safe fallback to `unknown`?
- Are confirmation paths explicit?
