---
name: stock-ai-channel-adapters
description: Channel adapter workflow for Stock AI. Use when implementing or reviewing WhatsApp/Kapso webhooks, web, desktop, CLI, notification adapters, payload normalization, raw payload storage, channel-specific media handling, or transformations between external channel payloads and AssistantRequest/AssistantResponse.
---

# Stock AI Channel Adapters

## Overview

Use this skill to add channels without contaminating the Assistant Core. A channel adapter translates external payloads into normalized requests and normalized responses back into channel messages.

## Adapter Contract

Adapters should:

- Parse external payload.
- Resolve `business_id` and `external_user_id`.
- Normalize text, audio, image, PDF, and attachments.
- Produce `AssistantRequest`.
- Send `AssistantResponse.reply` through the channel.
- Store or pass through `raw_payload` for audit/debugging.

Adapters should not:

- Classify business intent.
- Save reminders or vencimientos directly.
- Contain supplier, margin, price, or expiration rules.
- Depend on prompt internals.

## Kapso / WhatsApp Rules

- Keep Kapso code in `app/channels/whatsapp_kapso.py` or a dedicated package.
- Keep `/webhooks/kapso` thin.
- Treat WhatsApp media as attachments to be handled by providers.
- Add retries/idempotency for webhook processing when implementation reaches production level.

## Review Checklist

- Can the same core endpoint be called from CLI/Postman?
- Can this channel be removed without breaking modules?
- Is raw payload preserved?
- Are errors logged without leaking secrets?
- Are message acknowledgements separate from business execution?
