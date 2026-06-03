---
name: stock-ai-data-model
description: Data modeling workflow for Stock AI. Use when designing or reviewing Postgres/Supabase schemas, repositories, migrations, multi-tenant isolation with business_id, conversation logs, reminders, expiration batches, suppliers, products, supplier prices, margin rules, knowledge notes, or pgvector timing.
---

# Stock AI Data Model

## Overview

Use this skill to keep the data model operationally useful for supermarkets while staying simple enough for the MVP.

## Core Rules

- Every business-owned table must include `business_id`.
- Prefer English table and column names.
- Keep Spanish business meaning in docs/UI, not schema identifiers.
- Store conversation logs for debugging and audit.
- Do not add pgvector until `knowledge_notes` has real content.
- Do not create migrations before choosing Supabase client vs SQLAlchemy.

## MVP Tables

- `businesses`
- `users`
- `conversation_messages`
- `reminders`

## Vencimientos / Expiration Items

Model vencimientos as stock groups, not as a single product-level date.

Use `expiration_items` fields like:

- `business_id`
- `product_id`
- `product_name`
- `received_at`
- `quantity`
- `unit`
- `expires_at`
- `lot_code`
- `notify_days_before`
- `status`
- `notes`

`lot_code` is optional. The operationally important case is: 10 units entered on one day, 4 expire soon and 6 expire later. That should create two `expiration_items` records.

## Review Checklist

- Can the same WhatsApp user map to the right `business_id`?
- Can queries accidentally leak another tenant's data?
- Can quantities be partially sold/offered/discarded?
- Are dates timezone-aware where needed?
- Is the schema overfitting to invoices too early?
